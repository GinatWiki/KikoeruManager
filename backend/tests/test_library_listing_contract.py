"""库存 listing 端点契约快照测试（重构阶段 1 第二步）。

只测「新 listing 端点的统一响应形状」，不连数据库、不真扫盘：
- 用 FakeService 注入 service.list_children_page（browser/files 本地索引分支背后真实调用的方法）。
- 断言响应字段集合 ⊇ 契约 6 字段，且 items[i] 字段集合 == 契约 8 字段。
- 断言 mode=verify 本阶段等价于 index 并正确回显 mode/source。
- 断言 relative_path 根目录归一化为空 parent_path。

关于 4 个旧端点（browser/files、library/files、folder-contents、list-folders）：
本阶段不改它们，因此这里没有"旧结构快照"——它们的输出结构与统一形状差异过大
（绝对路径、ISO mtime、size_status、has_children、递归 fs walk、DB 解压时间等字段在
统一形状中不存在），强行映射会破坏前端契约。跳过依据见交付报告；旧端点保持原样即零回归。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.library_index import listing_service  # noqa: E402
from app.core.library_index.listing_view import LISTING_ITEM_FIELDS  # noqa: E402

CANONICAL_RESPONSE_KEYS = {"items", "source", "generation", "fresh_at", "cursor", "has_more"}


class FakeEntry:
    """极简 IndexEntry 替身，仅带 listing 需要的字段。"""

    def __init__(self, name, entry_type, relative_path, size=0, file_count=0, mtime=None):
        self.library_id = "lib1"
        self.entry_type = entry_type
        self.relative_path = relative_path
        self.name = name
        self.rjcode = None
        self.parent_path = ""
        self.size = size
        self.file_count = file_count
        self.mtime = mtime  # 毫秒
        self.depth = None
        self.absolute_path = ""
        self.indexed_at = 0
        self.generation = 1
        self.materialized_seq = 0


class FakeStatus:
    active_generation = 7
    updated_at = 1757000000000  # 毫秒


class FakeService:
    def __init__(self, entries, next_cursor=None):
        self._entries = entries
        self._next_cursor = next_cursor
        self.captured_parent_path = None

    def list_children_page(self, library_id, parent_path, **kwargs):
        self.captured_parent_path = parent_path
        return {
            "entries": self._entries,
            "total": len(self._entries),
            "next_page_cursor": self._next_cursor,
            "used_page_cursor": False,
        }

    def get_status(self, library_id):
        return FakeStatus()


def _fake_library(lib_type="local", root_path="/data/lib", browse_root_path=None):
    return SimpleNamespace(
        id="lib1", type=lib_type, root_path=root_path, browse_root_path=browse_root_path
    )


def test_listing_response_matches_contract_shape():
    entries = [
        FakeEntry("RJ01675713", "dir", "RJ01675713", size=123, file_count=4, mtime=1757000000000),
        FakeEntry("a.7z", "file", "RJ01675713/a.7z", size=2097152, mtime=1757000000123),
    ]
    svc = FakeService(entries, next_cursor="cur2")
    data = listing_service.build_library_listing(
        library=_fake_library(), relative_path="", cursor="", mode="index", get_service=lambda: svc
    )

    # 契约必含字段都在
    assert CANONICAL_RESPONSE_KEYS.issubset(data.keys()), data.keys()
    assert data["source"] == "index"
    assert data["generation"] == 7
    assert data["cursor"] == "cur2"
    assert data["has_more"] is True
    assert len(data["items"]) == 2

    # items 字段集合严格等于契约 8 字段
    for item in data["items"]:
        assert set(item.keys()) == set(LISTING_ITEM_FIELDS), item.keys()

    d = data["items"][0]
    assert d["name"] == "RJ01675713" and d["is_dir"] is True
    assert d["size"] == 0 and d["child_count"] == 4  # 目录 size 恒为 0（契约规定）
    assert d["mtime"] == 1757000000.0  # 毫秒 -> 秒
    assert d["relative_path"] == "RJ01675713"
    assert d["stale"] is False and d["source"] == "index"

    f = data["items"][1]
    assert f["is_dir"] is False and f["size"] == 2097152 and f["child_count"] == -1


def test_listing_verify_mode_echoes_but_equals_index():
    entries = [FakeEntry("x", "file", "sub/x", size=1, mtime=1757000000000)]
    svc = FakeService(entries)
    data = listing_service.build_library_listing(
        library=_fake_library(), relative_path="sub", cursor="", mode="verify", get_service=lambda: svc
    )
    assert data["mode"] == "verify"
    # verify 对 FakeEntry stat 真实磁盘（absolute_path 为空 → 拼库根），OSError 回落快照
    assert data["source"] == "verify"
    assert data["verify_failed"] is True
    assert data["items"][0]["relative_path"] == "sub/x"
    assert data["items"][0]["source"] == "verify"


def test_listing_relative_path_root_maps_to_empty_parent():
    svc = FakeService([])
    listing_service.build_library_listing(
        library=_fake_library(), relative_path="/", cursor="", mode="index", get_service=lambda: svc
    )
    assert svc.captured_parent_path == ""
    listing_service.build_library_listing(
        library=_fake_library(), relative_path="a/b", cursor="", mode="index", get_service=lambda: svc
    )
    assert svc.captured_parent_path == "a/b"


# ---------------------------------------------------------------------------
# 阶段 2：mode=verify 浅扫（真目录 + tmp_path，不连库）
# ---------------------------------------------------------------------------


def _make_disk_tree(root):
    """建一个磁盘树：RJ1/(a.wav=100B, b.jpg=200B) + stray_new/(c.wav)。"""
    rj = root / "RJ1"
    rj.mkdir(parents=True)
    (rj / "a.wav").write_bytes(b"x" * 100)
    (rj / "b.jpg").write_bytes(b"y" * 200)
    stray = root / "stray_new"
    stray.mkdir()
    (stray / "c.wav").write_bytes(b"z" * 50)


def test_verify_marks_size_mismatch_stale(tmp_path):
    disk_root = tmp_path / "lib"
    _make_disk_tree(disk_root)
    # 快照声称 a.wav 是 999 字节，磁盘是 100 → stale；目录 size 滞后允许
    entries = [
        FakeEntry("RJ1", "dir", "RJ1", size=300, file_count=2, mtime=0),
        FakeEntry("a.wav", "file", "RJ1/a.wav", size=999, mtime=0),
        FakeEntry("b.jpg", "file", "RJ1/b.jpg", size=200, mtime=0),
    ]
    svc = FakeService(entries)
    lib = _fake_library(root_path=str(disk_root))
    data = listing_service.build_library_listing(
        library=lib, relative_path="RJ1", mode="verify", get_service=lambda: svc
    )
    assert data["mode"] == "verify" and data["source"] == "verify"
    assert not data.get("verify_failed")
    by_name = {i["name"]: i for i in data["items"]}
    assert by_name["a.wav"]["stale"] is True
    assert by_name["b.jpg"]["stale"] is False
    assert by_name["RJ1"]["stale"] is False  # 目录 size 允许滞后


def test_verify_appends_disk_only_and_drops_ghosts(tmp_path):
    disk_root = tmp_path / "lib"
    _make_disk_tree(disk_root)
    entries = [
        # RJ1 快照只有 a.wav；b.jpg 是磁盘新文件 → disk_only
        FakeEntry("RJ1", "dir", "RJ1", size=0, file_count=1, mtime=0),
        FakeEntry("a.wav", "file", "RJ1/a.wav", size=100, mtime=0),
        # ghost_file 磁盘上已删除 → 幽灵条目，不进响应
        FakeEntry("ghost_file", "file", "RJ1/ghost_file", size=1, mtime=0),
    ]
    svc = FakeService(entries)
    lib = _fake_library(root_path=str(disk_root))
    data = listing_service.build_library_listing(
        library=lib, relative_path="RJ1", mode="verify", get_service=lambda: svc
    )
    names = [i["name"] for i in data["items"]]
    assert "ghost_file" not in names, names          # 幽灵条目被剔除
    assert "b.jpg" in names                          # 磁盘新文件补进来
    b = next(i for i in data["items"] if i["name"] == "b.jpg")
    assert b["source"] == "verify" and b["stale"] is True
    assert b["size"] == 200 and b["is_dir"] is False
    assert b["relative_path"] == "RJ1/b.jpg"


def test_verify_root_lists_disk_only_top_level(tmp_path):
    disk_root = tmp_path / "lib"
    _make_disk_tree(disk_root)
    # 快照只有 RJ1；stray_new 是磁盘新目录
    entries = [FakeEntry("RJ1", "dir", "RJ1", size=0, file_count=2, mtime=0)]
    svc = FakeService(entries)
    lib = _fake_library(root_path=str(disk_root))
    data = listing_service.build_library_listing(
        library=lib, relative_path="", mode="verify", get_service=lambda: svc
    )
    names = {i["name"] for i in data["items"]}
    assert names == {"RJ1", "stray_new"}


def test_verify_fails_when_dir_unreachable(tmp_path):
    disk_root = tmp_path / "lib"
    _make_disk_tree(disk_root)
    entries = [FakeEntry("RJ1", "dir", "RJ1", size=0, file_count=2, mtime=0)]
    svc = FakeService(entries)
    lib = _fake_library(root_path=str(disk_root))
    data = listing_service.build_library_listing(
        library=lib, relative_path="not_exist_dir", mode="verify", get_service=lambda: svc
    )
    # 当前层不可达 → 回落 index：纯快照原样返回（无 stale 标记、无 disk_only），标 verify_failed
    assert data["verify_failed"] is True
    assert [i["name"] for i in data["items"]] == ["RJ1"]
    assert all(i["stale"] is False for i in data["items"])


def test_verify_skipped_for_remote_library():
    entries = [FakeEntry("x", "file", "sub/x", size=1, mtime=0)]
    svc = FakeService(entries)
    lib = _fake_library(lib_type="smb", root_path="/mnt/lib")
    data = listing_service.build_library_listing(
        library=lib, relative_path="sub", mode="verify", get_service=lambda: svc
    )
    # 非本地库存：verify 不做 stat，等价 index 读
    assert data["source"] == "verify"
    assert not data.get("verify_failed")
    assert data["items"][0]["relative_path"] == "sub/x"
