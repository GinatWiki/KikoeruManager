"""浏览完整性探测（名字级 diff）回归测试。

背景（v2.6.40 真机反馈）：进入目录显示旧内容，需等 ~2s 轮询自纠。
根因：默认浏览链路（browser/files 索引分支）的完整性探测用
``len(fs_names) > index_total`` 计数比较——重命名/移动是 -1+1 计数守恒，
探测永远不触发，快照里的旧名字（幽灵）+ 缺失的新名字直接返回给前端。

修复：`_list_files_via_index` 的本地分支改为**名字集合 diff**——
磁盘 readdir 一次（本来就有），与索引条目名对比，任何差异都
`return None`（回退 _list_files 真扫盘，本次响应即正确）并入队 reconcile。

打桩原则：不连库。FakeEntry 直接构造，os.listdir 用 monkeypatch 控制，
验证三类场景：磁盘多（新增）、快照多（幽灵/重命名旧名）、两侧同数但名字不同（重命名）。
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.library_manager import LibraryManager  # noqa: E402


def _manager() -> LibraryManager:
    return LibraryManager.__new__(LibraryManager)


def _entry(name, relative_path, entry_type="file", size=100):
    return SimpleNamespace(
        library_id="lib1",
        entry_type=entry_type,
        relative_path=relative_path,
        name=name,
        rjcode=None,
        parent_path="",
        size=size,
        file_count=0,
        mtime=None,
        depth=None,
        absolute_path=f"D:/data/lib/{relative_path}",
        indexed_at=0,
        generation=1,
        materialized_seq=0,
    )


def _run_probe(monkeypatch, tmp_path, entries, disk_names):
    """驱动 _list_files_via_index 的 diff 探测，返回 (indexed_result, reconcile_calls)。"""
    from app.core import library_index as index_pkg

    manager = _manager()

    monkeypatch.setattr(
        LibraryManager,
        "_library_uses_inventory_index",
        lambda self, library: True,
    )
    monkeypatch.setattr(
        LibraryManager,
        "_index_has_usable_snapshot",
        lambda self, service, library_id: True,
    )
    monkeypatch.setattr(
        LibraryManager,
        "_validate_local_index_entries_for_read",
        lambda self, library, ents, return_stale_paths=False: (
            [e for e in ents], set()
        ),
    )
    # os.listdir 返回磁盘真名（带 skip 过滤由 _should_skip_entry 决定）
    monkeypatch.setattr(os, "listdir", lambda _p: list(disk_names))
    monkeypatch.setattr(
        LibraryManager,
        "_should_skip_entry",
        lambda self, name: name.startswith(".") or name.startswith("_"),
    )

    reconcile_calls = []
    monkeypatch.setattr(
        LibraryManager,
        "_report_browse_mismatch_and_reconcile",
        lambda self, library, dir_path, *, disk_only, ghost_only: reconcile_calls.append(
            (dir_path, sorted(disk_only), sorted(ghost_only))
        ),
    )

    class _FakeStoreService:
        def list_children_page(self, *_a, **_k):
            return {
                "entries": list(entries),
                "total": len(entries),
                "next_page_cursor": None,
                "used_page_cursor": False,
            }

        def has_library_entries(self, _library_id):
            return True

    # manager 内部 `from .library_index import get_library_index_service` 解析到
    # 包级 re-export，须 patch 包命名空间的名字
    monkeypatch.setattr(
        index_pkg,
        "get_library_index_service",
        lambda: _FakeStoreService(),
    )

    library = SimpleNamespace(
        id="lib1", name="测试库", type="local",
        root_path=str(tmp_path), browse_root_path=None, synology=None,
    )
    result = manager._list_files_via_index(
        library,
        page=1,
        page_size=200,
        current_path=str(tmp_path),
        browse_root=str(tmp_path),
        parent_path="",
        sort_by="size",
        sort_order="desc",
    )
    return result, reconcile_calls


def test_probe_falls_back_on_ghost_entry(tmp_path, monkeypatch):
    """快照有旧名而磁盘没有（删除/重命名旧侧）→ 回退 + diff 命中。"""
    entries = [
        _entry("old-name", "old-name"),
        _entry("b.wav", "b.wav"),
    ]
    result, calls = _run_probe(monkeypatch, tmp_path, entries, ["b.wav"])
    assert result is None                       # 回退 FS 扫描
    assert len(calls) == 1
    _, disk_only, ghost_only = calls[0]
    assert disk_only == []
    assert ghost_only == ["old-name"]


def test_probe_falls_back_on_rename_count_conservation(tmp_path, monkeypatch):
    """重命名场景：两侧总数相同（计数守恒）但名字不同 → 旧探测打不中，diff 必须命中。"""
    entries = [
        _entry("old-name", "old-name"),
        _entry("b.wav", "b.wav"),
    ]
    # 磁盘上 old-name 已改成 new-name：总数都是 2
    result, calls = _run_probe(
        monkeypatch, tmp_path, entries, ["new-name", "b.wav"]
    )
    assert result is None
    assert len(calls) == 1
    _, disk_only, ghost_only = calls[0]
    assert disk_only == ["new-name"]
    assert ghost_only == ["old-name"]


def test_probe_passes_when_names_match(tmp_path, monkeypatch):
    """磁盘与快照一致 → 走索引快速路径，不回退。"""
    entries = [
        _entry("a.wav", "a.wav", size=100),
        _entry("b.wav", "b.wav", size=200),
    ]
    result, calls = _run_probe(
        monkeypatch, tmp_path, entries, ["a.wav", "b.wav"]
    )
    assert result is not None
    assert result.get("browse_via_index") is True
    assert calls == []


def test_probe_ignores_hidden_files(tmp_path, monkeypatch):
    """隐藏文件（./_前缀）双侧都不参与 diff，不触发误报。"""
    entries = [_entry("a.wav", "a.wav")]
    result, calls = _run_probe(
        monkeypatch, tmp_path, entries, ["a.wav", ".hidden", "_work"]
    )
    assert result is not None
    assert calls == []


def test_probe_falls_back_on_disk_only_entry(tmp_path, monkeypatch):
    """磁盘新条目快照没有（解压新完成）→ 回退 + diff 命中。"""
    entries = [_entry("a.wav", "a.wav")]
    result, calls = _run_probe(
        monkeypatch, tmp_path, entries, ["a.wav", "new-dir"]
    )
    assert result is None
    assert len(calls) == 1
    _, disk_only, ghost_only = calls[0]
    assert disk_only == ["new-dir"]
    assert ghost_only == []
