"""Kikoeru 数据库管理服务（v2.6）

直接管理远程 Kikoeru（闭源新版 0.6.x）的 SQLite 数据库文件：
- 只读分页查询（功能1 浏览）
- 带写前快照的 UNC/本地直写事务（功能1 编辑；写后 integrity_check 校验，失败回滚）
- 写入路由：t_work 的 title/circle_id 优先走 Kikoeru 官方 API（POST /api/edit/work/{id}），
  其余或 API 不可达时自动降级 UNC 直写
- 备份（activate/auto/manual/snapshot/pre-restore 五类）与恢复
- 功能2：dir 反解 work_name 后批量/单条套用标题

表权限：
- 可编辑：t_work / t_circle / t_tag / t_va / r_tag_work / r_va_work / t_review
- 只读：t_play_histroy / t_translate_task
- 隐藏（API 也拒绝）：t_user / knex_migrations

编码约定：方法内需要配置时显式 ``config = get_config()``。
"""
import logging
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config.settings import get_config
from .kikoeru_folder_parser import parse_work_name_by_template

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 表权限白名单
TABLE_EDITABLE = {"t_work", "t_circle", "t_tag", "t_va", "r_tag_work", "r_va_work", "t_review"}
TABLE_READONLY = {"t_play_histroy", "t_translate_task"}
TABLE_HIDDEN = {"t_user", "knex_migrations"}
TABLE_EXPECTED = TABLE_EDITABLE | TABLE_READONLY | TABLE_HIDDEN  # 共 11 张

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

# t_work 中可走 Kikoeru 官方编辑 API（POST /api/edit/work/{id}）的字段映射
# （t_work 列名 → API payload 字段名；API 为整体替换语义，调用方必须带全四项）
_API_EDITABLE_FIELDS = {"title": "title", "circle_id": "circle"}


