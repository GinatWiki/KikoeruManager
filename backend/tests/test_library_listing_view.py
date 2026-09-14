"""库存浏览统一序列化层的单元测试（重构阶段 1）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.library_index.listing_view import (  # noqa: E402
    LISTING_ITEM_FIELDS,
    LISTING_ITEM_OPTIONAL_FIELDS,
    build_listing_response,
    legacy_browser_file_row,
    legacy_folder_row,
    normalize_listing_item,
    normalize_listing_items,
)


def test_normalize_accepts_multiple_field_spellings():
    file_row = normalize_listing_item(
        {"file_name": "RJ01675713.7z.001", "size_bytes": 2097152000, "modified_at": 1757000000},
        base_relative_path="RJ016xxxxx",
    )
    assert file_row is not None
    assert file_row["name"] == "RJ01675713.7z.001"
    assert file_row["is_dir"] is False
    assert file_row["size"] == 2097152000
    assert file_row["relative_path"] == "RJ016xxxxx/RJ01675713.7z.001"
    assert file_row["mtime"] == 1757000000.0
    assert file_row["child_count"] == -1
    assert file_row["stale"] is False
    assert file_row["source"] == "index"

    dir_row = normalize_listing_item({"name": "早期限定335大特典", "is_folder": True, "child_total": 4})
    assert dir_row is not None
    assert dir_row["is_dir"] is True
    assert dir_row["size"] == 0
    assert dir_row["child_count"] == 4
    assert dir_row["relative_path"] == "早期限定335大特典"


def test_normalize_handles_nanosecond_mtime_and_path_normalization():
    row = normalize_listing_item(
        {"name": "a.bin", "mtime_ns": 1757000000_000000000, "path": "\\sub\\dir\\a.bin"}
    )
    assert row is not None
    assert row["mtime"] == 1757000000.0
    assert row["relative_path"] == "sub/dir/a.bin"


def test_normalize_skips_unrecognizable_rows_and_keeps_order():
    items = normalize_listing_items(
        [
            {"name": "b", "is_dir": False},
            "not-a-dict",
            {"no_name": 1},
            {"name": "a", "is_dir": True},
        ]
    )
    assert [item["name"] for item in items] == ["b", "a"]
    assert all(set(item.keys()) == set(LISTING_ITEM_FIELDS) for item in items)


def test_build_listing_response_marks_source_and_metadata():
    payload = build_listing_response(
        [{"name": "a", "is_dir": False, "size": 3}],
        source="verify",
        base_relative_path="root",
        generation=42,
        fresh_at="2026-09-14T13:30:00",
        cursor="next-1",
        has_more=True,
        verify_failed=True,
    )
    assert payload["source"] == "verify"
    assert payload["generation"] == 42
    assert payload["fresh_at"] == "2026-09-14T13:30:00"
    assert payload["cursor"] == "next-1"
    assert payload["has_more"] is True
    assert payload["verify_failed"] is True
    assert payload["items"][0]["relative_path"] == "root/a"
    assert payload["items"][0]["source"] == "verify"


def test_normalize_keep_optional_preserves_legacy_fields():
    """keep_optional=True 收编旧端点扩展字段；None 保持 None（未知 ≠ 0）。"""
    raw = {
        "name": "RJ123",
        "is_dir": True,
        "rjcode": "RJ123",
        "size_status": "ready",
        "file_count": 3,
        "folder_count": None,          # 未知：必须保持 None，不能被钳成 0
        "has_children": None,
        "children_loaded": "true",     # 字符串 bool 归一化
        "size_via_index": 1,
        "browse_via_index": True,
        "index_refresh_pending": False,
        "absolute_path": "/data/lib/RJ123",
        "modified_time": "2026-09-14T13:00:00",
        "folder_count_status": "lazy",
    }
    row = normalize_listing_item(raw, keep_optional=True)
    # 语义：raw 里出现的可选字段都会带上（raw 没出现的不会凭空出现）
    for field in LISTING_ITEM_OPTIONAL_FIELDS:
        if field == "unzip_time" or field == "raw_size":
            continue
        assert field in row, field
    assert row["file_count"] == 3
    assert row["folder_count"] is None
    assert row["children_loaded"] is True
    assert row["size_via_index"] is True

    # 默认（新端点路径）不带可选字段：8 字段严格不变
    plain = normalize_listing_item(raw)
    assert set(plain.keys()) == set(LISTING_ITEM_FIELDS)


def test_legacy_browser_file_row_field_mapping():
    """canonical → browser/files 旧行：字段名与值语义逐字段对齐旧实现。"""
    canonical = normalize_listing_item(
        {
            "name": "RJ123",
            "is_dir": True,
            "mtime": 1757000000.0,
            "relative_path": "RJ123",
            "file_count": 4,
            "rjcode": "RJ123",
            "modified_time": "2026-09-04T18:13:20",
            "size_status": "ready",
            "raw_size": 123456,   # 目录累计大小（canonical.size 会被钳成 0）
        },
        keep_optional=True,
    )
    row = legacy_browser_file_row(canonical)
    assert row["name"] == "RJ123"
    assert row["is_directory"] is True
    assert row["size"] == 123456            # raw_size 优先透传
    assert row["size_status"] == "ready"
    assert row["modified_time"] == "2026-09-04T18:13:20"
    assert row["unzip_time"] == row["modified_time"]  # 旧实现 unzip_time == mtime_iso
    assert row["relative_path"] == "RJ123"
    assert row["rjcode"] == "RJ123"
    assert row["file_count"] == 4
    assert row["folder_count"] is None      # 旧实现目录 folder_count=None

    # synology 目录：raw_size=None → size=None（未知）
    remote = legacy_browser_file_row({"name": "d", "is_dir": True, "raw_size": None})
    assert remote["size"] is None

    # 文件行 fallback：无 modified_time 时从秒级 mtime 派生 ISO（本地时区）
    f = legacy_browser_file_row({"name": "a.7z", "is_dir": False, "mtime": 1757000000.0, "size": 9})
    assert f["size"] == 9
    assert f["modified_time"] is not None and f["modified_time"].endswith(":20")  # 秒级精度
    assert "T" in f["modified_time"]
    assert f["folder_count"] is None


def test_legacy_folder_row_field_mapping():
    """canonical → folder-contents/list-folders 旧行（超集行）。"""
    canonical = normalize_listing_item(
        {
            "name": "RJ123",
            "is_dir": True,
            "mtime": 1757000000.0,
            "relative_path": "RJ123",
            "file_count": 4,
            "folder_count": 2,
            "has_children": True,
            "children_loaded": False,
            "absolute_path": "/data/lib/RJ123",
            "raw_size": 999,
        },
        keep_optional=True,
    )
    row = legacy_folder_row(canonical)
    assert row["name"] == "RJ123"
    assert row["path"] == "/data/lib/RJ123"   # absolute_path → path
    assert row["relative_path"] == "RJ123"
    assert row["size"] == 999
    assert row["type"] == "dir"
    assert row["is_directory"] is True
    assert row["has_children"] is True
    assert row["children_loaded"] is False
    assert row["file_count"] == 4
    assert row["folder_count"] == 2
    assert row["folder_count_status"] == "ready"

    # 目录未提供 has_children/file_count 时按派生规则兜底
    derived = legacy_folder_row({"name": "empty-dir", "is_dir": True, "file_count": 0, "folder_count": 0})
    assert derived["has_children"] is False
    assert derived["children_loaded"] is True
    assert derived["file_count"] == 0
    # 文件行：has_children=False / file_count=1
    f = legacy_folder_row({"name": "a.wav", "is_dir": False, "size": 5})
    assert f["type"] == "file"
    assert f["has_children"] is False
    assert f["children_loaded"] is True
    assert f["file_count"] == 1
    assert f["folder_count"] == 0
