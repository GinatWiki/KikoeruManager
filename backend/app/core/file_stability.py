"""共享的文件稳定检测模块。

抽取自 extract_service._wait_file_stable 的健壮版实现，供
extract_service（解压前预检）与 file_processor（监视器创建任务前）共用，
避免两套判定逻辑漂移：

- size + mtime 双维度稳定判定（任一维度连续稳定即通过）；
- 超时按"无进展时长"计：只要 size 或 mtime 仍在变化就不会超时，
  因此 1GB 级文件慢慢复制不会被误杀；另设绝对总时长上限兜底；
- size 瞬时回退按 NAS stat 缓存抖动处理，不重置稳定计数；
- open 探测 1 字节检测文件锁定，PermissionError/OSError 软放行
  （Windows 写入中的文件 / SMB 临时锁）；
- stat 异常日志升级为 WARNING（含异常类型与路径），用户日志可见；
- 超时抛 TimeoutError。
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def wait_file_stable_robust(
    file_path: str,
    max_wait: int = 300,
    required_stable_checks: int = 3,
    check_interval: float = 2.0,
    log_prefix: str = "[FileStability]",
    max_total_wait: Optional[float] = None,
) -> None:
    """等待文件复制完成（size+mtime 双维度 + open 探测）。

    Args:
        file_path: 文件路径
        max_wait: **无进展**超时（秒）。只要 size 或 mtime 仍在变化就重新计时，
            所以大文件慢慢复制不会误判。触发意味着文件已经停滞这么久。
        required_stable_checks: 需要连续稳定的检测次数
        check_interval: 每次检测间隔（秒）
        log_prefix: 日志前缀
        max_total_wait: 绝对总时长上限（秒），为 None 时取 max_wait 的 6 倍。
            文件持续/极慢增长时无进展超时不会触发，靠它兜底防止无限等待。

    Raises:
        TimeoutError: 等待超时
    """
    previous_size = -1
    previous_mtime = -1.0
    stable_count = 0
    permission_failures = 0
    max_permission_failures = max(20, required_stable_checks * 6)
    # 绝对上限必须有兜底默认值：文件持续（哪怕极慢地）增长时无进展超时永远不会
    # 触发，没有硬上限的话一个卡住的写入方会让本函数无限等待。
    # 默认给到无进展超时的 6 倍，对应历史上"连续 3 次重试"的时间预算。
    if max_total_wait is None:
        max_total_wait = max_wait * 6
    max_total_wait = max(float(max_total_wait), float(max_wait))
    start_time = asyncio.get_event_loop().time()
    last_progress_time = start_time
    last_max_size = 0

    logger.info(f"{log_prefix} 开始等待文件复制完成: {file_path}")

    while stable_count < required_stable_checks:
        current_time = asyncio.get_event_loop().time()

        # 检查超时：只按"无进展时长"判定。文件还在增长（复制进行中）就一直等，
        # 这是 1GB 级文件被 300 秒固定窗口误杀的根因。
        if current_time - last_progress_time > max_wait:
            raise TimeoutError(
                f"等待文件复制完成超时（{max_wait}秒内无任何进展）: {file_path}"
            )
        # 绝对总时长兜底：文件持续/极慢增长时不至于无限等下去
        if current_time - start_time > max_total_wait:
            raise TimeoutError(
                f"等待文件复制完成超过总时长上限（{max_total_wait:.0f}秒）: {file_path}"
            )

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

            # 任一维度发生变化都算"有进展"，用于重置无进展超时。
            # 与上面的稳定判定解耦：size 暂停但 mtime 仍在变也算进展。
            if previous_size < 0 or current_size != previous_size or not mtime_stable:
                last_progress_time = current_time

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
