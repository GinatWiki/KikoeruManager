"""共享的文件稳定检测模块。

抽取自 extract_service._wait_file_stable 的健壮版实现，供
extract_service（解压前预检）与 file_processor（监视器创建任务前）共用，
避免两套判定逻辑漂移：

- size + mtime 双维度稳定判定（任一维度连续稳定即通过）；
- size 瞬时回退按 NAS stat 缓存抖动处理，不重置稳定计数；
- open 探测 1 字节检测文件锁定，PermissionError/OSError 软放行
  （Windows 写入中的文件 / SMB 临时锁）；
- stat 异常日志升级为 WARNING（含异常类型与路径），用户日志可见；
- 超时抛 TimeoutError。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


async def wait_file_stable_robust(
    file_path: str,
    max_wait: int = 300,
    required_stable_checks: int = 3,
    check_interval: float = 2.0,
    log_prefix: str = "[FileStability]",
) -> None:
    """等待文件复制完成（size+mtime 双维度 + open 探测）。

    Args:
        file_path: 文件路径
        max_wait: 最大等待时间（秒）
        required_stable_checks: 需要连续稳定的检测次数
        check_interval: 每次检测间隔（秒）
        log_prefix: 日志前缀

    Raises:
        TimeoutError: 等待超时
    """
    previous_size = -1
    previous_mtime = -1.0
    stable_count = 0
    permission_failures = 0
    max_permission_failures = max(20, required_stable_checks * 6)
    start_time = asyncio.get_event_loop().time()
    last_progress_time = start_time
    last_max_size = 0

    logger.info(f"{log_prefix} 开始等待文件复制完成: {file_path}")

    while stable_count < required_stable_checks:
        current_time = asyncio.get_event_loop().time()

        # 检查超时
        if current_time - start_time > max_wait:
            raise TimeoutError(f"等待文件复制完成超时 ({max_wait}秒): {file_path}")

        try:
            if not os.path.exists(file_path):
                await asyncio.sleep(check_interval)
                continue

            stat = os.stat(file_path)
            current_size = stat.st_size
            current_mtime = stat.st_mtime

            # 检查文件是否过小
            if current_size < 1024:  # 小于1KB
                logger.debug(f"{log_prefix} 文件太小，继续等待: {file_path} ({current_size} bytes)")
                await asyncio.sleep(check_interval)
                continue

            size_grew = current_size > last_max_size
            last_max_size = max(last_max_size, current_size)
            size_stable = (current_size == previous_size) and not size_grew
            mtime_stable = previous_mtime > 0 and abs(current_mtime - previous_mtime) < 1e-3

            if size_stable or mtime_stable:
                stable_count += 1
                # 尝试读取 1 字节，检测文件是否仍被其他进程锁定
                try:
                    with open(file_path, 'rb') as f:
                        f.read(1)
                    permission_failures = 0
                    if stable_count >= required_stable_checks:
                        logger.info(
                            f"{log_prefix} 文件复制完成检测通过: {file_path} "
                            f"({current_size} bytes, size_stable={size_stable}, "
                            f"mtime_stable={mtime_stable})"
                        )
                        return
                except (PermissionError, OSError) as exc:
                    permission_failures += 1
                    if size_stable and permission_failures >= max_permission_failures:
                        logger.warning(
                            "%s 文件 size 稳定但读取持续失败 %d 次，软放行: %s, %s: %s",
                            log_prefix, permission_failures, file_path,
                            type(exc).__name__, exc,
                        )
                        return
                    logger.warning(
                        "%s 文件仍被锁定 (%d/%d): %s, %s: %s",
                        log_prefix, permission_failures,
                        max_permission_failures, file_path,
                        type(exc).__name__, exc,
                    )
                    stable_count = 0
            else:
                if stable_count > 0:
                    logger.info(f"{log_prefix} 文件仍在复制中: {file_path} ({current_size} bytes)")
                stable_count = 0
                last_progress_time = current_time

            previous_size = current_size
            previous_mtime = current_mtime

            # 长时间无进展提示（不影响流程）
            if current_time - last_progress_time > 60:
                logger.warning(
                    f"{log_prefix} 文件复制可能已停滞: {file_path}, 当前大小: {current_size} bytes"
                )

        except Exception as e:
            logger.warning(
                "%s 等待文件稳定时出错: %s: %s (%s)",
                log_prefix, type(e).__name__, e, file_path,
            )
            await asyncio.sleep(check_interval)
            continue

        await asyncio.sleep(check_interval)
