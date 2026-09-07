"""KikoeruRatingFixService 单元测试（--noconftest 可独立运行，mock DLsite）

运行: cd backend && python -m pytest --noconftest tests/test_kikoeru_rating_fix_service.py -q
"""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import kikoeru_db_service as kdb  # noqa: E402
from app.core import kikoeru_rating_fix_service as rfs  # noqa: E402
from app.core.dlsite_service import DLsiteNetworkError  # noqa: E402
from app.core.kikoeru_rating_fix_service import (  # noqa: E402
    KikoeruRatingFixService,
    workno_candidates_from_work_id,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "kikoeru_test.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE t_work (id integer primary key autoincrement, title text not null,
            dir text not null, circle_id integer, lyric_status text not null default '',
            rate_count integer, rate_average_2dp float, rate_count_detail text,
            rank text, review_count integer, dl_count integer, price integer,
            is_custom_meta integer default 0);
        CREATE TABLE t_circle (id integer primary key autoincrement, name text not null);
        CREATE TABLE t_tag (id integer primary key autoincrement, name text not null);
        CREATE TABLE t_va (id text primary key, name text not null);
        CREATE TABLE r_tag_work (tag_id integer, work_id integer, primary key (tag_id, work_id));
        CREATE TABLE r_va_work (va_id text, work_id integer, primary key (va_id, work_id));
        CREATE TABLE t_review (user_name text, work_id integer, rating integer, primary key (user_name, work_id));
        CREATE TABLE t_play_histroy (user_name text, work_id integer, state text, primary key (user_name, work_id));
        CREATE TABLE t_translate_task (id integer primary key autoincrement, status integer);
        CREATE TABLE t_user (name text primary key, password text, "group" text);
        CREATE TABLE knex_migrations (id integer primary key autoincrement, name text);
        -- 1: 本体重抓有评分; 2: 本体 0 分但日文原版有评分; 3: 全版本 0 分; 4: dir 无编号
        INSERT INTO t_work (id, title, dir, rate_count, rate_average_2dp) VALUES
            (126662, '作品A', 'RJ126662 作品A', 0, 0.0),
            (234567, '作品B', 'RJ234567 作品B', 0, 0.0),
            (345678, '作品C', 'RJ345678 作品C', 0, 0.0),
            (456789, '作品D', '无编号文件夹', 0, NULL);
        """
    )
    conn.commit()
    conn.close()

    fake_cfg = SimpleNamespace(kikoeru_db=SimpleNamespace(
        enabled=True, db_path=str(db_path), backup_dir=str(backup_dir),
        backup_interval_hours=24.0, backup_retention=2, snapshot_retention=3,
        scan_listen_enabled=False, scan_poll_interval_minutes=30,
        scan_checkpoint='', last_scan_finished_at=''))
    monkeypatch.setattr(kdb, "get_config", lambda: fake_cfg)

    service = KikoeruRatingFixService()
    return SimpleNamespace(service=service, db_path=db_path, backup_dir=backup_dir)


def _rating(rate_count, average, rj=None):
    return {
        "rjcode": rj or "RJ000000", "dl_count": 100, "rank": [{"term": "day", "rank": 1}],
        "rate_count": rate_count, "rate_average_2dp": average,
        "rate_count_detail": [{"review_point": 5, "count": rate_count, "ratio": 100}],
        "review_count": 3, "price": 880,
    }


def _mock_dlsite(monkeypatch, rating_map, linked_map):
    """rating_map: {workno: rating_dict|None}; linked_map: {workno: {cand: SimpleNamespace}}"""
    dlsite = SimpleNamespace(
        get_product_rating=None,
        get_linked_works=None,
    )

    async def fake_rating(rjcode, locale=None):
        return rating_map.get(rjcode)

    async def fake_linked(rjcode, refresh=False):
        return linked_map.get(rjcode, {})

    dlsite.get_product_rating = fake_rating
    dlsite.get_linked_works = fake_linked
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)


def test_workno_candidates_from_vj_id():
    assert workno_candidates_from_work_id(2000001004045)[0] == "VJ01004045"
    assert workno_candidates_from_work_id(2000000015640)[0] == "VJ00015640"
    assert "VJ015640" in workno_candidates_from_work_id(2000000015640)
    assert workno_candidates_from_work_id(126662)[0] == "RJ00126662"
    # dir 优先时不会走到这里
    assert workno_candidates_from_work_id(0) == []


@pytest.mark.asyncio
async def test_preview_own_rescrape(env, monkeypatch):
    _mock_dlsite(monkeypatch, {"RJ126662": _rating(5, 4.5, "RJ126662")}, {})
    result = await env.service.preview_fix(ids=[126662])
    item = result["items"][0]
    assert item["plan"] == "own"
    assert item["fix"]["rate_average_2dp"] == 4.5
    assert result["fixable"] == 1


@pytest.mark.asyncio
async def test_preview_linked_japanese_original_priority(env, monkeypatch):
    """本体仍 0 分 → 抓关联版本；日文原版优先于翻译版。"""
    linked = {
        "RJ111111": SimpleNamespace(work_type="translation", lang="CHI"),
        "RJ222222": SimpleNamespace(work_type="original", lang="JPN"),
        "RJ333333": SimpleNamespace(work_type="translation", lang="CHI_HANS"),
    }
    rating_map = {
        # 本体仍 0
        "RJ234567": _rating(0, 0.0, "RJ234567"),
        # 原版有评分；翻译版也有（验证优先级）
        "RJ222222": _rating(67, 4.85, "RJ222222"),
        "RJ333333": _rating(20, 4.0, "RJ333333"),
    }
    _mock_dlsite(monkeypatch, rating_map, {"RJ234567": linked})
    result = await env.service.preview_fix(ids=[234567])
    item = result["items"][0]
    assert item["plan"] == "linked"
    assert item["source_rjcode"] == "RJ222222"
    assert item["source_lang"] == "JPN"
    assert item["source_work_type"] == "original"
    assert item["fix"]["rate_average_2dp"] == 4.85


@pytest.mark.asyncio
async def test_preview_none_when_all_zero(env, monkeypatch):
    linked = {"RJ444444": SimpleNamespace(work_type="original", lang="JPN")}
    _mock_dlsite(
        monkeypatch,
        {"RJ345678": _rating(0, 0.0), "RJ444444": _rating(0, 0.0)},
        {"RJ345678": linked},
    )
    result = await env.service.preview_fix(ids=[345678])
    item = result["items"][0]
    assert item["plan"] == "none"
    assert item["fix"] is None
    assert result["fixable"] == 0


@pytest.mark.asyncio
async def test_preview_dir_without_workno_uses_id_candidates(env, monkeypatch):
    """dir 无编号 → 用 id 推导候选（多形态依次尝试）。"""
    calls = []

    async def fake_rating(rjcode, locale=None):
        calls.append(rjcode)
        if rjcode == "RJ456789":
            return _rating(8, 4.2, "RJ456789")
        return None

    dlsite = SimpleNamespace(get_product_rating=fake_rating, get_linked_works=None)
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)
    result = await env.service.preview_fix(ids=[456789])
    item = result["items"][0]
    assert item["plan"] == "own"
    assert "RJ00456789" in calls  # zfill(8) 候选先试
    assert "RJ456789" in calls    # 原始形态兜底


@pytest.mark.asyncio
async def test_apply_fix_writes_fields(env, monkeypatch):
    _mock_dlsite(monkeypatch, {"RJ126662": _rating(1203, 4.63, "RJ126662")}, {})
    result = await env.service.apply_fix(ids=[126662])
    assert result["applied"] == 1

    row = kdb.KikoeruDbService().query_table("t_work", search="作品A")["rows"][0]
    assert row["rate_count"] == 1203
    assert row["rate_average_2dp"] == 4.63
    assert row["dl_count"] == 100
    assert row["review_count"] == 3
    assert row["price"] == 880
    import json
    assert json.loads(row["rate_count_detail"]) == [{"review_point": 5, "count": 1203, "ratio": 100}]
    assert json.loads(row["rank"]) == [{"term": "day", "rank": 1}]
    # 不自动改 is_custom_meta（评分由 Kikoeru 后续正常刷新）
    assert row["is_custom_meta"] == 0
    # 写前快照已生成
    assert len(kdb.KikoeruDbService().list_backups("snapshot")) >= 1


@pytest.mark.asyncio
async def test_perfect_score_with_original_lower_applies_original(env, monkeypatch):
    """重抓得满分 5 → 原版 4.85 → 不信任满分，套用日文原版评分。"""
    linked = {"RJ222222": SimpleNamespace(work_type="original", lang="JPN")}
    rating_map = {
        "RJ234567": _rating(5, 5.0, "RJ234567"),      # 翻译版 5 分（仅 5 评，存疑）
        "RJ222222": _rating(67, 4.85, "RJ222222"),    # 日文原版 4.85
    }
    _mock_dlsite(monkeypatch, rating_map, {"RJ234567": linked})
    result = await env.service.preview_fix(ids=[234567])
    item = result["items"][0]
    assert item["plan"] == "linked"
    assert item["source_rjcode"] == "RJ222222"
    assert item["fix"]["rate_average_2dp"] == 4.85
    assert "满分" in item["reason"]


@pytest.mark.asyncio
async def test_perfect_score_with_original_perfect_is_valid(env, monkeypatch):
    """重抓得满分 5 → 原版同为 5 → 满分有效，套用本体分。"""
    linked = {"RJ222222": SimpleNamespace(work_type="original", lang="JPN")}
    rating_map = {
        "RJ234567": _rating(9, 5.0, "RJ234567"),
        "RJ222222": _rating(300, 5.0, "RJ222222"),
    }
    _mock_dlsite(monkeypatch, rating_map, {"RJ234567": linked})
    result = await env.service.preview_fix(ids=[234567])
    item = result["items"][0]
    assert item["plan"] == "own"
    assert item["fix"]["rate_average_2dp"] == 5.0
    assert "满分有效" in item["reason"]


@pytest.mark.asyncio
async def test_perfect_score_original_unreachable_keeps_own(env, monkeypatch):
    """满分 5 且原版不可达 → 保留本体 5 分并标注未验证。"""
    _mock_dlsite(monkeypatch, {"RJ234567": _rating(5, 5.0, "RJ234567")}, {})
    result = await env.service.preview_fix(ids=[234567])
    item = result["items"][0]
    assert item["plan"] == "own"
    assert item["fix"]["rate_average_2dp"] == 5.0
    assert "未验证" in item["reason"]


@pytest.mark.asyncio
async def test_network_error_marks_error_not_none(env, monkeypatch):
    """网络失败（重试穷尽后仍异常）→ plan=error，绝不与"确认无评分"混淆。"""
    calls = []

    async def fake_rating(rjcode, locale=None):
        calls.append(rjcode)
        raise rfs.DLsiteNetworkError("连接超时")

    dlsite = SimpleNamespace(get_product_rating=fake_rating, get_linked_works=None)
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)
    result = await env.service.preview_fix(ids=[126662])
    item = result["items"][0]
    assert item["plan"] == "error"
    assert "网络失败" in item["reason"]
    assert result["error"] == 1
    assert result["fixable"] == 0
    # 作品级自动重试：attempts=3 → 同一 workno 打 3 次
    assert calls.count("RJ126662") == 3


@pytest.mark.asyncio
async def test_apply_skips_error_rows(env, monkeypatch):
    """apply 时 error 行自动跳过，applied=0。"""
    async def fake_rating(rjcode, locale=None):
        raise rfs.DLsiteNetworkError("连接超时")

    dlsite = SimpleNamespace(get_product_rating=fake_rating, get_linked_works=None)
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)
    result = await env.service.apply_fix(ids=[126662])
    assert result["applied"] == 0
    assert result["error"] == 1


@pytest.mark.asyncio
async def test_apply_skips_when_nothing_fixable(env, monkeypatch):
    _mock_dlsite(monkeypatch, {"RJ345678": _rating(0, 0.0)}, {})
    result = await env.service.apply_fix(ids=[345678])
    assert result["applied"] == 0
    assert result["skipped"] == 1
