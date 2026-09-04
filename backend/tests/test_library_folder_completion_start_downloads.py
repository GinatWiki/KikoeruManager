"""补全文件夹「启动下载」回归测试。

重点守住一个已经踩过的坑：`start_downloads()` 构造任务 metadata 时要读
`config.storage.temp_path` / `config.asmr_sync`，但函数里忘记先取
`config = get_config()`，导致每次启动都抛 NameError，被逐项 except 吞掉后
整批失败，最终以 400 + 英文 NameError 文案返回给前端。

本文件不依赖数据库，必须用 --noconftest 运行（tests/conftest.py 会在导入期连库建库）：

    cd backend && python -m pytest --noconftest tests/test_library_folder_completion_start_downloads.py -q
"""
import asyncio
import logging
import os
from types import SimpleNamespace

import pytest

import app.core.library_folder_completion_service as folder_completion_module
from app.core.library_folder_completion_service import LibraryFolderCompletionService


class _FakeManager:
    def __init__(self, library):
        self._library = library

    def get_library_definition(self, library_id):
        return self._library

    def _local_path_is_within_root(self, path, root):
        return True


class _FakeResourceService:
    def __init__(self):
        self.sessions = {}
        self._seq = 0

    def normalize_rjcode(self, value):
        return str(value or "").strip().upper()

    def classify_resource_type(self, file_name, relative_path=""):
        return "audio"

    def _create_download_session(self, **kwargs):
        self._seq += 1
        session_id = f"session-{self._seq}"
        self.sessions[session_id] = dict(kwargs)
        return session_id

    def _update_session(self, session_id, **kwargs):
        self.sessions.setdefault(session_id, {}).update(kwargs)


class _FakeEngine:
    def __init__(self):
        self.submitted = []

    async def submit(self, task):
        self.submitted.append(task)
        return task.id


class _FakeConfig:
    def __init__(self, temp_path):
        self.storage = SimpleNamespace(temp_path=temp_path)
        self.asmr_sync = SimpleNamespace(verify_md5_after_download=False, download_timeout_seconds=42)


def _build_service(tmp_path, monkeypatch):
    library = SimpleNamespace(
        id="lib-1",
        type="local",
        writable=True,
        root_path=str(tmp_path),
        browse_root_path=str(tmp_path),
    )
    service = object.__new__(LibraryFolderCompletionService)
    service.manager = _FakeManager(library)
    service.resource_service = _FakeResourceService()
    engine = _FakeEngine()
    monkeypatch.setattr(folder_completion_module, "get_task_engine", lambda: engine)
    monkeypatch.setattr(folder_completion_module, "get_config", lambda: _FakeConfig(str(tmp_path / "temp")))
    return service, library, engine


def _single_item(folder_path):
    return {
        "rjcode": "RJ01000001",
        "folder_path": folder_path,
        "selected_resources": [
            {
                "file_name": "track01.mp3",
                "remote_url": "https://example.invalid/track01.mp3",
                "size_bytes": 1024,
            }
        ],
    }


def test_start_downloads_reads_config_before_building_metadata(tmp_path, monkeypatch):
    """config 未取到时这里会退化成 400 + NameError，是最容易回归的一处。"""
    folder = tmp_path / "RJ01000001"
    folder.mkdir()
    service, library, engine = _build_service(tmp_path, monkeypatch)

    result = asyncio.run(service.start_downloads(library.id, [_single_item(str(folder))]))

    assert result["created_count"] == 1
    assert result["errors"] == []
    assert len(engine.submitted) == 1

    metadata = engine.submitted[0].task_metadata
    assert metadata["download_base_path"] == os.path.join(str(tmp_path / "temp"), "library_folder_completion")
    assert metadata["verify_md5_after_download"] is False
    assert metadata["download_timeout_seconds"] == 42
    assert metadata["source_action"] == "folder_completion"
    assert metadata["upload_options"]["target_path"] == str(folder)


def test_start_downloads_logs_error_when_every_item_fails(tmp_path, monkeypatch, caplog):
    """整批失败通常是配置/代码问题，必须留下 ERROR 级日志而不是只有 warning。"""
    folder = tmp_path / "RJ01000002"
    folder.mkdir()
    service, library, engine = _build_service(tmp_path, monkeypatch)

    with caplog.at_level(logging.ERROR, logger="app.core.library_folder_completion_service"):
        with pytest.raises(ValueError) as excinfo:
            asyncio.run(service.start_downloads(library.id, [{
                "rjcode": "RJ01000002",
                "folder_path": str(folder),
                "selected_resources": [],
            }]))

    assert "没有选中任何缺失文件" in str(excinfo.value)
    assert engine.submitted == []
    assert any("补全文件夹" in record.getMessage() for record in caplog.records)
