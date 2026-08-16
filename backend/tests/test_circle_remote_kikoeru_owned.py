"""远程 kikoeru 收录兑底的单测（无 PG 依赖）。"""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.circle_completion_service import CircleCompletionService


def _make_service():
    return CircleCompletionService.__new__(CircleCompletionService)


def _make_row(canonical, linked=(), updated=None):
    return SimpleNamespace(
        canonical_rjcode=canonical,
        display_rjcode=canonical,
        linked_rjcodes=list(linked),
        is_bonus_work=False,
        updated_at=updated or datetime(2026, 1, 1),
    )


@pytest.mark.asyncio
async def test_remote_kikoeru_skipped_when_disabled(monkeypatch):
    service = _make_service()
    fake = SimpleNamespace(
        config=SimpleNamespace(enabled=False, server_url=""),
        check_duplicate=AsyncMock(),
    )
    monkeypatch.setattr("app.core.kikoeru_duplicate_service.get_kikoeru_service", lambda: fake)

    merged = {}
    await service._merge_remote_kikoeru_owned(
        merged,
        [_make_row("RJ01000001")],
        {"RJ01000001": {"RJ01000001"}},
    )

    assert merged == {}
    fake.check_duplicate.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_kikoeru_merges_found_works_only(monkeypatch):
    service = _make_service()

    async def fake_check(rjcode, use_cache=True, extra_match_rjcodes=None):
        if rjcode == "RJ01000001":
            return SimpleNamespace(is_found=True, matched_rjcode="RJ01000001", match_type="exact")
        return SimpleNamespace(is_found=False, matched_rjcode="", match_type="")

    fake = SimpleNamespace(
        config=SimpleNamespace(enabled=True, server_url="http://kikoeru:8088"),
        check_duplicate=AsyncMock(side_effect=fake_check),
    )
    monkeypatch.setattr("app.core.kikoeru_duplicate_service.get_kikoeru_service", lambda: fake)

    merged = {
        "RJ01000002": {
            "owned_rjcodes": {"RJ01000002"},
            "owned_paths": [],
            "primary_folder_path": "",
            "primary_library_id": "",
            "folder_count": 0,
            "folder_size": 0,
            "file_count": 0,
            "has_local_subtitles": False,
            "subtitle_file_count": 0,
            "subtitle_dir": "",
        },
    }
    rows = [
        _make_row("RJ01000001", updated=datetime(2026, 2, 1)),
        _make_row("RJ01000002", updated=datetime(2026, 2, 2)),
    ]
    await service._merge_remote_kikoeru_owned(
        merged,
        rows,
        {
            "RJ01000001": {"RJ01000001"},
            "RJ01000002": {"RJ01000002"},
        },
    )

    assert "RJ01000001" in merged, "远程 kikoeru 命中的作品应并入拥有态"
    assert merged["RJ01000001"]["owned_rjcodes"] == {"RJ01000001"}
    assert "RJ01000002" in merged
    # 本地已命中的作品不应再探测
    called = [call.args[0] for call in fake.check_duplicate.await_args_list]
    assert called == ["RJ01000001"]


@pytest.mark.asyncio
async def test_remote_kikoeru_merge_uses_linkage_canonicals(monkeypatch):
    """关联链命中时，同链 canonical 都归并到拥有态。"""
    service = _make_service()

    async def fake_check(rjcode, use_cache=True, extra_match_rjcodes=None):
        return SimpleNamespace(is_found=True, matched_rjcode="RJ01009999", match_type="linkage_match")

    fake = SimpleNamespace(
        config=SimpleNamespace(enabled=True, server_url="http://kikoeru:8088"),
        check_duplicate=AsyncMock(side_effect=fake_check),
    )
    monkeypatch.setattr("app.core.kikoeru_duplicate_service.get_kikoeru_service", lambda: fake)

    merged = {}
    await service._merge_remote_kikoeru_owned(
        merged,
        [_make_row("RJ01000001", linked=["RJ01000002"])],
        {
            "RJ01000001": {"RJ01000001", "RJ01000002"},
            "RJ01000002": {"RJ01000001", "RJ01000002"},
        },
    )

    assert merged["RJ01000001"]["owned_rjcodes"] == {"RJ01000001"}
    assert merged["RJ01000002"]["owned_rjcodes"] == {"RJ01000001"}


@pytest.mark.asyncio
async def test_remote_kikoeru_owned_for_items_fills_missing_only(monkeypatch):
    """单社团路径：只补录本地未命中的作品，已命中不重复探测。"""
    service = _make_service()

    async def fake_check(rjcode, use_cache=True, extra_match_rjcodes=None):
        if rjcode == "RJ01000001":
            return SimpleNamespace(is_found=True, matched_rjcode="RJ01000001", match_type="exact")
        return SimpleNamespace(is_found=False, matched_rjcode="", match_type="")

    fake = SimpleNamespace(
        config=SimpleNamespace(enabled=True, server_url="http://kikoeru:8088"),
        check_duplicate=AsyncMock(side_effect=fake_check),
    )
    monkeypatch.setattr("app.core.kikoeru_duplicate_service.get_kikoeru_service", lambda: fake)

    items = {
        "RJ01000001": {
            "canonical_rjcode": "RJ01000001",
            "linked_rjcodes": [],
            "kikoeru_found_rjcodes": [],
        },
        "RJ01000002": {
            "canonical_rjcode": "RJ01000002",
            "linked_rjcodes": [],
            "kikoeru_found_rjcodes": ["RJ01000002"],
        },
    }
    hits = await service._remote_kikoeru_owned_for_items(items, [])

    assert hits == {"RJ01000001": "RJ01000001"}
    assert items["RJ01000001"]["kikoeru_found_rjcodes"] == ["RJ01000001"]
    called = [call.args[0] for call in fake.check_duplicate.await_args_list]
    assert called == ["RJ01000001"], "已命中的作品不应再探测"
