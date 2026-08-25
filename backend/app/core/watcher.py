"""文件监视器

监视指定文件夹，检测新文件并触发处理。
使用 FileProcessor 统一处理逻辑。
"""

import os
import asyncio
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
import logging

from ..config.settings import get_config
from ..core.task_engine import Task, TaskType, get_task_engine
from .archive_volume_utils import get_archive_volume_paths
from .deferred_archive_service import get_deferred_archive_service
from .file_processor import get_file_processor

logger = logging.getLogger(__name__)

# 疑似分卷成员的文件名形态：孤儿分卷判断的前置过滤，
# 避免对输入目录里每个普通文件都触发一次 listdir
_ORPHAN_VOLUME_NAME_RE = re.compile(
    r'\.(?:z\d{2}|r\d{2}|7z\.\d{3}|zip\.\d{3}|part\d+)(?:\.(?:rar|zip|7z|exe))?$',
    re.IGNORECASE,
)


class ArchiveHandler(FileSystemEventHandler):
    """文件系统事件处理器

    检测新创建/修改的文件，识别压缩包并触发处理。
    """

    def __init__(
        self,
        on_archive_detected: Callable[[str], None],
        get_excluded_paths: Callable[[], Set[str]],
        is_paused: Callable[[], bool],
        mark_processed: Callable[[str], None],
        on_orphan_volume: Optional[Callable[[str], None]] = None,
    ):
        self.on_archive_detected = on_archive_detected
        self.get_excluded_paths = get_excluded_paths
        self.is_paused = is_paused
        self.mark_processed = mark_processed
        self.on_orphan_volume = on_orphan_volume
        self._file_processor = get_file_processor()

    def on_created(self, event):
        if event.is_directory:
            return
        if self.is_paused():
            return
        file_path = str(event.src_path)
        if file_path in self.get_excluded_paths():
            logger.debug(f"文件在排除列表中，跳过: {file_path}")
            return
        if not os.path.exists(file_path):
            logger.debug(f"文件不存在，跳过: {file_path}")
            return
        result = self._is_archive(file_path)
        if result:
            self.on_archive_detected(file_path)
        elif result is False:
            self._mark_volume_file_processed(file_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        if self.is_paused():
            return
        file_path = str(event.src_path)
        if file_path in self.get_excluded_paths():
            return
        if not os.path.exists(file_path):
            return
        result = self._is_archive(file_path)
        if result:
            self.on_archive_detected(file_path)
        elif result is False:
            self._mark_volume_file_processed(file_path)

    def _mark_volume_file_processed(self, file_path: str):
        """将分卷文件标记为已处理（防止重复检测）。

        首卷已被归档移走的迟到尾卷（.7z.002 等）不能只标记为已处理，
        否则会永久残留在输入目录；转交给延后归档队列单独归档。
        """
        # 下载中的文件（aria2 伴生控制文件还在）此刻的"不是压缩包"只是暂时状态：
        # 若在这里按分卷规则永久标记已处理，下载完成后的 on_modified 会被
        # _processed_files 拦截，首卷（如 .7z.001）将永远不会再创建解压任务。
        if self._file_processor.has_active_download_sidecar(file_path):
            logger.debug("[Watcher] 文件仍在下载中，暂不标记分卷处理: %s", file_path)
            return
        filename = os.path.basename(file_path).lower()
        if self.on_orphan_volume and self._orphan_volume_paths(file_path):
            self.on_orphan_volume(file_path)
            return

        if re.search(r'\.z\d{2}$', filename):
            logger.debug(f"ZIP 分卷文件标记为已处理: {file_path}")
            self.mark_processed(file_path)
        elif re.search(r'\.r\d{2}$', filename):
            logger.debug(f"旧式 RAR 分卷文件标记为已处理: {file_path}")
            self.mark_processed(file_path)
        elif re.search(r'\.7z\.\d{3}$', filename):
            logger.debug(f"7z 分卷文件标记为已处理: {file_path}")
            self.mark_processed(file_path)
        elif re.search(r'\.part\d+\.(rar|zip|7z|exe)$', filename, re.IGNORECASE):
            part_match = re.search(r'\.part(\d+)\.', filename, re.IGNORECASE)
            if part_match and int(part_match.group(1)) > 1:
                logger.debug(f"分卷文件标记为已处理: {file_path}")
                self.mark_processed(file_path)

    def _orphan_volume_paths(self, file_path: str) -> list:
        """判断是否「首卷缺失的迟到尾卷」；是则返回待归档文件，否则空列表。"""
        directory = os.path.dirname(file_path)
        filename = os.path.basename(file_path)
        sibling_casefold = {}
        try:
            sibling_casefold = {name.casefold(): name for name in os.listdir(directory)}
        except OSError:
            return []

        def exists(first_name: str) -> bool:
            return bool(sibling_casefold.get(str(first_name).casefold()))

        match = re.match(r'^(.+)\.7z\.(\d{3})$', filename, re.IGNORECASE)
        if match and int(match.group(2)) > 1:
            if not exists(f"{match.group(1)}.7z.001"):
                return [file_path]
            return []
        match = re.match(r'^(.+)\.zip\.(\d{3})$', filename, re.IGNORECASE)
        if match and int(match.group(2)) > 1:
            if not exists(f"{match.group(1)}.zip.001"):
                return [file_path]
            return []
        match = re.match(r'^(.+)\.part(\d+)\.(rar|zip|7z|exe)$', filename, re.IGNORECASE)
        if match and int(match.group(2)) > 1:
            if not exists(f"{match.group(1)}.part1.{match.group(3)}"):
                return [file_path]
            return []
        match = re.match(r'^(.+)\.(z|r)(\d{2})$', filename, re.IGNORECASE)
        if match and int(match.group(3)) > 1:
            main_ext = "zip" if match.group(2).lower() == "z" else "rar"
            if not exists(f"{match.group(1)}.{main_ext}"):
                return [file_path]
            return []
        return []

    def _is_archive(self, path: str) -> bool:
        """检查是否是压缩包文件（委托给 FileProcessor）"""
        return self._file_processor.is_archive(path)


class FolderWatcher:
    """文件夹监视器

    监视指定文件夹，检测新文件并使用 FileProcessor 处理。
    """

    def __init__(self):
        # 不缓存配置，每次都获取最新配置
        self.observer = None
        self.handler = None
        self.is_running = False
        self.pending_files = set()
        self._processed_files = set()
        self._scan_task = None
        self._loop = None
        self._paused = False  # 暂停监听标志
        self._file_processor = get_file_processor()

    @property
    def config(self):
        """动态获取最新配置"""
        return get_config()

    def _broadcast_status(self, reason: str = "status") -> None:
        try:
            from .realtime_event_service import broadcast_event

            payload = {
                "is_running": bool(self.is_running),
                "watch_path": self.config.storage.input_path,
                "pending_files": list(self.pending_files),
            }
            broadcast_event({
                "type": "watcher.status.changed",
                "reason": reason,
                "id": "watcher",
                "domain": "watcher",
                "status": "running" if self.is_running else "stopped",
                "current_step": f"{len(self.pending_files)} 个待处理文件" if self.pending_files else "",
                "payload": payload,
            })
        except Exception:
            logger.debug("广播 watcher 实时状态失败", exc_info=True)

    def _get_excluded_paths(self):
        """获取所有应该排除的路径（pending + processed）"""
        return self.pending_files | self._processed_files

    def _mark_file_processed(self, file_path: str):
        """将文件标记为已处理"""
        self._processed_files.add(file_path)

    def _is_file_processed(self, file_path: str) -> bool:
        """检查文件是否已处理。

        只认已处理集合，不能把 pending_files 算进来：检测入口
        （_on_archive_detected / _scan_folder）会先把文件放进 pending_files
        再调 process_file，如果这里把 pending 视为"已处理"，
        process_file 第一步就会把自己刚加入的文件跳过，任务永远创建不出来，
        且文件永远进不了 processed 集合，周期扫描每轮都会重复检测。
        pending 期间的防重复由入口处的 pending 查重负责。
        """
        return file_path in self._processed_files

    def pause_watching(self):
        """暂停文件监听（在重命名等操作前调用）"""
        self._paused = True
        if self.observer:
            try:
                self.observer.unschedule_all()
                logger.debug("已暂停文件监听")
            except Exception as e:
                logger.warning(f"暂停监听失败: {e}")

    def resume_watching(self):
        """恢复文件监听"""
        if not self._paused:
            return
        self._paused = False
        if self.observer and self.handler:
            watch_path = self.config.storage.input_path

            def _reschedule():
                try:
                    self.observer.schedule(self.handler, watch_path, recursive=True)
                    logger.debug("已恢复文件监听")
                except Exception as e:
                    logger.warning(f"恢复监听失败: {e}")

            threading.Thread(
                target=_reschedule,
                name="watcher-resume-schedule",
                daemon=True,
            ).start()

    async def start(self):
        """启动监视器"""
        if self.is_running:
            return

        self._loop = asyncio.get_running_loop()

        watch_path = self.config.storage.input_path
        if not os.path.exists(watch_path):
            os.makedirs(watch_path, exist_ok=True)

        self.handler = ArchiveHandler(
            self._on_archive_detected,
            self._get_excluded_paths,
            lambda: self._paused,
            self._mark_file_processed,
            self._on_orphan_volume_detected
        )
        observer = Observer()
        # inotify 后端 schedule 会递归 os.walk 监视目录，放到线程执行避免阻塞主事件循环
        # watchdog 3.x 的 schedule 中 recursive 是仅限关键字参数，按位置传参会直接 TypeError
        await asyncio.to_thread(observer.schedule, self.handler, watch_path, recursive=True)
        observer.start()
        self.observer = observer

        self._scan_task = asyncio.create_task(self._periodic_scan())

        self.is_running = True
        logger.info(f"文件夹监视器已启动: {watch_path}")
        self._broadcast_status("started")

    def stop(self):
        """停止监视器"""
        if not self.is_running:
            return

        if self.observer:
            self.observer.stop()
            self.observer.join()

        if self._scan_task:
            self._scan_task.cancel()

        self.is_running = False
        logger.info("文件夹监视器已停止")
        self._broadcast_status("stopped")

    def _on_archive_detected(self, file_path: str):
        """检测到压缩包"""
        # 检查是否已经在处理中或已处理过
        if file_path in self.pending_files:
            logger.debug(f"文件已在处理中，跳过: {file_path}")
            return

        if file_path in self._processed_files:
            logger.debug(f"文件已处理过，跳过: {file_path}")
            return

        self.pending_files.add(file_path)
        logger.info("检测到新压缩包: path=%s auto_start=%s", file_path, self.config.watcher.auto_start)
        self._broadcast_status("pending_added")

        # 创建自动处理任务
        if self.config.watcher.auto_start:
            logger.debug(f"准备创建处理任务: {file_path}")
            # 使用保存的事件循环来调度任务
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._process_file(file_path), self._loop)
                logger.debug(f"任务已调度: {file_path}")
            else:
                logger.error(f"事件循环未就绪，无法调度任务: {file_path}")
        else:
            logger.info(f"auto_start为false，跳过自动处理: {file_path}")

    def _on_orphan_volume_detected(self, file_path: str):
        """首卷缺失的迟到尾卷：调度到事件循环，交给延后归档队列单独归档。"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._enqueue_orphan_volume(file_path), self._loop)

    async def _enqueue_orphan_volume(self, file_path: str):
        """把首卷已归档的迟到尾卷入队延后归档，避免永久残留在输入目录。"""
        try:
            # 延迟复核：入队瞬间可能正处于下载中（首卷尚未落盘、尾卷仍在增长），
            # 直接冻结身份快照会导致延后归档永远过不了"源文件已变化"校验。
            # 等一段时间后重新确认：文件已稳定、无 aria2 伴生、且首卷确实仍缺失。
            await asyncio.sleep(self._orphan_recheck_delay_seconds())
            if not os.path.isfile(file_path):
                return
            if self._file_processor.has_active_download_sidecar(file_path):
                logger.info("[Watcher] 孤儿分卷复核时仍在下载，放弃入队: %s", file_path)
                return
            try:
                mtime_age = max(0.0, time.time() - os.path.getmtime(file_path))
            except OSError:
                return
            if mtime_age < self._orphan_stable_seconds():
                logger.info(
                    "[Watcher] 孤儿分卷复核时 %d 秒内仍有写入，放弃入队: %s",
                    int(mtime_age),
                    file_path,
                )
                return
            if not self._orphan_volume_paths(file_path):
                # 首卷已出现（或文件形态已变）：交给常规检测流程处理
                return
            archive_service = get_deferred_archive_service()
            if await archive_service.is_source_claimed(file_path):
                return
            result = await archive_service.enqueue_source(file_path)
            if result.get("queued"):
                logger.info("迟到的非首卷分卷已单独入队归档: %s", file_path)
        except Exception:
            logger.warning("迟到的非首卷分卷入队归档失败: %s", file_path, exc_info=True)

    def _orphan_recheck_delay_seconds(self) -> float:
        try:
            return max(30.0, float(get_config().processing.orphan_volume_recheck_delay_seconds))
        except Exception:
            return 180.0

    def _orphan_stable_seconds(self) -> float:
        try:
            return max(10.0, float(get_config().processing.orphan_volume_stable_seconds))
        except Exception:
            return 120.0

    async def _process_file(self, file_path: str):
        """处理文件（使用 FileProcessor 统一流程）"""
        logger.debug(f"[Watcher] 开始处理文件: {file_path}")
        original_path = file_path

        try:
            # 使用 FileProcessor 处理文件
            # 传入暂停/恢复监听回调，用于文件名规范化时避免重复事件
            task = await self._file_processor.process_file(
                file_path,
                auto_classify=self.config.watcher.auto_classify,
                wait_stable=True,
                max_wait=300,
                is_processed=self._is_file_processed,
                mark_processed=self._mark_file_processed,
                pause_fn=self.pause_watching,
                resume_fn=self.resume_watching
            )

            if task:
                # 等待任务完成
                while task.status.value in ["pending", "processing"]:
                    await asyncio.sleep(1)

                # 任务完成后添加到已处理列表
                self._processed_files.add(task.source_path)
                logger.info(f"文件处理完成: {task.source_path}, 状态: {task.status.value}")

                # 处理后删除原文件（如果配置允许且处理成功，且不是重新解压）
                if self.config.watcher.delete_after_process and not task.skip_archive:
                    if task.status.value == "completed":
                        try:
                            source_path = task.source_path
                            archive_status = str((task.task_metadata or {}).get("archive_queue_status") or "").strip()
                            archive_enabled = bool(
                                getattr(getattr(self.config, "auto_process", None), "archive", False)
                            )
                            if archive_enabled and archive_status != "completed":
                                # 入队失败、尚未入队或等待归档时都必须保留源文件；否则
                                # watcher 会删除唯一的可恢复压缩包。
                                logger.warning(
                                    "跳过 watcher 删除，归档尚未安全完成: path=%s status=%s",
                                    source_path,
                                    archive_status or "unknown",
                                )
                                return
                            archive_service = get_deferred_archive_service()
                            if await archive_service.is_source_claimed(source_path):
                                # 延后归档已冻结完整分卷清单；只能由队列在安全发布后删源。
                                logger.info("跳过 watcher 删除，源压缩包已由空闲归档队列声明: %s", source_path)
                                return
                            source_dir = os.path.dirname(source_path)
                            files_to_delete = [
                                path for path in get_archive_volume_paths(source_path)
                                if os.path.exists(path)
                            ]

                            # 删除所有相关文件
                            for fp in files_to_delete:
                                try:
                                    await asyncio.to_thread(os.remove, fp)
                                    logger.info(f"已删除原文件: {fp}")
                                except Exception as e:
                                    logger.warning(f"删除原文件失败: {fp}, {e}")

                            # 清理空源目录（逐级向上）
                            if os.path.isdir(source_dir):
                                config = get_config()
                                protected = {
                                    os.path.abspath(p) for p in [
                                        getattr(config.storage, 'input_path', ''),
                                        getattr(config.storage, 'processed_archives_path', ''),
                                        getattr(config.storage, 'temp_path', ''),
                                        getattr(config.storage, 'library_path', ''),
                                        getattr(config.storage, 'existing_folders_path', ''),
                                    ] if p
                                }
                                current = os.path.abspath(source_dir)
                                while current:
                                    parent = os.path.dirname(current)
                                    if parent == current:
                                        break
                                    if not os.path.isdir(current):
                                        break
                                    if current in protected:
                                        break
                                    try:
                                        if os.listdir(current):
                                            break
                                        await asyncio.to_thread(os.rmdir, current)
                                        logger.info(f"已自动清理空源目录: {current}")
                                        current = parent
                                    except (FileNotFoundError, PermissionError, OSError) as exc:
                                        logger.debug(f"清理空源目录停止: {current}, {exc}")
                                        break
                        except Exception as e:
                            logger.warning(f"删除原文件失败: {task.source_path}, {e}")
                elif task.skip_archive:
                    logger.info(f"跳过删除原文件（重新解压）: {task.source_path}")
            else:
                logger.info(f"[Watcher] 未创建任务: {file_path}")

        except Exception as e:
            logger.error(f"处理文件失败: {file_path}, {e}", exc_info=True)
            self._processed_files.add(file_path)
        finally:
            self.pending_files.discard(original_path)
            self._broadcast_status("pending_removed")

    async def _periodic_scan(self):
        """定期扫描文件夹"""
        while True:
            try:
                await asyncio.sleep(self.config.watcher.scan_interval)
                await self._scan_folder()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定期扫描失败: {e}")

    async def _scan_folder(self):
        """扫描文件夹中的现有文件。

        os.walk 和魔数识别全部在工作线程里跑：输入目录文件多时（含挂进库存
        工作台后的大量下载残留），同步 walk + 逐文件读魔数 + 逐分卷候选 listdir
        会把事件循环卡住数秒，表现为监视器启用后系统卡死。
        """
        watch_path = self.config.storage.input_path

        if not self.handler:
            return

        archive_candidates, orphan_candidates = await asyncio.to_thread(
            self._collect_scan_candidates_sync, watch_path
        )

        engine = get_task_engine()
        for file_path in archive_candidates:
            if await get_deferred_archive_service().is_source_claimed(file_path):
                continue
            existing = any(
                t.source_path == file_path and t.status.value in ["pending", "processing"]
                for t in engine.get_all_tasks()
            )

            if not existing and file_path not in self.pending_files and file_path not in self._processed_files:
                self._on_archive_detected(file_path)

        for file_path in orphan_candidates:
            # 首卷已归档的迟到尾卷：周期扫描兜底入队归档
            await self._enqueue_orphan_volume(file_path)

    def _collect_scan_candidates_sync(self, watch_path: str) -> tuple[list[str], list[str]]:
        """在工作线程里完成目录遍历和压缩包识别，返回 (压缩包候选, 疑似孤儿分卷候选)。"""
        archive_candidates: list[str] = []
        orphan_candidates: list[str] = []
        excluded = self._get_excluded_paths()
        for root, _dirs, files in os.walk(watch_path):
            for file in files:
                file_path = os.path.join(root, file)

                if file_path in excluded:
                    continue

                if self.handler._is_archive(file_path):
                    archive_candidates.append(file_path)
                    continue

                # 只有文件名长得像分卷成员才做孤儿分卷判断，
                # 避免对目录里每个普通文件都触发一次 listdir；
                # 再校验首卷确实缺失，避免把还在等主卷的分卷误归档
                if _ORPHAN_VOLUME_NAME_RE.search(file) and self.handler._orphan_volume_paths(file_path):
                    orphan_candidates.append(file_path)
        return archive_candidates, orphan_candidates


# 全局监视器实例
_watcher: Optional[FolderWatcher] = None


def get_watcher() -> FolderWatcher:
    """获取监视器实例"""
    global _watcher
    if _watcher is None:
        _watcher = FolderWatcher()
    return _watcher
