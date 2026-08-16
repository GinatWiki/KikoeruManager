"""延后归档周边修复的轻量测试：空目录清理与迟到尾卷识别（无 PG 依赖）。"""
import os
from types import SimpleNamespace

from app.core.deferred_archive_service import DeferredArchiveService
from app.core.watcher import ArchiveHandler


def _make_config(tmp_path):
    return SimpleNamespace(storage=SimpleNamespace(
        input_path=str(tmp_path / "input"),
        processed_archives_path=str(tmp_path / "processed"),
        temp_path=str(tmp_path / "temp"),
        library_path=str(tmp_path / "library"),
        existing_folders_path=str(tmp_path / "existing"),
    ))


def test_cleanup_empty_source_dirs_removes_baidu_folder(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    baidu_folder = input_dir / "百度网盘_20260812_121937"
    baidu_folder.mkdir(parents=True)
    # 源压缩包已被归档移走，目录现在为空
    manifest = [{"source_path": str(baidu_folder / "RJ01679823.7z.001")}]

    monkeypatch.setattr("app.core.deferred_archive_service.get_config", lambda: _make_config(tmp_path))
    DeferredArchiveService()._cleanup_empty_source_dirs(manifest)

    assert not baidu_folder.exists(), "归档完成后空源目录应被清理"
    assert input_dir.exists(), "受保护的 input 目录不能被删除"


def test_cleanup_empty_source_dirs_keeps_non_empty_dir(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    baidu_folder = input_dir / "百度网盘_20260812_121937"
    baidu_folder.mkdir(parents=True)
    (baidu_folder / "keep.txt").write_text("still here", encoding="utf-8")
    manifest = [{"source_path": str(baidu_folder / "RJ01679823.7z.001")}]

    monkeypatch.setattr("app.core.deferred_archive_service.get_config", lambda: _make_config(tmp_path))
    DeferredArchiveService()._cleanup_empty_source_dirs(manifest)

    assert baidu_folder.exists(), "目录仍有文件时不得删除"


def _make_handler():
    return ArchiveHandler(
        on_archive_detected=lambda path: None,
        get_excluded_paths=lambda: set(),
        is_paused=lambda: False,
        mark_processed=lambda path: None,
        on_orphan_volume=lambda path: None,
    )


def test_orphan_volume_detects_7z_tail_without_first(tmp_path):
    (tmp_path / "RJ01679823.7z.002").write_bytes(b"x")
    handler = _make_handler()
    assert handler._orphan_volume_paths(str(tmp_path / "RJ01679823.7z.002")) == [str(tmp_path / "RJ01679823.7z.002")]


def test_orphan_volume_not_detected_when_first_present(tmp_path):
    (tmp_path / "RJ01679823.7z.001").write_bytes(b"x")
    (tmp_path / "RJ01679823.7z.002").write_bytes(b"x")
    handler = _make_handler()
    assert handler._orphan_volume_paths(str(tmp_path / "RJ01679823.7z.002")) == []


def test_orphan_volume_detects_part_tail_without_first(tmp_path):
    (tmp_path / "RJ01679823.part2.rar").write_bytes(b"x")
    handler = _make_handler()
    assert handler._orphan_volume_paths(str(tmp_path / "RJ01679823.part2.rar")) == [str(tmp_path / "RJ01679823.part2.rar")]


def test_orphan_volume_ignores_regular_files(tmp_path):
    (tmp_path / "readme.txt").write_bytes(b"x")
    handler = _make_handler()
    assert handler._orphan_volume_paths(str(tmp_path / "readme.txt")) == []
