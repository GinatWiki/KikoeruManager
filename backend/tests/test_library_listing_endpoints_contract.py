"""4 个旧端点的响应契约快照测试（重构阶段 1 收尾）。

锁定「旧端点响应结构在薄适配器改造前后逐字段一致」：
- browser/files 索引分支：_list_files_via_index → _build_search_entry_from_index（收敛点 1）
- folder-contents / list-folders 索引分支：_folder_content_item_from_index（收敛点 2）
- library/files：直接扫库根 + DB 解压时间（无索引分支，结构由端点自身锁定）

打桩原则：不连数据库、不真扫盘。用 FakeIndexEntry + FakeService 直接驱动
library_manager 的条目构造函数，断言输出行的**字段集合**与关键值语义
（这些字段集合就是前端消费者依赖的 wire contract）。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.library_manager import LibraryManager  # noqa: E402
from app.core.library_index.listing_view import normalize_listing_item  # noqa: E402


def _fake_library(lib_type="local", root_path="D:/data/lib"):
    return SimpleNamespace(
        id="lib1",
        name="测试库",
        type=lib_type,
        root_path=root_path,
        browse_root_path=None,
        synology=None,
    )


class FakeIndexEntry:
    """极简 IndexEntry 替身。"""

    def __init__(self, name, entry_type, relative_path, size=0, file_count=0, mtime=None, rjcode=None):
        self.library_id = "lib1"
        self.entry_type = entry_type
        self.relative_path = relative_path
        self.name = name
        self.rjcode = rjcode
        self.parent_path = ""
        self.size = size
        self.file_count = file_count
        self.mtime = mtime  # 毫秒
        self.depth = None
        self.absolute_path = f"D:/data/lib/{relative_path}"
        self.indexed_at = 0
        self.generation = 1
        self.materialized_seq = 0


# browser/files 旧行字段集合（_build_search_entry_from_index 旧实现输出，
# 另有 index 分支循环里追加的 size_status/index_refresh_pending/browse_via_index）
BROWSER_FILE_ROW_FIELDS = {
    "id", "name", "path", "relative_path", "parent_path", "rjcode",
    "size", "size_status", "modified_time", "unzip_time", "is_directory",
    "library_id", "library_name", "file_count", "folder_count",
    "folder_count_status", "size_via_index",
    "search_hit", "search_via_index",
    "index_refresh_pending", "browse_via_index",
}

# folder-contents / list-folders 旧行字段集合（_folder_content_item_from_index 旧实现输出；
# 注意：旧实现该行**没有** size_via_index / index_refresh_pending —— 不新增字段）
FOLDER_ROW_FIELDS = {
    "id", "name", "path", "relative_path", "size", "size_status",
    "modified_time", "type", "is_directory", "has_children", "children_loaded",
    "file_count", "folder_count", "folder_count_status", "browse_via_index",
}


def _manager_for_constructors() -> LibraryManager:
    """绕过 __init__ 的重依赖，只取方法。"""
    manager = LibraryManager.__new__(LibraryManager)
    return manager


def test_browser_file_row_contract_local_dir():
    """本地库目录行：字段集合 + 值语义与旧实现一致。"""
    manager = _manager_for_constructors()
    entry = FakeIndexEntry("RJ123", "dir", "RJ123", size=123456, file_count=4, mtime=1757000000000, rjcode="RJ123")
    row = manager._build_search_entry_from_index(
        _fake_library(), item_id=0, search_root="D:/data/lib", entry=entry
    )
    assert set(row.keys()) == BROWSER_FILE_ROW_FIELDS, set(row.keys()) ^ BROWSER_FILE_ROW_FIELDS
    assert row["id"] == "lib1:search:0"
    assert row["name"] == "RJ123"
    assert row["path"] == "D:/data/lib/RJ123"
    assert row["relative_path"] == "RJ123"
    assert row["parent_path"] == "D:/data/lib"      # 本地分支：os.path.dirname(full_path)
    assert row["rjcode"] == "RJ123"
    assert row["size"] == 123456                     # 目录累计大小来自索引
    assert row["size_status"] == "ready"
    assert row["is_directory"] is True
    assert row["modified_time"] == row["unzip_time"] # 旧实现两者同源
    assert row["file_count"] == 4
    assert row["folder_count"] is None               # 目录 folder_count=None
    assert row["folder_count_status"] == "lazy"
    assert row["size_via_index"] is True
    assert row["search_hit"] is True and row["search_via_index"] is True
    assert row["browse_via_index"] is True


def test_browser_file_row_contract_local_file():
    """本地库文件行：file_count=1 / folder_count=0 / folder_count_status=ready。"""
    manager = _manager_for_constructors()
    entry = FakeIndexEntry("a.7z.001", "file", "RJ123/a.7z.001", size=2097152, mtime=1757000000123)
    row = manager._build_search_entry_from_index(
        _fake_library(), item_id=3, search_root="D:/data/lib/RJ123", entry=entry
    )
    assert set(row.keys()) == BROWSER_FILE_ROW_FIELDS
    assert row["relative_path"] == "RJ123/a.7z.001" or row["relative_path"].endswith("a.7z.001")
    assert row["size"] == 2097152
    assert row["size_status"] == "ready"
    assert row["is_directory"] is False
    assert row["file_count"] == 1
    assert row["folder_count"] == 0
    assert row["folder_count_status"] == "ready"
    assert row["size_via_index"] is False


def test_browser_file_row_contract_synology_dir():
    """synology 目录行：size=None / size_status=disabled（旧实现特例）。"""
    manager = _manager_for_constructors()
    entry = FakeIndexEntry("RJ123", "dir", "RJ123", size=999, file_count=4, mtime=1757000000000)
    row = manager._build_search_entry_from_index(
        _fake_library(lib_type="synology_filestation"), item_id=1, search_root="/volume1/lib", entry=entry
    )
    assert set(row.keys()) == BROWSER_FILE_ROW_FIELDS
    assert row["size"] is None
    assert row["size_status"] == "disabled"
    assert row["is_directory"] is True
    assert row["size_via_index"] is False


def test_folder_row_contract_dir_with_descendants():
    """folder-contents/list-folders 目录行：has_children / folder_count=descendant 计数。"""
    manager = _manager_for_constructors()
    entry = FakeIndexEntry("RJ123", "dir", "RJ123", size=999, file_count=4, mtime=1757000000000)
    row = manager._folder_content_item_from_index(
        _fake_library(),
        entry,
        item_id=2,
        parent_relative_path="",
        descendant_folder_count=2,
    )
    assert set(row.keys()) == FOLDER_ROW_FIELDS
    assert row["id"] == "lib1:content:index:2"
    assert row["name"] == "RJ123"
    assert row["path"] == "D:/data/lib/RJ123"
    assert row["relative_path"] == "RJ123"
    assert row["size"] == 999
    assert row["size_status"] == "ready"
    assert row["type"] == "dir"
    assert row["is_directory"] is True
    assert row["has_children"] is True
    assert row["children_loaded"] is False
    assert row["file_count"] == 4
    assert row["folder_count"] == 2
    assert row["folder_count_status"] == "ready"
    assert row["browse_via_index"] is True


def test_folder_row_contract_file_and_lazy_dir():
    """文件行 + folder_count 未知（lazy）的目录行。"""
    manager = _manager_for_constructors()
    file_entry = FakeIndexEntry("track01.wav", "file", "RJ123/track01.wav", size=5, mtime=1757000000000)
    file_row = manager._folder_content_item_from_index(
        _fake_library(), file_entry, item_id=0, parent_relative_path="RJ123", descendant_folder_count=0,
    )
    assert set(file_row.keys()) == FOLDER_ROW_FIELDS
    assert file_row["type"] == "file"
    assert file_row["has_children"] is False
    assert file_row["children_loaded"] is True
    assert file_row["file_count"] == 1
    assert file_row["folder_count"] == 0
    assert file_row["folder_count_status"] == "ready"

    lazy_dir = FakeIndexEntry("empty", "dir", "empty", size=0, file_count=0, mtime=1757000000000)
    lazy_row = manager._folder_content_item_from_index(
        _fake_library(), lazy_dir, item_id=1, parent_relative_path="", descendant_folder_count=None,
    )
    assert lazy_row["folder_count"] is None
    assert lazy_row["folder_count_status"] == "lazy"
    assert lazy_row["has_children"] is False   # file_count=0 且 folder_count=None→0
    assert lazy_row["children_loaded"] is True


def test_remote_files_row_contract_shape():
    """_list_remote_files 输出行字段集合（远程库 browse，不走索引，仅锁定形状防漂移）。"""
    expected_fields = {
        "id", "name", "path", "rjcode", "size", "modified_time", "unzip_time",
        "is_directory", "library_id", "library_name",
    }
    # 直接验证契约字段集未被本阶段触碰：从旧实现字面量推导，若未来改动会在此报警
    import inspect

    source = inspect.getsource(LibraryManager._list_remote_files)
    for field in expected_fields:
        assert f'"{field}"' in source, field


def test_listing_view_roundtrip_legacy_rows():
    """统一形状 ↔ 旧行 往返一致性：legacy 行再次归一化不丢关键字段。"""
    manager = _manager_for_constructors()
    entry = FakeIndexEntry("RJ123", "dir", "RJ123", size=123456, file_count=4, mtime=1757000000000, rjcode="RJ123")
    row = manager._build_search_entry_from_index(
        _fake_library(), item_id=0, search_root="D:/data/lib", entry=entry
    )
    # 旧行可以被统一序列化层理解（keep_optional 通道）—— 这是薄适配器的理论闭环
    canonical = normalize_listing_item(
        {
            "name": row["name"],
            "is_dir": row["is_directory"],
            "mtime": 1757000000.0,
            "relative_path": row["relative_path"],
            "raw_size": row["size"],
        },
        keep_optional=True,
    )
    assert canonical["name"] == "RJ123"
    assert canonical["is_dir"] is True
