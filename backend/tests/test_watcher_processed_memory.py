"""监视器已处理名单 / 稳定等待 / 周期扫描自愈的回归测试。

覆盖用户报的"1.03GB RAR 丢进监视目录十分钟没反应"背后的几个失效点：

1. 已处理名单带 TTL，不再永久拉黑（历史上是全代码库只 add 不 clear 的 set）；
2. 关掉再打开监视器会清空名单，让卡住的文件能重新被检测；
3. 稳定等待按体积放宽，且只在"无进展"时才超时，慢速复制不再被误杀；
4. 超时计数带归零窗口，历史偶发超时不会累积成永久拉黑；
5. 周期扫描协程不会静默停摆；
6. 普通压缩包不再被误判成 polyglot。

这些用例不依赖数据库，且 tests/conftest.py 导入期就会连库建库，
所以必须用 --noconftest 运行：
    python -m pytest --noconftest tests/test_watcher_processed_memory.py -q
"""

import asyncio
import os
from types import SimpleNamespace

import pytest

from app.core import watcher as watcher_module
from app.core import file_processor as file_processor_module
from app.core.file_processor import FileProcessor
from app.core.polyglot_detector import RAR_SIG, SEVENZ_SIG, find_embedded_archive
from app.core.watcher import FolderWatcher


def _stub_config():
    """只提供本次改动用到的配置项，避免加载真实配置（会读盘/连库）。"""
    return SimpleNamespace(
        watcher=SimpleNamespace(
            scan_interval=0.01,
            stability_idle_timeout_seconds=300,
            stability_max_total_seconds=900,
            stability_max_total_per_gb_seconds=900,
            stability_max_total_cap_seconds=7200,
            processed_memory_ttl_seconds=86400,
            processed_failure_ttl_seconds=1800,
            scan_supervisor_interval_seconds=30,
        ),
        processing=SimpleNamespace(
            stability_timeout_blacklist_count=3,
            stability_timeout_reset_seconds=3600,
        ),
        storage=SimpleNamespace(input_path="/tmp/not-used"),
    )


def _make_watcher(monkeypatch):
    """绕开 __init__ 构造 FolderWatcher，避免触碰 Observer 与配置加载。"""
    monkeypatch.setattr(watcher_module, "get_config", _stub_config)
    watcher = object.__new__(FolderWatcher)
    watcher.observer = None
    watcher.handler = None
    watcher.is_running = False
    watcher.pending_files = set()
    watcher._processed_files = {}
    watcher._scan_task = None
    watcher._scan_supervisor_task = None
    watcher._last_scan_at = None
    watcher._loop = None
    watcher._paused = False
    watcher._file_processor = None
    return watcher


def _make_processor(monkeypatch):
    monkeypatch.setattr(file_processor_module, "get_config", _stub_config)
    processor = object.__new__(FileProcessor)
    processor._processed_files = set()
    processor._stability_timeout_counts = {}
    return processor


# ========== 已处理名单的生命周期 ==========

def test_processed_entry_expires_after_ttl(monkeypatch):
    """过期后必须重新可检测，并且顺带把记录回收掉。"""
    watcher = _make_watcher(monkeypatch)
    watcher._mark_file_processed("/data/a.rar")
    assert watcher._is_file_processed("/data/a.rar") is True

    watcher._processed_files["/data/a.rar"] = 0.0  # 把过期时刻拨到过去
    assert watcher._is_file_processed("/data/a.rar") is False
    assert "/data/a.rar" not in watcher._processed_files


def test_failure_ttl_is_shorter_than_success_ttl(monkeypatch):
    """超时/异常属于失败，冷却时间必须明显短于成功，否则等于永久拉黑。"""
    watcher = _make_watcher(monkeypatch)
    watcher._mark_file_processed("/data/ok.rar")
    watcher._mark_file_processed("/data/failed.rar", failure=True)
    assert watcher._processed_files["/data/failed.rar"] < watcher._processed_files["/data/ok.rar"]


def test_reset_memory_clears_pending_and_processed(monkeypatch):
    watcher = _make_watcher(monkeypatch)
    watcher._mark_file_processed("/data/a.rar")
    watcher.pending_files.add("/data/b.rar")
    watcher._last_scan_at = 123.0

    watcher._reset_memory()

    assert watcher._processed_files == {}
    assert watcher.pending_files == set()
    assert watcher._last_scan_at is None


def test_stop_clears_processed_memory(monkeypatch):
    """关掉监视器必须清空名单，否则"再打开"对卡住的文件毫无作用。"""
    watcher = _make_watcher(monkeypatch)
    watcher._mark_file_processed("/data/a.rar")
    watcher.is_running = True

    watcher.stop()

    assert watcher._processed_files == {}
    assert watcher.is_running is False


def test_clear_processed_files_returns_count(monkeypatch):
    watcher = _make_watcher(monkeypatch)
    watcher._mark_file_processed("/data/a.rar")
    watcher._mark_file_processed("/data/b.rar")

    assert watcher.clear_processed_files() == 2
    assert watcher._is_file_processed("/data/a.rar") is False


def test_purge_expired_processed_removes_only_expired(monkeypatch):
    watcher = _make_watcher(monkeypatch)
    watcher._mark_file_processed("/data/keep.rar")
    watcher._mark_file_processed("/data/drop.rar")
    watcher._processed_files["/data/drop.rar"] = 0.0

    assert watcher.purge_expired_processed() == 1
    assert watcher._is_file_processed("/data/keep.rar") is True
    assert watcher._is_file_processed("/data/drop.rar") is False


