"""Kikoeru 数据库增量整理服务（v2.6.26）

手动触发的「title 替换 + 评分异常修复」整理任务，替代 socket 监听方案：
- 起点语义：
  - since 为空串 → 从上次处理游标继续（data/kikoeru_db_cleanup_cursor.json）；
  - since = "0" → 整个数据库；
  - since = "RJxxxx" → 定位该作品，处理「不包括它、之后入库」的所有作品
    （t_work.id 自增等价入库顺序，取 id > 起点作品 id）。
- 两阶段执行：
  ① title 替换：全部目标逐个用 DLsite 官方 work_name 校验/覆盖 t_work.title；
  ② 评分修复：**仅评分异常**（rate_average_2dp 为 NULL / 0 / >= 5）的作品
     触发评分修复后台任务（复用 kikoeru_rating_fix_service，全链含关联版本
     日文原版优先与满分核验）；正常评分一律不碰。
- 游标持久化：任务完成后写 data/kikoeru_db_cleanup_cursor.json
  （{rjcode, work_id, finished_at, processed_count}），前端弹窗展示上次位置。
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config.settings import get_config
from .kikoeru_db_service import KikoeruDbError, get_kikoeru_db_service

logger = logging.getLogger(__name__)

_CURSOR_FILENAME = "kikoeru_db_cleanup_cursor.json"


class KikoeruDbCleanupService:
    """Kikoeru 数据库增量整理（title 替换 + 评分异常修复），单例。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._state: Optional[Dict[str, Any]] = None
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ 游标
    def _cursor_file(self) -> Path:
        # data 目录与 kikoeru_db_service 备份目录同源（DATA_PATH 环境变量）
        import os

        data_path = str(os.environ.get("DATA_PATH") or "").strip() or "/app/data"
        return Path(data_path) / _CURSOR_FILENAME

    def get_cursor(self) -> Dict[str, Any]:
        """上次处理游标（前端弹窗展示「上次处理到哪」）。"""
        cursor_file = self._cursor_file()
        try:
            if cursor_file.exists():
                data = json.loads(cursor_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            logger.warning("[KIKOERU-CLEANUP] 游标文件读取失败", exc_info=True)
        return {}

    def _save_cursor(self, payload: Dict[str, Any]) -> None:
        try:
            cursor_file = self._cursor_file()
            cursor_file.parent.mkdir(parents=True, exist_ok=True)
            cursor_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.warning("[KIKOERU-CLEANUP] 游标文件写入失败", exc_info=True)

    # ------------------------------------------------------------ 目标解析
    def _resolve_since(self, since: str) -> tuple[str, str]:
        """解析起点：返回 (since_created_at, 描述)。

        - 空串 → 上次游标的 created_at（从未处理过则全库）；
        - "0" → 全库（空串比较基准）；
        - "RJxxxx" → 定位该作品的 **created_at**（Kikoeru 的加入时间；
          t_work.id 是 DLsite 作品号数值，递增方向是发售顺序而非入库顺序）。
        处理范围：created_at 严格大于起点的所有作品。
        """
        since_clean = str(since or "").strip()
        if not since_clean:
            cursor = self.get_cursor()
            since_created = str(cursor.get("created_at") or "")
            if since_created:
                return since_created, f"上次处理位置 {cursor.get('rjcode') or since_created}"
            return "", "从未处理过，从数据库起始"
        if since_clean in ("0", "RJ0"):
            return "", "整个数据库"
        service = get_kikoeru_db_service()
        conn = service._connect(None, readonly=True)
        try:
            workno = since_clean.upper()
            if not workno.startswith("RJ"):
                workno = f"RJ{workno}"
            cursor = conn.execute(
                'SELECT id, dir, created_at FROM "t_work" WHERE dir LIKE ? ORDER BY created_at LIMIT 1',
                (f"%{workno}%",),
            ).fetchone()
            if not cursor:
                raise ValueError(f"起点作品 {workno} 在数据库中不存在，请确认 RJ 号")
            created_at = str(cursor["created_at"] or "")
            if not created_at:
                raise ValueError(f"起点作品 {workno} 缺少 created_at，无法按加入时间定位")
            return created_at, f"起点作品 {workno}（加入于 {created_at}）"
        finally:
            conn.close()

    def _collect_targets(self, since_created_at: str) -> List[Dict[str, Any]]:
        service = get_kikoeru_db_service()
        conn = service._connect(None, readonly=True)
        try:
            if since_created_at:
                rows = conn.execute(
                    'SELECT id, dir, title, created_at, rate_count, rate_average_2dp '
                    'FROM "t_work" WHERE created_at > ? ORDER BY created_at, id',
                    (since_created_at,),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT id, dir, title, created_at, rate_count, rate_average_2dp '
                    'FROM "t_work" ORDER BY created_at, id'
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _abnormal_rating_ids(targets: List[Dict[str, Any]]) -> List[Any]:
        """评分异常：rate_average_2dp 为 NULL / 0 / >= 5（用户要求：正常评分不碰）。"""
        abnormal = []
        for row in targets:
            avg = row.get("rate_average_2dp")
            if avg is None or float(avg) == 0 or float(avg) >= 5:
                abnormal.append(row["id"])
        return abnormal

    # ------------------------------------------------------------ 启动
    async def start(self, since: str) -> Dict[str, Any]:
        if self._state and self._state.get("running"):
            return {"started": False, "reason": "已有整理任务在运行"}
        since_created_at, since_desc = await asyncio.to_thread(self._resolve_since, since)
        targets = await asyncio.to_thread(self._collect_targets, since_created_at)
        abnormal_ids = self._abnormal_rating_ids(targets)
        state: Dict[str, Any] = {
            "running": True,
            "cancel": False,
            "phase": "title",
            "since_desc": since_desc,
            "total": len(targets),
            "title_done": 0,
            "title_total": len(targets),
            "title_updated": 0,
            "title_unchanged": 0,
            "title_missed": 0,
            "rating_total": len(abnormal_ids),
            "rating_phase": "pending",
            "results": [],
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
        }
        self._state = state
        self._task = asyncio.create_task(self._run_impl(state, targets, abnormal_ids, since_created_at))
        logger.info(
            "[KIKOERU-CLEANUP] 整理任务已启动: %s 目标=%s 评分异常=%s",
            since_desc, len(targets), len(abnormal_ids),
        )
        return {
            "started": True,
            "since_desc": since_desc,
            "total": len(targets),
            "abnormal_rating": len(abnormal_ids),
        }

    # ------------------------------------------------------------ 执行
    async def _run_impl(self, state: Dict[str, Any], targets: List[Dict[str, Any]],
                        abnormal_ids: List[Any], since_created_at: str) -> None:
        from .kikoeru_db_service import get_kikoeru_db_service
        from .kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

        service = get_kikoeru_db_service()
        last_row: Optional[Dict[str, Any]] = targets[-1] if targets else None
        try:
            # ---------- 阶段 0：写前备份（批量修改不可逆，失败即中止） ----------
            state["phase"] = "backup"
            try:
                backup_result = await asyncio.to_thread(service.create_backup, "snapshot")
                state["backup_file"] = str(backup_result.get("filename") or "")
                logger.info("[KIKOERU-CLEANUP] 写前备份完成: %s", state["backup_file"])
            except Exception as exc:
                state["running"] = False
                state["phase"] = "error"
                state["error"] = f"写前备份失败，已中止整理: {exc}"
                state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.error("[KIKOERU-CLEANUP] 写前备份失败，整理中止: %s", exc)
                return

            # ---------- 阶段 1：title = 文件夹名反解出的作品名 ----------
            # 不能用整条 dir：dir 是「重命名模板产物」（如 [RJ01630673][作品名]），
            # 需按模板逆过程只取 {work_name} 段。模板严格匹配失败时回退通用形态
            # 反解（[RJ..][名字] / [社团][RJ..][名字] / RJ 名字 等），
            # 反解不出的行保持原 title。
            from .kikoeru_folder_parser import parse_work_name_by_template, parse_work_name_from_dir

            rename_template = str(get_config().rename.template or "")
            state["title_total"] = len(targets)
            updates: List[tuple] = []
            for row in targets:
                dir_name = str(row["dir"] or "")
                parsed = parse_work_name_by_template(dir_name, rename_template)
                if not parsed.get("matched"):
                    parsed = parse_work_name_from_dir(dir_name)
                new_title = str(parsed.get("work_name") or "").strip() if parsed.get("matched") else ""
                current_title = str(row["title"] or "").strip()
                if not new_title:
                    state["title_missed"] += 1
                elif new_title == current_title:
                    state["title_unchanged"] += 1
                else:
                    updates.append((new_title, row["id"]))
                    state["title_updated"] += 1
                state["title_done"] += 1
            if updates:
                conn = await asyncio.to_thread(service._connect, None, readonly=False)
                try:
                    await asyncio.to_thread(
                        conn.executemany,
                        'UPDATE "t_work" SET title = ? WHERE id = ?',
                        updates,
                    )
                    await asyncio.to_thread(conn.commit)
                finally:
                    conn.close()
            logger.info(
                "[KIKOERU-CLEANUP] title 已按文件夹名反解更新: 更新 %s / 一致 %s / 未匹配 %s（目标 %s）",
                state["title_updated"], state["title_unchanged"], state["title_missed"], len(targets),
            )
            state["phase"] = "rating"

            # ---------- 阶段 2：评分异常修复（复用评分修复后台任务） ----------
            if abnormal_ids and not state["cancel"]:
                state["rating_phase"] = "running"
                rating_fix = get_kikoeru_rating_fix_service()
                rating_result = await rating_fix.start_run(ids=abnormal_ids)
                if rating_result.get("started"):
                    # 轮询等待评分修复完成（状态由 rating_fix 维护）
                    while state["rating_phase"] == "running":
                        await asyncio.sleep(2)
                        rating_status = rating_fix.get_run_status()
                        state["rating_done"] = int(rating_status.get("done") or 0)
                        state["rating_applied"] = int(rating_status.get("applied") or 0)
                        if not rating_status.get("running"):
                            state["rating_phase"] = "done"
                            break
                        if state["cancel"]:
                            rating_fix.request_cancel()
                else:
                    state["rating_phase"] = "skipped"
                    state["rating_skip_reason"] = str(rating_result.get("reason") or "")
            else:
                state["rating_phase"] = "none" if not abnormal_ids else "cancelled"

            state["phase"] = "done"
            state["running"] = False
            state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if last_row and not state["cancel"]:
                # 游标推进：记录本次处理的最大 created_at 作品（下次默认起点）
                candidates = self._resolve_workno_candidates(last_row["id"], str(last_row["dir"] or ""))
                last_created_at = str(last_row.get("created_at") or "")
                self._save_cursor({
                    "rjcode": candidates[0] if candidates else str(last_row["dir"] or ""),
                    "work_id": int(last_row["id"]),
                    "created_at": last_created_at,
                    "finished_at": state["finished_at"],
                    "processed_count": len(targets),
                })
                logger.info(
                    "[KIKOERU-CLEANUP] 整理完成: 游标推进到 %s（created_at=%s）title 更新 %s / 评分异常 %s",
                    candidates[0] if candidates else last_row["dir"],
                    last_created_at, state["title_updated"], len(abnormal_ids),
                )
            if state["cancel"]:
                logger.info("[KIKOERU-CLEANUP] 整理任务被取消，游标不推进")
        except Exception:
            state["running"] = False
            state["phase"] = "error"
            state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.error("[KIKOERU-CLEANUP] 整理任务异常", exc_info=True)

    def _resolve_workno_candidates(self, work_id: Any, dir_name: str) -> List[str]:
        """dir 名称解析 workno 候选（与评分修复服务同源逻辑，避免跨服务耦合私有方法）。"""
        candidates: List[str] = []
        text = str(dir_name or "")
        import re

        for match in re.finditer(r"(?:RJ|VJ)\d{6,10}", text, flags=re.IGNORECASE):
            code = match.group(0).upper()
            if code not in candidates:
                candidates.append(code)
        if not candidates:
            # id 兜底（t_work.id 的 VJ 偏移规则：>=2e12 为 VJ）
            try:
                numeric = int(work_id)
                candidates.append(f"VJ{numeric - 2000000000000:06d}" if numeric >= 2000000000000
                                  else f"RJ{numeric:06d}")
            except (TypeError, ValueError):
                pass
        return candidates

    # ------------------------------------------------------------ 状态与取消
    def get_status(self) -> Dict[str, Any]:
        if not self._state:
            return {"running": False, "phase": "idle", "title_done": 0, "title_total": 0,
                    "title_updated": 0, "rating_total": 0}
        return dict(self._state)

    def request_cancel(self) -> bool:
        if self._state and self._state.get("running"):
            self._state["cancel"] = True
            logger.info("[KIKOERU-CLEANUP] 收到取消请求")
            return True
        return False


_cleanup_service: Optional[KikoeruDbCleanupService] = None


def get_kikoeru_db_cleanup_service() -> KikoeruDbCleanupService:
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = KikoeruDbCleanupService()
    return _cleanup_service
