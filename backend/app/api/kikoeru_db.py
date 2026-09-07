"""Kikoeru 数据库管理 API（v2.6）

前缀 /api/kikoeru-db。除 tables/diagnose 外全部要求 config.kikoeru_db.enabled。
写操作路由：t_work 的 title/circle_id 优先走 Kikoeru 官方 API（整体替换语义，
必须带全 title/tags/vas/circle 四项），其余或 API 不可达时自动降级 UNC 直写。
"""
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi import APIRouter, Body, HTTPException

from ..config.settings import get_config
from ..core.kikoeru_db_service import (
    KikoeruDbError,
    get_kikoeru_db_service,
)
from ..core.kikoeru_duplicate_service import get_kikoeru_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kikoeru-db", tags=["kikoeru-db"])


def _service():
    return get_kikoeru_db_service()


def _require_enabled() -> None:
    config = get_config()
    if not config.kikoeru_db.enabled:
        raise HTTPException(status_code=403, detail="Kikoeru 数据库管理功能未启用")


def _raise_or(exc: Exception):
    if isinstance(exc, KikoeruDbError):
        raise HTTPException(status_code=exc.status, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------- API 写入通道
def _read_work_relations(work_id: Any) -> Optional[Dict[str, Any]]:
    """读取作品的当前 title/circle/tags/vas，供 Kikoeru 编辑 API 整体替换时带全。"""
    svc = _service()
    try:
        conn = svc._connect(readonly=True)
    except KikoeruDbError:
        return None
    try:
        row = conn.execute(
            'SELECT id, title, circle_id FROM "t_work" WHERE id = ?', (work_id,)
        ).fetchone()
        if not row:
            return None
        circle_row = conn.execute(
            'SELECT name FROM "t_circle" WHERE id = ?', (row["circle_id"],)
        ).fetchone()
        tag_rows = conn.execute(
            'SELECT t.name FROM "t_tag" t JOIN "r_tag_work" r ON r.tag_id = t.id '
            'WHERE r.work_id = ?',
            (work_id,),
        ).fetchall()
        va_rows = conn.execute(
            'SELECT v.name FROM "t_va" v JOIN "r_va_work" r ON r.va_id = v.id '
            'WHERE r.work_id = ?',
            (work_id,),
        ).fetchall()
        return {
            "title": row["title"],
            "circle_id": row["circle_id"],
            "circle_name": circle_row["name"] if circle_row else "",
            "tags": [r["name"] for r in tag_rows],
            "vas": [r["name"] for r in va_rows],
        }
    except Exception:
        logger.warning("[KIKOERU-DB] 读取作品关联失败（API 写入通道降级）", exc_info=True)
        return None
    finally:
        conn.close()


async def _api_edit_work(work_id: Any, patch: Dict[str, Any]) -> bool:
    """Kikoeru 官方编辑 API（POST /api/edit/work/{id}）。

    payload 为整体替换语义：title/tags/vas/circle 必须带全（未修改的项传当前值）。
    成功返回 True；任何失败返回 False（调用方降级 UNC）。
    """
    relations = _read_work_relations(work_id)
    if relations is None:
        return False

    new_title = str(patch.get("title", relations["title"]) or "")
    new_circle_name = relations["circle_name"]
    if "circle_id" in patch and patch["circle_id"] not in (None, relations["circle_id"]):
        svc = _service()
        try:
            conn = svc._connect(readonly=True)
            try:
                row = conn.execute(
                    'SELECT name FROM "t_circle" WHERE id = ?', (patch["circle_id"],)
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return False  # 目标社团不存在 → 降级 UNC
            new_circle_name = row["name"]
        except Exception:
            return False

    payload = {
        "title": new_title,
        "tags": relations["tags"],
        "vas": relations["vas"],
        "circle": new_circle_name,
    }

    try:
        kikoeru = get_kikoeru_service()
        if not await kikoeru._ensure_valid_token():
            return False
        session = await kikoeru._get_session()
        url = f"{str(kikoeru.config.server_url or '').rstrip('/')}/api/edit/work/{work_id}"
        async with session.post(
            url,
            json=payload,
            headers=kikoeru._get_headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if 200 <= resp.status < 300:
                logger.info("[KIKOERU-DB] API 编辑成功 work_id=%s（channel=api）", work_id)
                return True
            body = await resp.text()
            logger.warning("[KIKOERU-DB] API 编辑失败 status=%s body=%s（降级 UNC）", resp.status, body[:200])
            return False
    except Exception:
        logger.warning("[KIKOERU-DB] API 编辑异常（降级 UNC）", exc_info=True)
        return False


async def _route_update(table: str, row_id: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
    """写入路由：t_work 的 title/circle_id 走 API 优先，其余/失败走 UNC 快照写。"""
    svc = _service()
    if table == "t_work" and set(patch) <= {"title", "circle_id"}:
        if await _api_edit_work(row_id, patch):
            # API 通道由 Kikoeru 落库（is_custom_meta 由 Kikoeru 维护）
            return {"table": table, "channel": "api", "updated": 1}
        # API 不可达/失败 → 降级 UNC，title 变更时顺带置 is_custom_meta=1
        unc_patch = dict(patch)
        if "title" in unc_patch:
            unc_patch["is_custom_meta"] = 1
        result = await _run_sync_write(svc.update_row, table, row_id, unc_patch)
        result["channel"] = "unc"
        return result
    result = await _run_sync_write(svc.update_row, table, row_id, patch)
    result.setdefault("channel", "unc")
    return result


async def _run_sync_write(method, *args) -> Dict[str, Any]:
    import asyncio

    try:
        return await asyncio.to_thread(method, *args)
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------- 端点
@router.get("/tables")
async def list_tables():
    _require_enabled()
    try:
        return {"tables": _service().list_tables()}
    except Exception as exc:
        _raise_or(exc)


@router.get("/backups")
async def list_backups(kind: Optional[str] = None):
    _require_enabled()
    try:
        import asyncio

        items = await asyncio.to_thread(_service().list_backups, kind)
        return {"backups": items}
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/backup")
async def create_backup(body: Dict[str, Any] = Body(default={})):
    _require_enabled()
    kind = str((body or {}).get("kind") or "manual")
    if kind not in ("manual",):
        raise HTTPException(status_code=400, detail="此端点仅支持 kind=manual（auto/activate 由系统自动触发）")
    import asyncio

    try:
        result = await asyncio.to_thread(_service().create_backup, kind)
        return result
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/backup/restore")
async def restore_backup(body: Dict[str, Any] = Body(...)):
    _require_enabled()
    filename = str((body or {}).get("filename") or "")
    import asyncio

    try:
        result = await asyncio.to_thread(_service().restore_backup, filename)
        return result
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/snapshots")
async def list_snapshots():
    _require_enabled()
    try:
        import asyncio

        items = await asyncio.to_thread(_service().list_backups, "snapshot")
        return {"snapshots": items}
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/rename/preview")
async def rename_preview(body: Dict[str, Any] = Body(default={})):
    _require_enabled()
    ids = (body or {}).get("ids")
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids 需为数组或省略（省略=全量）")
    mode = str((body or {}).get("mode") or "template")
    regex = str((body or {}).get("regex") or "")
    if mode not in ("template", "regex"):
        raise HTTPException(status_code=400, detail="mode 仅支持 template / regex")
    import asyncio

    try:
        return await asyncio.to_thread(_service().preview_rename, ids, mode, regex)
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/rename/apply")
async def rename_apply(body: Dict[str, Any] = Body(default={})):
    _require_enabled()
    ids = (body or {}).get("ids")
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids 需为数组或省略（省略=全量）")
    mode = str((body or {}).get("mode") or "template")
    regex = str((body or {}).get("regex") or "")
    if mode not in ("template", "regex"):
        raise HTTPException(status_code=400, detail="mode 仅支持 template / regex")
    import asyncio

    try:
        return await asyncio.to_thread(_service().apply_rename, ids, mode, regex)
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


# ---------------------------------------------------------------- 评分修复（v2.6，两段式：名单零请求 / 执行才抓取）
@router.post("/rating-fix/preview")
async def rating_fix_preview(body: Dict[str, Any] = Body(default={})):
    """第一步（轻量）：纯 SQL 筛选 0 分/满分名单，零 DLsite 请求，秒出。"""
    _require_enabled()
    ids = (body or {}).get("ids")
    limit = int((body or {}).get("limit") or 300)
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids 需为数组或省略（省略=全库 0 分+满分作品）")
    from ..core.kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

    service = get_kikoeru_rating_fix_service()
    try:
        return await service.list_fix_targets(ids=ids, limit=limit)
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"评分修复名单获取失败: {exc}")


@router.post("/rating-fix/run")
async def rating_fix_run(body: Dict[str, Any] = Body(default={})):
    """第二步（用户确认后）：启动后台任务，逐个抓取 DLsite 并分批写库，带实时进度。"""
    _require_enabled()
    ids = (body or {}).get("ids")
    limit = int((body or {}).get("limit") or 300)
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids 需为数组或省略")
    from ..core.kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

    service = get_kikoeru_rating_fix_service()
    try:
        return await service.start_run(ids=ids, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"评分修复任务启动失败: {exc}")


@router.get("/rating-fix/run/status")
async def rating_fix_status():
    """后台修复任务实时进度：done/total、applied/none/error 计数与逐行结果。"""
    _require_enabled()
    from ..core.kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

    return get_kikoeru_rating_fix_service().get_run_status()


@router.post("/rating-fix/run/cancel")
async def rating_fix_cancel():
    _require_enabled()
    from ..core.kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

    return {"cancelled": get_kikoeru_rating_fix_service().request_cancel()}


@router.post("/diagnose")
async def diagnose():
    _require_enabled()
    import asyncio

    try:
        return await asyncio.to_thread(_service().diagnose)
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/scan/status")
async def scan_status():
    _require_enabled()
    from ..core.kikoeru_scan_listener import get_kikoeru_scan_listener

    listener = get_kikoeru_scan_listener()
    config = get_config().kikoeru_db
    return {
        "listening": listener.is_running(),
        "connected": listener.is_connected(),
        "scan_listen_enabled": bool(config.scan_listen_enabled),
        "checkpoint": config.scan_checkpoint,
        "last_scan_finished_at": config.last_scan_finished_at,
        "poll_interval_minutes": config.scan_poll_interval_minutes,
    }


@router.post("/scan/start")
async def scan_start():
    _require_enabled()
    from ..core.kikoeru_scan_listener import get_kikoeru_scan_listener

    listener = get_kikoeru_scan_listener()
    await listener.start()
    return {"success": True, "listening": listener.is_running()}


@router.post("/scan/stop")
async def scan_stop():
    _require_enabled()
    from ..core.kikoeru_scan_listener import get_kikoeru_scan_listener

    listener = get_kikoeru_scan_listener()
    await listener.stop()
    return {"success": True, "listening": False}


@router.get("/{table}/rows")
async def query_rows(table: str, page: int = 1, size: int = 50,
                     search: str = "", sort: str = ""):
    _require_enabled()
    import asyncio

    try:
        return await asyncio.to_thread(
            _service().query_table, table, page, size, search, sort
        )
    except KikoeruDbError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.put("/{table}/row")
async def update_row(table: str, body: Dict[str, Any] = Body(...)):
    _require_enabled()
    row_id = (body or {}).get("id")
    patch = (body or {}).get("patch")
    if row_id is None or not isinstance(patch, dict) or not patch:
        raise HTTPException(status_code=400, detail="需要 {id, patch}")
    return await _route_update(table, row_id, patch)


@router.post("/{table}/row")
async def insert_row(table: str, body: Dict[str, Any] = Body(...)):
    _require_enabled()
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="需要行字段对象")
    return await _run_sync_write(_service().insert_row, table, body)


@router.delete("/{table}/row")
async def delete_row(table: str, body: Dict[str, Any] = Body(...)):
    _require_enabled()
    row_id = (body or {}).get("id")
    if row_id is None:
        raise HTTPException(status_code=400, detail="需要 {id}（组合主键传对象）")
    return await _run_sync_write(_service().delete_row, table, row_id)
