"""Kikoeru 扫描监听器（v2.6 功能3）

以 socket.io 客户端身份连接新版 Kikoeru（0.6.x）的管理事件流：
- SCAN_TASKS / SCAN_FAILED_TASKS：仅记日志（个别任务失败**不影响**触发）
- SCAN_FINISHED：扫描完成 → 对 created_at 晚于检查点的新增作品自动套用文件命名（功能2）

兼容性：新版 Kikoeru 前端捆绑 socket.io-client v2（EIO=3 协议），
因此钉版 python-socketio 4.x + python-engineio 3.x；JWT 通过连接查询串
`?token=`（socketio-jwt-auth 默认读取位置）与 `Authorization: Bearer` 双通道携带。

断线兜底：socket 不可用时按 scan_poll_interval_minutes 轮询数据库增量，
重连/恢复后自动补处理，检查点推进保证不重不漏。

首次运行只建立基线（checkpoint=当前最大 created_at），不会把存量库整批改名。
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from ..config.settings import get_config, save_config
from .kikoeru_db_service import KikoeruDbError, get_kikoeru_db_service

logger = logging.getLogger(__name__)

_DB_TIME_FMT = "%Y-%m-%d %H:%M:%S"
_EPOCH = "1970-01-01 00:00:00"


class KikoeruScanListener:
    """Kikoeru 扫描事件监听 + 轮询兜底（单例）"""

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
        self._task: Optional[asyncio.Task] = None
        self._sio = None
        self._connected = False
        self._had_connection = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._processing = False

    # ------------------------------------------------------------ 生命周期
    def _should_run(self) -> bool:
        config = get_config()
        kb = config.kikoeru_db
        ks = config.kikoeru_server
        return bool(
            kb.enabled
            and kb.scan_listen_enabled
            and ks.enabled
            and str(ks.server_url or "").strip()
        )

    async def start(self):
        if self._task is not None and not self._task.done():
            logger.warning("[KIKOERU-SCAN] 监听已在运行")
            return
        if not self._should_run():
            logger.info("[KIKOERU-SCAN] 未满足启动条件（kikoeru_db.enabled / scan_listen_enabled / kikoeru_server），不启动")
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="kikoeru-scan-listener")
        logger.info("[KIKOERU-SCAN] 监听任务已启动")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._connected = False
        logger.info("[KIKOERU-SCAN] 监听已停止")

    async def restart(self):
        await self.stop()
        await self.start()

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------ 主循环
    async def _run(self):
        backoff = 5
        # 负偏移：启动后第一次循环立即执行增量处理（"重连/恢复后自动补处理"
        # 的设计意图），否则要等 poll_interval（默认 30 分钟）才第一次跑，
        # 用户扫描后立刻看会以为功能没生效
        last_poll = -10**9
        while True:
            if not self._should_run():
                self._connected = False
                await asyncio.sleep(30)
                continue

            self._had_connection = False
            # 阻塞直到断线（connect + wait 跑在独立线程）
            await self._try_connect()
            logger.info("[KIKOERU-SCAN] socket 断开，转入轮询兜底")

            if not self._had_connection:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
            else:
                backoff = 5

            # 断线期间轮询兜底：按检查点增量处理
            now = asyncio.get_event_loop().time()
            poll_interval = max(int(get_config().kikoeru_db.scan_poll_interval_minutes or 30), 1) * 60
            if now - last_poll >= poll_interval:
                last_poll = now
                try:
                    await self._process_new_works(trigger="poll_fallback")
                except Exception:
                    logger.warning("[KIKOERU-SCAN] 轮询兜底处理失败", exc_info=True)

    # ------------------------------------------------------------ socket 连接
    async def _try_connect(self) -> bool:
        """建立 socket.io(EIO=3) 连接；成功后阻塞等待断线。返回是否连上过。"""
        import socketio  # python-socketio 4.x（EIO=3，已对真实 0.6.15 实例验证可连接）

        from .kikoeru_duplicate_service import get_kikoeru_service

        try:
            kikoeru = get_kikoeru_service()
            if not await kikoeru._ensure_valid_token():
                logger.warning("[KIKOERU-SCAN] 无法获取 Kikoeru JWT，本轮不连接")
                return False
            server = str(kikoeru.config.server_url or "").rstrip("/")
            token = kikoeru.config.api_token
        except Exception:
            logger.warning("[KIKOERU-SCAN] 获取 Kikoeru 连接信息失败", exc_info=True)
            return False

        sio = socketio.Client(logger=False, engineio_logger=False)
        self._sio = sio

        @sio.on("connect")
        def _on_connect():  # noqa: ANN001
            self._connected = True
            self._had_connection = True
            logger.info("[KIKOERU-SCAN] 已连接 Kikoeru 事件流: %s", server)

        @sio.on("disconnect")
        def _on_disconnect():  # noqa: ANN001
            self._connected = False
            logger.info("[KIKOERU-SCAN] 连接已断开")

        @sio.on("SCAN_TASKS")
        def _on_scan_tasks(data):  # noqa: ANN001
            try:
                tasks = (data or {}).get("tasks") or []
                logger.info("[KIKOERU-SCAN] Kikoeru 开始扫描，任务数=%s", len(tasks))
            except Exception:
                logger.debug("[KIKOERU-SCAN] SCAN_TASKS payload 解析失败", exc_info=True)

        @sio.on("SCAN_FAILED_TASKS")
        def _on_scan_failed(data):  # noqa: ANN001
            # 个别任务失败只记日志；SCAN_FINISHED 仍会触发，不影响自动套用
            try:
                failed = (data or {}).get("failedTasks") or []
                logger.warning("[KIKOERU-SCAN] 扫描存在失败任务数=%s（不阻断自动套用）", len(failed))
            except Exception:
                logger.debug("[KIKOERU-SCAN] SCAN_FAILED_TASKS payload 解析失败", exc_info=True)

        @sio.on("SCAN_FINISHED")
        def _on_scan_finished(data):  # noqa: ANN001
            logger.info("[KIKOERU-SCAN] 收到 SCAN_FINISHED，开始增量套用文件命名")
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._process_new_works("scan_finished"), self._loop)

        def _socket_blocking():
            try:
                # EIO=3：token 走查询串（socketio-jwt-auth 默认从 handshake.query.token 取，
                # 已对真实 0.6.15 实例验证通过），同时带 Authorization 头兜底。
                # python-socketio 4.x 的 connect 签名无 wait_timeout/extra args。
                sio.connect(
                    f"{server}/?token={token}",
                    headers={"Authorization": f"Bearer {token}"},
                    transports=["websocket", "polling"],
                )
                sio.wait()
            except Exception as exc:
                # 连接失败原因升级为 WARNING：现场从未成功连上过（9/7 起日志无一次"已连接"），
                # 一直在 5→300s 退避重试，需要 exc 内容定位（JWT/协议/网络）
                logger.warning("[KIKOERU-SCAN] socket 连接/等待结束，退避后重试: %s", exc)
            finally:
                self._connected = False
                try:
                    if sio.connected:
                        sio.disconnect()
                except Exception:  # noqa: BLE001
                    pass

        try:
            await asyncio.to_thread(_socket_blocking)
        except Exception:
            logger.warning("[KIKOERU-SCAN] socket 线程异常退出", exc_info=True)
        return self._had_connection

    # ------------------------------------------------------------ 增量处理
    async def _process_new_works(self, trigger: str) -> dict:
        if self._processing:
            logger.info("[KIKOERU-SCAN] 上一次增量处理尚未结束，跳过本次触发（%s）", trigger)
            return {"processed": 0, "busy": True}
        self._processing = True
        try:
            config = get_config().kikoeru_db
            service = get_kikoeru_db_service()
            try:
                conn = await asyncio.to_thread(service._connect, None, readonly=True)
            except KikoeruDbError as exc:
                logger.warning("[KIKOERU-SCAN] 数据库不可用，无法增量处理: %s", exc)
                return {"processed": 0, "error": str(exc)}

            checkpoint = str(config.scan_checkpoint or "").strip()
            try:
                if not checkpoint:
                    # 首次运行：只建立基线，绝不批量改动存量库
                    cursor = await asyncio.to_thread(
                        conn.execute, 'SELECT MAX(created_at) AS m FROM "t_work"'
                    )
                    max_row = await asyncio.to_thread(cursor.fetchone)
                    baseline = (max_row["m"] if max_row and max_row["m"] else None) or \
                        datetime.now().strftime(_DB_TIME_FMT)
                    self._save_checkpoint(str(baseline))
                    logger.info("[KIKOERU-SCAN] 首次运行，建立基线检查点: %s", baseline)
                    return {"processed": 0, "baseline": str(baseline)}

                cursor = await asyncio.to_thread(
                    conn.execute,
                    'SELECT id, dir, title, created_at FROM "t_work" WHERE created_at > ?',
                    (checkpoint,),
                )
                rows = await asyncio.to_thread(cursor.fetchall)
            finally:
                conn.close()

            if not rows:
                self._mark_scan_finished(trigger)
                # 空结果也要留日志：让用户能区分「功能没跑」和「没有新内容」
                logger.info(
                    "[KIKOERU-SCAN] 增量处理完成（%s）: 新增=0 checkpoint=%s（无新内容）",
                    trigger, checkpoint,
                )
                return {"processed": 0}

            applied, failed = 0, 0
            new_ids = [r["id"] for r in rows]
            for r in rows:
                try:
                    if await asyncio.to_thread(service.apply_rename_single, r["id"]):
                        applied += 1
                except Exception:
                    failed += 1
                    logger.warning("[KIKOERU-SCAN] 处理作品失败 id=%s", r["id"], exc_info=True)

            # title 重构 + 评分修复（监控链路补全：原先只做文件命名，
            # 新增内容的 title/评分从未做重构校验）
            title_result = {"updated": 0, "unchanged": 0, "missed": 0}
            rating_started = False
            rating_reason = ""
            try:
                title_result = await self._refresh_titles_for_new_works(rows)
            except Exception:
                logger.warning("[KIKOERU-SCAN] title 重构整体失败", exc_info=True)
            try:
                from .kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

                rating_result = await get_kikoeru_rating_fix_service().start_run(ids=new_ids)
                rating_started = bool(rating_result.get("started"))
                rating_reason = str(rating_result.get("reason") or "")
                if not rating_started:
                    logger.info("[KIKOERU-SCAN] 评分修复未启动: %s", rating_reason)
            except Exception:
                logger.warning("[KIKOERU-SCAN] 评分修复任务启动失败", exc_info=True)
                rating_reason = "启动异常"

            new_checkpoint = max(
                [str(r["created_at"]) for r in rows if r["created_at"]] + [checkpoint]
            )
            self._save_checkpoint(new_checkpoint)
            self._mark_scan_finished(trigger)
            logger.info(
                "[KIKOERU-SCAN] 增量处理完成（%s）: 新增=%s rename_applied=%s rename_failed=%s "
                "title_updated=%s title_missed=%s rating_started=%s%s checkpoint=%s",
                trigger, len(rows), applied, failed,
                title_result.get("updated"), title_result.get("missed"),
                rating_started, (f"（{rating_reason}）" if rating_reason else ""), new_checkpoint,
            )
            return {
                "processed": len(rows), "applied": applied, "failed": failed,
                "title_updated": title_result.get("updated"),
                "rating_started": rating_started,
            }
        finally:
            self._processing = False

    # ------------------------------------------------------------ title 重构
    async def _refresh_titles_for_new_works(self, rows: list) -> dict:
        """对新作品用 DLsite 官方 title 校验/重构数据库 title（title 杂乱痛点）。

        逐作品：dir 解析 workno 候选（复用评分修复的解析）→ get_work_info 取
        官方 work_name → 非空且与当前 title 不同则 UPDATE t_work.title。
        """
        from .dlsite_service import get_dlsite_service
        from .kikoeru_rating_fix_service import get_kikoeru_rating_fix_service

        rating_fix = get_kikoeru_rating_fix_service()
        dlsite = get_dlsite_service()
        service = get_kikoeru_db_service()
        updated, unchanged, missed = 0, 0, 0
        conn = await asyncio.to_thread(service._connect, None, readonly=False)
        try:
            for r in rows:
                work_id = r["id"]
                dir_name = str(r["dir"] or "")
                try:
                    candidates = rating_fix._resolve_workno_candidates(work_id, dir_name)
                    official_title = ""
                    for workno in candidates:
                        info = await dlsite.get_work_info(workno)
                        if info and str(info.get("title") or "").strip():
                            official_title = str(info["title"]).strip()
                            break
                    if not official_title:
                        missed += 1
                        logger.info("[KIKOERU-SCAN] title 重构跳过（DLsite 无数据）: id=%s dir=%s", work_id, dir_name)
                        continue
                    current_title = str(r["title"] or "").strip()
                    if current_title == official_title:
                        unchanged += 1
                        continue
                    await asyncio.to_thread(
                        conn.execute,
                        'UPDATE "t_work" SET title = ? WHERE id = ?',
                        (official_title, work_id),
                    )
                    await asyncio.to_thread(conn.commit)
                    updated += 1
                    logger.info(
                        "[KIKOERU-SCAN] title 重构: id=%s 旧=%s 新=%s",
                        work_id, current_title[:60], official_title[:60],
                    )
                except Exception:
                    missed += 1
                    logger.warning("[KIKOERU-SCAN] title 重构失败 id=%s", work_id, exc_info=True)
        finally:
            conn.close()
        return {"updated": updated, "unchanged": unchanged, "missed": missed}

    # ------------------------------------------------------------ 检查点持久化
    @staticmethod
    def _save_checkpoint(value: str) -> None:
        try:
            save_config({"kikoeru_db": {"scan_checkpoint": str(value)}})
        except Exception:
            logger.warning("[KIKOERU-SCAN] 检查点保存失败", exc_info=True)

    @staticmethod
    def _mark_scan_finished(trigger: str) -> None:
        if trigger != "scan_finished":
            return
        try:
            save_config({"kikoeru_db": {
                "last_scan_finished_at": datetime.now().strftime(_DB_TIME_FMT)
            }})
        except Exception:
            logger.warning("[KIKOERU-SCAN] 扫描完成时间保存失败", exc_info=True)


_listener: Optional[KikoeruScanListener] = None


def get_kikoeru_scan_listener() -> KikoeruScanListener:
    global _listener
    if _listener is None:
        _listener = KikoeruScanListener()
    return _listener
