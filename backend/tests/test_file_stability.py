"""file_stability.wait_file_stable_robust 单元测试。"""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.file_stability import wait_file_stable_robust


def _touch(path: str, size: int, mtime: float):
    """写入指定大小的文件并固定 mtime（Windows 上 os.utime 精度足够）。"""
    with open(path, "wb") as fp:
        fp.write(b"\0" * size)
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_stable_file_passes_quickly(tmp_path):
    """size+mtime 稳定的文件应在 3 次检测后放行。"""
    path = str(tmp_path / "stable.rar")
    _touch(path, 4096, time.time() - 10)

    await asyncio.wait_for(
        wait_file_stable_robust(
            path, max_wait=10, required_stable_checks=3,
            check_interval=0.01, log_prefix="[T]",
        ),
        timeout=5,
    )


@pytest.mark.asyncio
async def test_growing_file_times_out(tmp_path):
    """size 持续增长的文件不应放行，直到超时抛 TimeoutError。"""
    path = str(tmp_path / "growing.rar")
    with open(path, "wb") as fp:
        fp.write(b"\0" * 4096)

    def fake_stat(_path, _counter={"n": 0}):
        _counter["n"] += 1
        # 每次检测 size 增长，模拟仍在复制
        return SimpleNamespace(
            st_size=4096 + _counter["n"] * 1024, st_mtime=time.time(),
        )

    with patch("os.stat", side_effect=fake_stat):
        with pytest.raises(TimeoutError) as exc_info:
            await wait_file_stable_robust(
                path, max_wait=0.1, required_stable_checks=3,
                check_interval=0.01, log_prefix="[T]",
            )
    assert "growing.rar" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mtime_stable_with_size_jitter_passes(tmp_path):
    """mtime 稳定但 size 抖动（NAS stat 缓存）时，mtime 维度应放行。"""
    path = str(tmp_path / "jitter.rar")
    fixed_mtime = time.time() - 10
    _touch(path, 8192, fixed_mtime)

    def fake_stat(_path, _counter={"n": 0}):
        _counter["n"] += 1
        # size 在两个值之间抖动，mtime 固定
        size = 8192 if _counter["n"] % 2 else 7168
        return SimpleNamespace(st_size=size, st_mtime=fixed_mtime)

    with patch("os.stat", side_effect=fake_stat):
        await asyncio.wait_for(
            wait_file_stable_robust(
                path, max_wait=10, required_stable_checks=3,
                check_interval=0.01, log_prefix="[T]",
            ),
            timeout=5,
        )


@pytest.mark.asyncio
async def test_permission_error_soft_release_after_threshold(tmp_path):
    """open 持续 PermissionError 但 size 稳定时，达到软放行阈值后放行。"""
    path = str(tmp_path / "locked.rar")
    _touch(path, 4096, time.time() - 10)

    def fake_open(_path, *_args, **_kwargs):
        raise PermissionError(13, "文件被另一进程占用")

    # required_stable_checks=3 → 软放行阈值 = max(20, 18) = 20 次
    with patch("builtins.open", side_effect=fake_open):
        await asyncio.wait_for(
            wait_file_stable_robust(
                path, max_wait=30, required_stable_checks=3,
                check_interval=0.001, log_prefix="[T]",
            ),
            timeout=10,
        )


@pytest.mark.asyncio
async def test_stat_error_does_not_crash_and_logs(tmp_path, caplog):
    """os.stat 抛异常时应记录 WARNING 而不是崩溃，最终超时抛出。"""
    path = str(tmp_path / "error.rar")

    with patch("os.path.exists", return_value=True), \
         patch("os.stat", side_effect=OSError(5, "拒绝访问")):
        with pytest.raises(TimeoutError):
            await wait_file_stable_robust(
                path, max_wait=0.05, required_stable_checks=3,
                check_interval=0.01, log_prefix="[T]",
            )

    assert any(
        record.levelname == "WARNING" and "OSError" in record.getMessage()
        for record in caplog.records
    )
