import hashlib
import os
from types import SimpleNamespace

import pytest

from app.core.audio_dedup_service import (
    _MIN_DEDUP_SIZE,
    deduplicate_audio_versions,
    file_fingerprint,
    language_priority_of,
)
from app.core.filter_recovery_service import FilterRecoveryService


def _make_wav(path, payload, size=None):
    """写入文件；payload 用 bytes 时直接写，用 int 时写入填充到该大小。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(payload, int):
        data = b"RIFF" + hashlib.sha1(str(payload).encode()).digest() + (b"\x00" * max(0, payload - 24))
        data = data[: max(payload, 24)]
        if len(data) < payload:
            data += b"\x00" * (payload - len(data))
    else:
        data = payload
    with open(path, "wb") as handle:
        handle.write(data)


def test_language_priority_prefers_simplified_chinese():
    assert language_priority_of("简体中文/WAV/01.wav") == 0
    assert language_priority_of("繁體中文/WAV/01.wav") is None
    assert language_priority_of("日本語/WAV/01.wav") is None
    assert language_priority_of("") is None


def test_file_fingerprint_same_content_same_fingerprint(tmp_path):
    left = tmp_path / "a.wav"
    right = tmp_path / "b.wav"
    content = b"\x00" * (_MIN_DEDUP_SIZE + 1024)
    _make_wav(str(left), content)
    _make_wav(str(right), content)
    assert file_fingerprint(str(left)) == file_fingerprint(str(right))


def test_file_fingerprint_differs_on_content(tmp_path):
    left = tmp_path / "a.wav"
    right = tmp_path / "b.wav"
    _make_wav(str(left), b"\x01" * (_MIN_DEDUP_SIZE + 1024))
    _make_wav(str(right), b"\x02" * (_MIN_DEDUP_SIZE + 1024))
    assert file_fingerprint(str(left)) != file_fingerprint(str(right))


@pytest.mark.asyncio
async def test_deduplicate_keeps_simplified_chinese_version(tmp_path, monkeypatch):
    root = tmp_path / "work"
    content = b"\x11" * (256 * 1024)  # 256KB，超过 64KB 阈值
    for rel in ["日本語/WAV/01.wav", "简体中文/WAV/01.wav", "繁體中文/WAV/01.wav"]:
        _make_wav(str(root / rel), content)
    _make_wav(str(root / "cover.jpg"), b"jpeg-data")

    recovery_root = tmp_path / "recovery"
    service = FilterRecoveryService(recovery_root=str(recovery_root))
    monkeypatch.setattr(
        "app.core.filter_recovery_service.get_filter_recovery_service",
        lambda: service,
    )
    task = SimpleNamespace(id="dedup-test-task")

    result = await deduplicate_audio_versions(str(root), task)

    assert result["removed_count"] == 2
    kept = [str(root / "日本語/WAV/01.wav"), str(root / "简体中文/WAV/01.wav"), str(root / "繁體中文/WAV/01.wav")]
    existing = [path for path in kept if os.path.exists(path)]
    assert existing == [str(root / "简体中文/WAV/01.wav")], "应只保留简体中文目录里的版本"
    assert os.path.exists(str(root / "cover.jpg")), "非音频文件不受影响"
    removed_items = result["removed_items"]
    assert len(removed_items) == 2
    assert all(item.get("recovery_id") for item in removed_items), "清理项应带恢复区信息"
    summary = service.public_summary("dedup-test-task")
    assert summary.get("available_count") == 2


@pytest.mark.asyncio
async def test_deduplicate_ignores_different_content_and_small_files(tmp_path, monkeypatch):
    root = tmp_path / "work"
    _make_wav(str(root / "日本語/WAV/01.wav"), b"\xaa" * (256 * 1024))
    _make_wav(str(root / "简体中文/WAV/01.wav"), b"\xbb" * (256 * 1024))
    # 小于阈值的小文件不参与判重
    _make_wav(str(root / "日本語/WAV/02.wav"), b"\xcc" * (8 * 1024))
    _make_wav(str(root / "简体中文/WAV/02.wav"), b"\xcc" * (8 * 1024))

    service = FilterRecoveryService(recovery_root=str(tmp_path / "recovery"))
    monkeypatch.setattr(
        "app.core.filter_recovery_service.get_filter_recovery_service",
        lambda: service,
    )
    task = SimpleNamespace(id="dedup-test-task-2")

    result = await deduplicate_audio_versions(str(root), task)

    assert result["removed_count"] == 0
    assert os.path.exists(str(root / "日本語/WAV/01.wav"))
    assert os.path.exists(str(root / "简体中文/WAV/01.wav"))
    assert os.path.exists(str(root / "日本語/WAV/02.wav"))
    assert os.path.exists(str(root / "简体中文/WAV/02.wav"))


@pytest.mark.asyncio
async def test_deduplicate_skips_missing_dir(tmp_path, monkeypatch):
    service = FilterRecoveryService(recovery_root=str(tmp_path / "recovery"))
    monkeypatch.setattr(
        "app.core.filter_recovery_service.get_filter_recovery_service",
        lambda: service,
    )
    result = await deduplicate_audio_versions(str(tmp_path / "not-exist"), SimpleNamespace(id="t"))
    assert result["removed_count"] == 0
    assert result["skipped"] == "work_dir_missing"