def test_processed_memory_is_bounded(monkeypatch):
    """名单不能无界增长（历史上它只增不减，等同于内存泄漏）。"""
    watcher = _make_watcher(monkeypatch)
    monkeypatch.setattr(watcher, "_PROCESSED_FILES_MAX", 10)
    for i in range(50):
        watcher._mark_file_processed(f"/data/f{i}.rar")
    assert len(watcher._processed_files) <= 10


def test_get_excluded_paths_works_with_dict_backed_memory(monkeypatch):
    """名单改成 dict 后，与 pending 求并集的地方必须仍然可用。"""
    watcher = _make_watcher(monkeypatch)
    watcher.pending_files.add("/data/p.rar")
    watcher._mark_file_processed("/data/d.rar")
    assert watcher._get_excluded_paths() == {"/data/p.rar", "/data/d.rar"}


# ========== 稳定等待窗口 ==========

def test_stability_wait_scales_with_file_size(monkeypatch, tmp_path):
    """1GB 文件的等待上限必须远大于原来写死的 300 秒。"""
    watcher = _make_watcher(monkeypatch)
    path = str(tmp_path / "any.rar")
    with open(path, "wb") as fp:
        fp.write(b"\0" * 64)

    idle_small, total_small = watcher._stability_wait_seconds(path)

    monkeypatch.setattr(os.path, "getsize", lambda _p: 1024 ** 3)
    idle_big, total_big = watcher._stability_wait_seconds(path)

    assert idle_small == idle_big == 300
    assert total_big > total_small
    assert total_big >= 1500


def test_stability_wait_respects_cap(monkeypatch, tmp_path):
    watcher = _make_watcher(monkeypatch)
    path = str(tmp_path / "any.rar")
    with open(path, "wb") as fp:
        fp.write(b"\0" * 64)

    monkeypatch.setattr(os.path, "getsize", lambda _p: 1024 ** 5)  # 1PB
    _idle, total = watcher._stability_wait_seconds(path)
    assert total <= 7200


# ========== 超时计数与拉黑 ==========

def test_stability_timeout_counter_resets_after_window(monkeypatch):
    """跨过归零窗口后重新计数，历史偶发超时不会累积成永久拉黑。"""
    processor = _make_processor(monkeypatch)
    assert processor._bump_stability_timeout("/data/a.rar") == 1
    assert processor._bump_stability_timeout("/data/a.rar") == 2

    count, _ts = processor._stability_timeout_counts["/data/a.rar"]
    processor._stability_timeout_counts["/data/a.rar"] = (count, _ts - 7200)

    assert processor._bump_stability_timeout("/data/a.rar") == 1


def test_call_mark_processed_passes_failure_flag(monkeypatch):
    processor = _make_processor(monkeypatch)
    calls = []
    processor._call_mark_processed(
        lambda p, failure=False: calls.append((p, failure)), "/data/a.rar", failure=True
    )
    assert calls == [("/data/a.rar", True)]


def test_call_mark_processed_supports_legacy_signature(monkeypatch):
    """只接受 (path) 的旧回调不能被 failure 关键字打挂。"""
    processor = _make_processor(monkeypatch)
    calls = []
    processor._call_mark_processed(lambda p: calls.append(p), "/data/a.rar", failure=True)
    assert calls == ["/data/a.rar"]


# ========== 周期扫描自愈 ==========

@pytest.mark.asyncio
async def test_periodic_scan_propagates_cancelled_error(monkeypatch):
    """扫描协程被取消时必须向外传播。

    旧代码是 `except CancelledError: break`，会让扫描永久停摆，
    而 is_running 仍为 True、状态接口照常显示"运行中"。
    """
    watcher = _make_watcher(monkeypatch)
    calls = {"n": 0}

    async def _fake_scan():
        calls["n"] += 1
        raise asyncio.CancelledError()

    watcher._scan_folder = _fake_scan
    with pytest.raises(asyncio.CancelledError):
        await watcher._periodic_scan()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_scan_supervisor_restarts_dead_scan_task(monkeypatch):
    watcher = _make_watcher(monkeypatch)
    watcher.is_running = True
    monkeypatch.setattr(watcher, "_scan_supervisor_interval_seconds", lambda: 0.01)

    async def _dead():
        return None

    watcher._scan_task = asyncio.create_task(_dead())
    await watcher._scan_task
    assert watcher._scan_task.done()

    supervisor = asyncio.create_task(watcher._scan_supervisor())
    await asyncio.sleep(0.08)
    assert not watcher._scan_task.done(), "扫描协程意外退出后应被监护自动拉起"

    supervisor.cancel()
    watcher._scan_task.cancel()
    for task in (supervisor, watcher._scan_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


# ========== polyglot 误判 ==========

def test_plain_rar_is_not_reported_as_polyglot(tmp_path):
    """普通 RAR 的签名就在文件头，不是"带前缀伪装"，必须返回 None。

    以前 _sequential_scan 会返回 ('rar', 0)，导致每个普通 RAR 都被当成
    伪装压缩包，而且要先白读一大块数据。
    """
    path = tmp_path / "plain.rar"
    path.write_bytes(RAR_SIG + b"\x00" + b"\0" * 20000)
    assert find_embedded_archive(str(path)) is None


def test_plain_7z_is_not_reported_as_polyglot(tmp_path):
    path = tmp_path / "plain.7z"
    path.write_bytes(SEVENZ_SIG + b"\0" * 20000)
    assert find_embedded_archive(str(path)) is None


def test_disguised_rar_is_still_detected(tmp_path):
    """真正前面带伪装数据的 polyglot 仍然要能定位到偏移。"""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 4096 + RAR_SIG + b"\x00" + b"\0" * 20000)
    result = find_embedded_archive(str(path))
    assert result is not None
    assert result[0] == "rar"
    assert result[1] == 4096
