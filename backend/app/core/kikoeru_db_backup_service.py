"""Kikoeru 数据库定时备份服务（v2.6）

备份类型（文件名 `{db_stem}_{YYYYMMDD_HHMMSS}_{kind}.sqlite3`，存于 backup_dir）：
- activate   原始库备份：功能激活（enabled false→true）时自动做一次，永久保留 1 份
- auto       滚动备份：按 backup_interval_hours 定时，保留 backup_retention 份
- manual     手动备份：页面按钮触发，不自动清理
- snapshot   回滚快照：每次写事务前自动（由 KikoeruDbService.create_backup 触发），
             保留 snapshot_retention 份或 24h
- pre-restore 恢复前备份：每次恢复前自动拍，永久保留

生命周期仿 password_cleanup：类级单例 + start()/stop()/restart()，
在 routes.py 的 startup_event 中拉起。
"""
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config.settings import get_config
from .kikoeru_db_service import KikoeruDbError, get_kikoeru_db_service

logger = logging.getLogger(__name__)


class KikoeruDbBackupService:
    """Kikoeru 数据库定时备份服务（单例）"""

    _instance = None
    _scheduler: Optional[AsyncIOScheduler] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._scheduler = None

    async def start(self):
        """启动定时滚动备份（未启用功能或未配置 db_path 时不启动）。"""
        config = get_config().kikoeru_db
        if not config.enabled:
            logger.info("[KIKOERU-DB] 数据库管理未启用，定时备份不启动")
            return
        if not str(config.db_path or "").strip():
            logger.info("[KIKOERU-DB] 未配置 db_path，定时备份不启动")
            return
        if self._scheduler is not None:
            logger.warning("[KIKOERU-DB] 定时备份已在运行")
            return

        try:
            self._scheduler = AsyncIOScheduler()
            interval_hours = max(float(config.backup_interval_hours or 24.0), 0.1)
            self._scheduler.add_job(
                self._backup_job,
                trigger=IntervalTrigger(hours=interval_hours),
                id="kikoeru_db_backup",
                name="Kikoeru 数据库滚动备份",
                replace_existing=True,
            )
            self._scheduler.start()
            next_run = self._scheduler.get_job("kikoeru_db_backup").next_run_time
            logger.info("[KIKOERU-DB] 定时备份已启动，间隔 %.2f 小时，下次: %s", interval_hours, next_run)
        except Exception as exc:
            logger.error("[KIKOERU-DB] 启动定时备份失败: %s", exc, exc_info=True)
            self._scheduler = None
            raise

    async def stop(self):
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("[KIKOERU-DB] 定时备份已停止")

    async def restart(self):
        await self.stop()
        await self.start()

    async def _backup_job(self):
        """定时滚动备份（sync sqlite 放线程池，避免阻塞事件循环）。"""
        import asyncio

        try:
            service = get_kikoeru_db_service()
            result = await asyncio.to_thread(service.create_backup, "auto")
            await asyncio.to_thread(service.cleanup_retention)
            logger.info("[KIKOERU-DB] 滚动备份完成: %s", result.get("filename"))
        except KikoeruDbError as exc:
            logger.error("[KIKOERU-DB] 滚动备份失败: %s", exc)
        except Exception:
            logger.error("[KIKOERU-DB] 滚动备份异常", exc_info=True)

    async def ensure_activation_backup(self) -> Optional[dict]:
        """功能激活（enabled false→true）时自动做一次原始库备份（activate，永久保留 1 份）。

        由配置保存流程在检测到开关翻转时调用；已有 activate 备份时跳过。
        """
        import asyncio

        config = get_config().kikoeru_db
        if not config.enabled or not str(config.db_path or "").strip():
            return None
        service = get_kikoeru_db_service()
        existing = await asyncio.to_thread(service.list_backups, "activate")
        if existing:
            logger.info("[KIKOERU-DB] 已存在 activate 原始库备份，跳过: %s", existing[0]["filename"])
            return existing[0]
        try:
            result = await asyncio.to_thread(service.create_backup, "activate")
            logger.info("[KIKOERU-DB] 功能激活，已生成原始库备份: %s", result["filename"])
            return result
        except KikoeruDbError as exc:
            logger.error("[KIKOERU-DB] 生成原始库备份失败: %s", exc)
            raise

    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running


_backup_service: Optional[KikoeruDbBackupService] = None


def get_kikoeru_db_backup_service() -> KikoeruDbBackupService:
    global _backup_service
    if _backup_service is None:
        _backup_service = KikoeruDbBackupService()
    return _backup_service