class KikoeruDbError(Exception):
    """带 HTTP 语义的错误：status 用于 API 层直接映射响应码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class KikoeruDbService:
    """Kikoeru SQLite 数据库管理服务（无持久连接，方法级短连接）。"""

    # ------------------------------------------------------------------ 路径
    @staticmethod
    def _resolve_db_path() -> Path:
        config = get_config()
        raw = str(config.kikoeru_db.db_path or "").strip().strip('"')
        if not raw:
            raise KikoeruDbError("尚未配置 Kikoeru 数据库路径（db_path）", 400)
        path = Path(raw)
        if not path.is_file():
            raise KikoeruDbError(f"数据库文件不存在或不可达: {raw}", 404)
        return path

    @staticmethod
    def _resolve_backup_dir() -> Path:
        config = get_config()
        raw = str(config.kikoeru_db.backup_dir or "").strip()
        if raw:
            base = Path(raw)
        else:
            from ..config.settings import get_config_file_path

            data_path = str(os.environ.get("DATA_PATH") or "").strip()
            if data_path:
                base = Path(data_path) / "kikoeru_db_backups"
            else:
                config_path = Path(get_config_file_path()).resolve()
                data_dir = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
                base = data_dir / "kikoeru_db_backups"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise KikoeruDbError(f"备份目录不可用: {base} ({exc})", 500)
        return base

    # ------------------------------------------------------------------ 表权限
    @staticmethod
    def _validate_table(table: str, *, need: str = "read") -> str:
        name = str(table or "").strip()
        if not _IDENTIFIER_RE.match(name):
            raise KikoeruDbError(f"非法表名: {table!r}", 400)
        if name in TABLE_HIDDEN:
            raise KikoeruDbError("该表受保护，不可访问", 403)
        if name not in TABLE_EDITABLE and name not in TABLE_READONLY:
            raise KikoeruDbError(f"未知表: {name}", 404)
        if need in ("write",) and name not in TABLE_EDITABLE:
            raise KikoeruDbError(f"表 {name} 为只读表，不可修改", 403)
        return name

    @staticmethod
    def _table_mode(table: str) -> str:
        if table in TABLE_EDITABLE:
            return "editable"
        if table in TABLE_READONLY:
            return "readonly"
        return "hidden"

    # ------------------------------------------------------------------ 连接
    @staticmethod
    def _connect(db_path: Optional[Path] = None, *, readonly: bool = False,
                 busy_timeout_ms: int = 5000) -> sqlite3.Connection:
        path = db_path or KikoeruDbService._resolve_db_path()
        conn = sqlite3.connect(str(path), timeout=max(busy_timeout_ms / 1000, 1.0), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if readonly:
            # query_only 在 SQL 层禁写，比 mode=ro URI 更稳（规避 UNC/URI 兼容问题）
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _columns_of(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [
            {"name": r["name"], "type": (r["type"] or "").upper(), "notnull": bool(r["notnull"]),
             "default": r["dflt_value"], "pk": int(r["pk"] or 0)}
            for r in rows
        ]

    @staticmethod
    def _pk_columns(conn: sqlite3.Connection, table: str) -> List[str]:
        cols = KikoeruDbService._columns_of(conn, table)
        pks = [c for c in cols if c["pk"]]
        pks.sort(key=lambda c: c["pk"])
        return [c["name"] for c in pks] or ["rowid"]

    # ------------------------------------------------------------------ 备份/快照（sqlite3 backup API，WAL 安全）
    @staticmethod
    def create_backup(kind: str = "manual") -> Dict[str, Any]:
        """对当前数据库做一致性备份（sqlite3 backup API，可安全在 Kikoeru 运行时执行）。"""
        if kind not in ("activate", "auto", "manual", "pre-restore", "snapshot"):
            raise KikoeruDbError(f"非法备份类型: {kind}", 400)
        config = get_config()
        if not str(config.kikoeru_db.db_path or "").strip():
            raise KikoeruDbError("尚未配置 Kikoeru 数据库路径（db_path）", 400)
        backup_dir = KikoeruDbService._resolve_backup_dir()
        db_path = KikoeruDbService._resolve_db_path()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"{db_path.stem}_{stamp}_{kind}.sqlite3"
        # 先做抢锁探测（sqlite3 backup API 遇 BUSY 会无限重试，必须先快速失败）
        probe = KikoeruDbService._connect(db_path, readonly=False, busy_timeout_ms=2000)
        probe.isolation_level = None
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            raise KikoeruDbError(f"数据库被占用，无法备份（Kikoeru 可能在写入）: {exc}", 423)
        finally:
            probe.close()
        src = KikoeruDbService._connect(db_path, readonly=True)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        # activate 原始备份只保留 1 份：删除更早的 activate
        if kind == "activate":
            for old in sorted(backup_dir.glob(f"{db_path.stem}_*_activate.sqlite3"))[:-1]:
                try:
                    old.unlink()
                except OSError:
                    logger.warning("[KIKOERU-DB] 清理旧 activate 备份失败: %s", old)
        logger.info("[KIKOERU-DB] 备份完成 kind=%s -> %s", kind, target)
        return {"filename": target.name, "kind": kind, "size": target.stat().st_size,
                "created_at": datetime.fromtimestamp(target.stat().st_mtime).isoformat(timespec="seconds")}

    @staticmethod
    def list_backups(kind: Optional[str] = None) -> List[Dict[str, Any]]:
        backup_dir = KikoeruDbService._resolve_backup_dir()
        db_stem = KikoeruDbService._resolve_db_path().stem
        items: List[Dict[str, Any]] = []
        for f in backup_dir.glob("*.sqlite3"):
            m = re.match(rf"^{re.escape(db_stem)}_\d{{8}}_\d{{6}}_([a-z\-]+)\.sqlite3$", f.name)
            if not m:
                continue
            file_kind = m.group(1)
            if kind and file_kind != kind:
                continue
            stat = f.stat()
            items.append({"filename": f.name, "kind": file_kind, "size": stat.st_size,
                          "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
        items.sort(key=lambda x: x["created_at"], reverse=True)
        return items

    @staticmethod
    def cleanup_retention() -> Dict[str, int]:
        """按 retention 清理 auto（backup_retention）与 snapshot（snapshot_retention + 24h）。"""
        config = get_config()
        db_stem = KikoeruDbService._resolve_db_path().stem
        backup_dir = KikoeruDbService._resolve_backup_dir()
        removed = {"auto": 0, "snapshot": 0}

        autos = sorted(
            [f for f in backup_dir.glob(f"{db_stem}_*_auto.sqlite3")],
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        for old in autos[max(int(config.kikoeru_db.backup_retention or 7), 1):]:
            try:
                old.unlink()
                removed["auto"] += 1
            except OSError:
                pass

        now_ts = datetime.now().timestamp()
        max_keep = max(int(config.kikoeru_db.snapshot_retention or 20), 1)
        snaps = sorted(
            [f for f in backup_dir.glob(f"{db_stem}_*_snapshot.sqlite3")],
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        for idx, old in enumerate(snaps):
            expired = (now_ts - old.stat().st_mtime) > 24 * 3600
            if idx >= max_keep or expired:
                try:
                    old.unlink()
                    removed["snapshot"] += 1
                except OSError:
                    pass
        return removed

    @staticmethod
    def restore_backup(filename: str) -> Dict[str, Any]:
        """用指定备份整文件替换当前库。恢复前自动做 pre-restore 备份。"""
        backup_dir = KikoeruDbService._resolve_backup_dir()
        db_path = KikoeruDbService._resolve_db_path()

        safe_name = Path(str(filename or "")).name
        if not re.match(rf"^{re.escape(db_path.stem)}_\d{{8}}_\d{{6}}_[a-z\-]+\.sqlite3$", safe_name):
            raise KikoeruDbError(f"非法备份文件名: {filename}", 400)
        source = backup_dir / safe_name
        if not source.is_file():
            raise KikoeruDbError(f"备份文件不存在: {safe_name}", 404)

        # 恢复前先备份当前状态
        KikoeruDbService.create_backup("pre-restore")

        # 校验备份文件本身可用
        check = sqlite3.connect(str(source))
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()
            if not result or str(result[0]).lower() != "ok":
                raise KikoeruDbError(f"备份文件损坏（integrity_check={result[0] if result else 'n/a'}），已中止恢复", 500)
        finally:
            check.close()

        # 写入临时文件后原子替换；一并清掉旧 -wal/-shm，防止旧 WAL 污染新主库
        tmp = db_path.with_suffix(".sqlite3.restore_tmp")
        shutil.copy2(source, tmp)
        os.replace(tmp, db_path)
        for suffix in ("-wal", "-shm"):
            stale = Path(str(db_path) + suffix)
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                logger.warning("[KIKOERU-DB] 清理旧 %s 失败（建议确认 Kikoeru 状态）", stale.name)

        verify = KikoeruDbService._connect(readonly=True)
        try:
            row = verify.execute("PRAGMA integrity_check").fetchone()
            ok = bool(row) and str(row[0]).lower() == "ok"
        finally:
            verify.close()
        if not ok:
            raise KikoeruDbError("恢复后 integrity_check 未通过", 500)
        logger.info("[KIKOERU-DB] 恢复完成: %s -> %s", safe_name, db_path)
        return {"restored": safe_name, "integrity": "ok"}

    # ------------------------------------------------------------------ 只读查询
    def query_table(self, table: str, page: int = 1, size: int = 50,
                    search: str = "", sort: str = "") -> Dict[str, Any]:
        table = self._validate_table(table, need="read")
        page = max(int(page or 1), 1)
        size = min(max(int(size or 50), 1), 200)
        offset = (page - 1) * size

        conn = self._connect(readonly=True)
        try:
            columns = self._columns_of(conn, table)
            col_names = [c["name"] for c in columns]
            where_sql, params = "", []
            search = str(search or "").strip()
            if search:
                text_cols = [
                    c["name"] for c in columns
                    if not c["type"] or any(t in c["type"] for t in ("CHAR", "CLOB", "TEXT"))
                ]
                if text_cols:
                    where_sql = " WHERE " + " OR ".join(
                        [f'"{c}" LIKE ?' for c in text_cols]
                    )
                    like = f"%{search}%"
                    params = [like] * len(text_cols)

            order_sql = ""
            sort_field, _, sort_dir = str(sort or "").partition(":")
            if sort_field and sort_field in col_names:
                direction = "DESC" if sort_dir.strip().lower() == "desc" else "ASC"
                order_sql = f' ORDER BY "{sort_field}" {direction}'
            else:
                order_sql = " ORDER BY rowid"

            total = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"{where_sql}', params).fetchone()["n"]
            rows = conn.execute(
                f'SELECT * FROM "{table}"{where_sql}{order_sql} LIMIT ? OFFSET ?',
                [*params, size, offset],
            ).fetchall()
            return {
                "table": table,
                "mode": self._table_mode(table),
                "columns": columns,
                "rows": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "size": size,
            }
        finally:
            conn.close()

    def list_tables(self) -> List[Dict[str, Any]]:
        conn = self._connect(readonly=True)
        try:
            names = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
        finally:
            conn.close()
        result = []
        for name in sorted(names):
            mode = self._table_mode(name)
            if mode == "hidden":
                continue
            result.append({"name": name, "mode": mode})
        return result

    # ------------------------------------------------------------------ 写事务（UNC/本地直写，写前快照）
    def _write_with_snapshot(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """写前拍 snapshot 备份 → BEGIN IMMEDIATE 重试 → 写后 integrity_check 校验。

        fn(conn) 在 BEGIN IMMEDIATE 之后的同一连接/事务内执行，异常时 ROLLBACK。
        """
        import asyncio as _asyncio

        db_path = self._resolve_db_path()
        # 写前一致性快照（backup API 生成独立完整副本）
        snapshot = self.create_backup("snapshot")

        conn = self._connect(db_path, readonly=False, busy_timeout_ms=5000)
        conn.isolation_level = None  # 手动事务
        try:
            last_err: Optional[Exception] = None
            import time
            for attempt in range(5):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    last_err = None
                    break
                except sqlite3.OperationalError as exc:
                    last_err = exc
                    time.sleep(min(0.2 * (2 ** attempt), 2.0))
            if last_err is not None:
                raise KikoeruDbError(
                    f"数据库被占用（Kikoeru 可能在写入），请稍后重试或先停止 Kikoeru: {last_err}", 423
                )
            try:
                result = fn(conn)
                row = conn.execute("PRAGMA integrity_check").fetchone()
                if not row or str(row[0]).lower() != "ok":
                    raise KikoeruDbError(f"写入后完整性校验失败: {row[0] if row else 'n/a'}", 500)
                conn.execute("COMMIT")
                return result
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        except KikoeruDbError:
            raise
        except sqlite3.Error as exc:
            raise KikoeruDbError(f"数据库写入失败: {exc}", 500)
        finally:
            conn.close()
            try:
                self.cleanup_retention()
            except Exception:  # noqa: BLE001 - 清理失败不影响写入结果
                logger.debug("[KIKOERU-DB] 快照清理失败", exc_info=True)

    # ---- 行级写操作（含组合主键支持） ----
    @staticmethod
    def _normalize_bind_value(value: Any) -> Any:
        """sqlite3 不支持 dict/list 绑定：JSON 语义的复合值序列化为字符串兜底。"""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def _pk_where(self, conn: sqlite3.Connection, table: str, row_id: Any) -> tuple:
        pk_cols = self._pk_columns(conn, table)
        if isinstance(row_id, dict):
            missing = [c for c in pk_cols if c not in row_id]
            if missing:
                raise KikoeruDbError(f"缺少主键字段: {missing}", 400)
            values = [row_id[c] for c in pk_cols]
        else:
            if len(pk_cols) > 1:
                raise KikoeruDbError(f"表 {table} 为组合主键 {pk_cols}，id 需传对象", 400)
            values = [row_id]
        where = " AND ".join([f'"{c}" = ?' for c in pk_cols])
        return where, values

    def update_row(self, table: str, row_id: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
        table = self._validate_table(table, need="write")
        if not patch:
            raise KikoeruDbError("patch 为空", 400)

        def _do(conn: sqlite3.Connection) -> Dict[str, Any]:
            columns = {c["name"] for c in self._columns_of(conn, table)}
            unknown = [k for k in patch if k not in columns]
            if unknown:
                raise KikoeruDbError(f"未知字段: {unknown}", 400)
            where, values = self._pk_where(conn, table, row_id)
            set_sql = ", ".join([f'"{k}" = ?' for k in patch])
            bind_values = [self._normalize_bind_value(v) for v in patch.values()]
            cur = conn.execute(
                f'UPDATE "{table}" SET {set_sql} WHERE {where}',
                [*bind_values, *values],
            )
            if cur.rowcount == 0:
                raise KikoeruDbError("未找到目标行", 404)
            return {"table": table, "updated": cur.rowcount}

        return self._write_with_snapshot(_do)

    def insert_row(self, table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        table = self._validate_table(table, need="write")
        if not payload:
            raise KikoeruDbError("payload 为空", 400)

        def _do(conn: sqlite3.Connection) -> Dict[str, Any]:
            columns = {c["name"] for c in self._columns_of(conn, table)}
            unknown = [k for k in payload if k not in columns]
            if unknown:
                raise KikoeruDbError(f"未知字段: {unknown}", 400)
            cols = ", ".join([f'"{k}"' for k in payload])
            marks = ", ".join(["?"] * len(payload))
            cur = conn.execute(
                f'INSERT INTO "{table}" ({cols}) VALUES ({marks})',
                [self._normalize_bind_value(v) for v in payload.values()],
            )
            return {"table": table, "lastrowid": cur.lastrowid, "inserted": cur.rowcount}

        return self._write_with_snapshot(_do)

    def delete_row(self, table: str, row_id: Any) -> Dict[str, Any]:
        table = self._validate_table(table, need="write")

        def _do(conn: sqlite3.Connection) -> Dict[str, Any]:
            where, values = self._pk_where(conn, table, row_id)
            cur = conn.execute(f'DELETE FROM "{table}" WHERE {where}', values)
            if cur.rowcount == 0:
                raise KikoeruDbError("未找到目标行", 404)
            return {"table": table, "deleted": cur.rowcount}

        return self._write_with_snapshot(_do)

    # ------------------------------------------------------------------ 功能2：套用文件命名
    def _parse_dir_for_rename(self, dir_name: str) -> Dict[str, Any]:
        """按用户当前重命名模板反解文件夹名（唯一可信路径）。

        文件夹名不符合模板结构时直接 skipped——绝不靠启发式猜标题，
        避免把「RJ号 [社团]【副标题】正标题」里的社团名当成标题。
        """
        config = get_config()
        return parse_work_name_by_template(dir_name, config.rename.template)

    def preview_rename(self, ids: Optional[List[Any]] = None) -> Dict[str, Any]:
        conn = self._connect(readonly=True)
        try:
            if ids:
                marks = ",".join(["?"] * len(ids))
                rows = conn.execute(
                    f'SELECT id, dir, title FROM "t_work" WHERE id IN ({marks})', list(ids)
                ).fetchall()
            else:
                rows = conn.execute('SELECT id, dir, title FROM "t_work"').fetchall()
        finally:
            conn.close()

        items = []
        for r in rows:
            parsed = self._parse_dir_for_rename(r["dir"])
            changed = bool(parsed["matched"] and parsed["work_name"] and parsed["work_name"] != r["title"])
            items.append({
                "id": r["id"],
                "dir": r["dir"],
                "old_title": r["title"],
                "new_title": parsed["work_name"],
                "rjcode": parsed.get("rjcode"),
                "changed": changed,
                "skipped": not parsed["matched"] or not parsed["work_name"],
                "reason": parsed.get("reason", ""),
            })
        return {"total": len(items), "changed": sum(1 for i in items if i["changed"]),
                "skipped": sum(1 for i in items if i["skipped"]), "items": items}

    def apply_rename(self, ids: Optional[List[Any]] = None) -> Dict[str, Any]:
        """批量套用：单事务内 UPDATE title 且 is_custom_meta=1（防 Kikoeru 重扫描覆盖）。"""
        preview = self.preview_rename(ids)
        targets = [i for i in preview["items"] if i["changed"]]

        def _do(conn: sqlite3.Connection) -> Dict[str, Any]:
            applied = 0
            for item in targets:
                conn.execute(
                    'UPDATE "t_work" SET "title" = ?, "is_custom_meta" = 1 WHERE "id" = ?',
                    (item["new_title"], item["id"]),
                )
                applied += 1
            return {"applied": applied, "skipped": preview["skipped"],
                    "total": preview["total"]}

        result = self._write_with_snapshot(_do)
        logger.info("[KIKOERU-DB] 套用文件命名完成: applied=%s skipped=%s", result["applied"], result["skipped"])
        return result

    def apply_rename_single(self, work_id: Any) -> bool:
        """单条套用（功能3 复用）：仅在模板反解出 work_name 时更新。"""
        conn = self._connect(readonly=True)
        try:
            row = conn.execute('SELECT id, dir, title FROM "t_work" WHERE id = ?', (work_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return False
        parsed = self._parse_dir_for_rename(row["dir"])
        if not (parsed["matched"] and parsed["work_name"]) or parsed["work_name"] == row["title"]:
            return False
        self.update_row("t_work", work_id, {"title": parsed["work_name"], "is_custom_meta": 1})
        return True

    # ------------------------------------------------------------------ 诊断
    def diagnose(self) -> Dict[str, Any]:
        """四步检测：路径可达 → 结构+完整性 → 锁可用 → 副本试写。"""
        steps: List[Dict[str, Any]] = []

        def _step(name: str, ok: bool, detail: str) -> None:
            steps.append({"step": name, "ok": ok, "detail": detail})

        # ① 路径可达
        config = get_config()
        raw = str(config.kikoeru_db.db_path or "").strip().strip('"')
        db_path: Optional[Path] = None
        if not raw:
            _step("路径可达", False, "未配置 db_path")
        else:
            path = Path(raw)
            if path.is_file():
                _step("路径可达", True, str(path))
                db_path = path
            else:
                _step("路径可达", False, f"文件不存在或不可达: {raw}")

        if db_path is None:
            return {"ok": False, "steps": steps}

        # ② 结构 + 完整性
        try:
            conn = self._connect(db_path, readonly=True)
            try:
                names = {r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                missing = sorted(TABLE_EXPECTED - names)
                row = conn.execute("PRAGMA integrity_check").fetchone()
                integrity = str(row[0]).lower() if row else "unknown"
                if missing:
                    _step("结构匹配", False, f"缺少表: {missing}（可能为旧版或异常库）")
                else:
                    _step("结构匹配", True, f"11 张表齐全，integrity_check={integrity}")
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            _step("结构匹配", False, f"读取失败: {exc}")

        # ③ 锁可用（BEGIN IMMEDIATE 后立即 ROLLBACK，不动数据）
        try:
            conn = self._connect(db_path, readonly=False, busy_timeout_ms=2000)
            try:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
                _step("锁可用", True, "BEGIN IMMEDIATE 抢锁成功（当前无写入方占用）")
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            _step("锁可用", False, f"数据库被占用（Kikoeru 可能在扫描/写入）: {exc}")
        except Exception as exc:  # noqa: BLE001
            _step("锁可用", False, f"未知错误: {exc}")

        # ④ 写入能力（拷贝副本到备份目录试写，验证文件系统写权限）
        # 锁不可用时跳过（backup API 遇 BUSY 会无限重试，且占锁状态下试写无意义）
        if not steps[-1]["ok"] and steps[-1]["step"] == "锁可用":
            _step("写入能力", False, "已跳过：数据库被占用，请先释放写锁（如停止 Kikoeru 扫描）")
        else:
            try:
                backup_dir = KikoeruDbService._resolve_backup_dir()
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")
                probe = backup_dir / f"{db_path.stem}_diagnose_{stamp}.sqlite3"
                src = self._connect(db_path, readonly=True)
                try:
                    dst = sqlite3.connect(str(probe))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                try:
                    test = sqlite3.connect(str(probe))
                    try:
                        test.execute("CREATE TABLE _km_diagnose_probe (v TEXT)")
                        test.execute("INSERT INTO _km_diagnose_probe (v) VALUES ('ok')")
                        got = test.execute("SELECT v FROM _km_diagnose_probe").fetchone()[0]
                        test.execute("DROP TABLE _km_diagnose_probe")
                        test.commit()
                        _step("写入能力", got == "ok", f"副本试写成功: {probe.name}")
                    finally:
                        test.close()
                finally:
                    try:
                        probe.unlink()
                    except OSError:
                        pass
            except Exception as exc:  # noqa: BLE001
                _step("写入能力", False, f"副本试写失败（检查写权限/只读挂载）: {exc}")

        return {"ok": all(s["ok"] for s in steps), "db_path": str(db_path), "steps": steps}


_service: Optional[KikoeruDbService] = None


def get_kikoeru_db_service() -> KikoeruDbService:
    global _service
    if _service is None:
        _service = KikoeruDbService()
    return _service
