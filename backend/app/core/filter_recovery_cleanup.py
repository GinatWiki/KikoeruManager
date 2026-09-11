"""
过滤恢复区智能清理服务
按保留策略定期清理临时目录下的过滤恢复数据（filter-recovery/<任务ID>），
防止被过滤音频长期堆积写满磁盘（用户实测满盘导致过滤全部失效的事故）。
"""

import logging
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config.settings import get_config
from .filter_recovery_service import get_filter_recovery_service

logger = logging.getLogger(__name__)


class FilterRecoveryCleanupService:
    """过滤恢复区智能清理服务"""

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
        """启动清理服务（先做一次存量迁移，再挂定时任务）"""
        config = get_config().filter_recovery_cleanup
        recovery_service = get_filter_recovery_service()

        # 存量迁移：旧位置（DATA_PATH / config 推断）→ 临时目录下，尽力而为不阻塞启动
        try:
            recovery_service.migrate_legacy_recovery_root()
        except Exception:
            logger.warning("[FILTER-RECOVERY] 存量恢复区迁移失败，继续启动（旧目录保留）", exc_info=True)

        if not config.enabled:
            logger.info("过滤恢复区智能清理服务已禁用")
            return

        if self._scheduler is not None:
            logger.warning("过滤恢复区清理服务已经在运行")
            return

        try:
            self._scheduler = AsyncIOScheduler()
            self._scheduler.add_job(
                self._cleanup_job,
                trigger=CronTrigger.from_crontab(config.cron_expression),
                id="filter_recovery_cleanup",
                name="过滤恢复区智能清理",
                replace_existing=True,
            )
            self._scheduler.start()
            logger.info(
                f"过滤恢复区智能清理服务已启动，Cron表达式: {config.cron_expression}，"
                f"保留天数: {config.preserve_days}，容量上限: {config.max_size_gb}GB，"
                f"最少保留: {config.min_keep_count} 个任务"
            )
            next_run = self._scheduler.get_job("filter_recovery_cleanup").next_run_time
            logger.info(f"下次清理时间: {next_run}")
        except Exception as e:
            logger.error(f"启动过滤恢复区清理服务失败: {e}")
            self._scheduler = None
            raise

    async def stop(self):
        """停止清理服务"""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("过滤恢复区智能清理服务已停止")

    async def restart(self):
        """重启清理服务（配置变更后调用）"""
        await self.stop()
        await self.start()

    async def _cleanup_job(self):
        """定时清理任务"""
        try:
            config = get_config().filter_recovery_cleanup
            logger.info("开始执行过滤恢复区智能清理...")
            result = get_filter_recovery_service().cleanup_expired(
                preserve_days=config.preserve_days,
                max_size_gb=config.max_size_gb,
                min_keep_count=config.min_keep_count,
            )
            logger.info(
                "过滤恢复区清理完成: 删除 %d 个任务目录，释放 %.2f GB，剩余 %d 个",
                len(result.get("deleted_tasks") or []),
                (result.get("freed_bytes") or 0) / (1024 ** 3),
                result.get("kept_count") or 0,
            )
        except Exception as e:
            logger.error(f"过滤恢复区清理任务执行失败: {e}")

    async def cleanup_now(self, dry_run: bool = False) -> dict:
        """手动触发一次清理（dry_run 只返回计划删除项）"""
        config = get_config().filter_recovery_cleanup
        kwargs = dict(
            preserve_days=config.preserve_days,
            max_size_gb=config.max_size_gb,
            min_keep_count=config.min_keep_count,
        )
        if dry_run:
            # dry_run：只统计不删除（通过极大保留天数实现"不触发按期删除"不可靠，
            # 直接传 min_keep_count=极大值保留全部更直观）
            kwargs["min_keep_count"] = 10 ** 9
            kwargs["max_size_gb"] = 0  # 0 = 不按容量清理
        return get_filter_recovery_service().cleanup_expired(**kwargs)

    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running


# 全局服务实例
_cleanup_service: Optional[FilterRecoveryCleanupService] = None


def get_filter_recovery_cleanup_service() -> FilterRecoveryCleanupService:
    """获取清理服务实例（单例）"""
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = FilterRecoveryCleanupService()
    return _cleanup_service
