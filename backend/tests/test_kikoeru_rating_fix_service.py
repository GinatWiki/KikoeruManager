"""KikoeruRatingFixService 单元测试（--noconftest 可独立运行，mock DLsite）

两段式接口：
- list_fix_targets：纯 SQL 名单（零 DLsite 请求）
- start_run / _run_fix_impl：确认后才逐个抓取并分批写库

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
            is_custom_meta integer default 0, memo text);
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
        -- 126662: 本体重抓有评分; 234567: 本体 0 分但日文原版有评分; 345678: 全版本 0 分; 456789: dir 无编号
        INSERT INTO t_work (id, title, dir, rate_count, rate_average_2dp) VALUES
            (126662, '作品A', 'RJ126662 作品A', 0, 0.0),
            (234567, '作品B', 'RJ234567 作品B', 0, 0.0),
            (345678, '作品C', 'RJ345678 作品C', 0, 0.0),
            (456789, '作品D', '无编号文件夹', 0, NULL);
        """
    )
    conn.commit()
    conn.close()

    fake_cfg = SimpleNamespace(
        rename=SimpleNamespace(template="{rjcode} {work_name}"),
        kikoeru_db=SimpleNamespace(
        enabled=True, db_path=str(db_path), backup_dir=str(backup_dir),
        backup_interval_hours=24.0, backup_retention=2, snapshot_retention=3,
        scan_listen_enabled=False, scan_poll_interval_minutes=30,
        scan_checkpoint='', last_scan_finished_at=''))
    monkeypatch.setattr(kdb, "get_config", lambda: fake_cfg)

    service = KikoeruRatingFixService()
    # 回归：v2.6.3 误删模块级 _service 单例变量导致 get_run_status 抛 NameError
    monkeypatch.setattr(rfs, "_service", None)
    singleton = rfs.get_kikoeru_rating_fix_service()
    assert singleton.get_run_status()["running"] is False
    return SimpleNamespace(service=service, db_path=db_path, backup_dir=backup_dir)


def _rating(rate_count, average, rj=None):
    return {
        "rjcode": rj or "RJ000000", "dl_count": 100, "rank": [{"term": "day", "rank": 1}],
        "rate_count": rate_count, "rate_average_2dp": average,
        "rate_count_detail": [{"review_point": 5, "count": rate_count, "ratio": 100}],
        "review_count": 3, "price": 880,
    }


def _mock_dlsite(monkeypatch, rating_map, linked_map):
    """rating_map: {workno: rating_dict|None}；linked_map: {workno: {cand: SimpleNamespace}}"""
    calls = {"rating": [], "linked": []}

    async def fake_rating(rjcode, locale=None):
        calls["rating"].append(rjcode)
        return rating_map.get(rjcode)

    async def fake_linked(rjcode, refresh=False):
        calls["linked"].append(rjcode)
        return linked_map.get(rjcode, {})

    dlsite = SimpleNamespace(get_product_rating=fake_rating, get_linked_works=fake_linked)
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)
    return calls


def test_workno_candidates_from_vj_id():
    assert workno_candidates_from_work_id(2000001004045)[0] == "VJ01004045"
    assert workno_candidates_from_work_id(2000000015640)[0] == "VJ00015640"
    assert "VJ015640" in workno_candidates_from_work_id(2000000015640)
    assert workno_candidates_from_work_id(126662)[0] == "RJ00126662"
    assert workno_candidates_from_work_id(0) == []


# ------------------------------------------------------------ 第一步：名单（零网络请求）
@pytest.mark.asyncio
async def test_list_fix_targets_zero_requests(env, monkeypatch):
    calls = _mock_dlsite(monkeypatch, {}, {})
    result = await env.service.list_fix_targets()
    assert result["total"] == 4
    assert result["zero"] == 4
    assert result["perfect"] == 0
    # 名单阶段绝不触网
    assert calls["rating"] == [] and calls["linked"] == []


# ------------------------------------------------------------ 第二步：执行
@pytest.mark.asyncio
async def test_run_own_rescrape(env, monkeypatch):
    _mock_dlsite(monkeypatch, {"RJ126662": _rating(5, 4.5, "RJ126662")}, {})
    await env.service.start_run(ids=[126662])
    await env.service._run_task
    status = env.service.get_run_status()
    assert status["running"] is False
    assert status["applied"] == 1
    row = kdb.KikoeruDbService().query_table("t_work", search="作品A")["rows"][0]
    assert row["rate_average_2dp"] == 4.5
    assert row["is_custom_meta"] == 0  # 不冻结 Kikoeru 的元数据刷新
    assert len(kdb.KikoeruDbService().list_backups("snapshot")) >= 1


@pytest.mark.asyncio
async def test_run_linked_japanese_original_priority(env, monkeypatch):
    """本体仍 0 分 → 抓关联版本；日文原版优先于翻译版。"""
    linked = {
        "RJ111111": SimpleNamespace(work_type="translation", lang="CHI"),
        "RJ222222": SimpleNamespace(work_type="original", lang="JPN"),
        "RJ333333": SimpleNamespace(work_type="translation", lang="CHI_HANS"),
    }
    rating_map = {
        "RJ234567": _rating(0, 0.0, "RJ234567"),
        "RJ222222": _rating(67, 4.85, "RJ222222"),
        "RJ333333": _rating(20, 4.0, "RJ333333"),
    }
    _mock_dlsite(monkeypatch, rating_map, {"RJ234567": linked})
    await env.service.start_run(ids=[234567])
    await env.service._run_task
    status = env.service.get_run_status()
    assert status["applied"] == 1
    result_row = next(r for r in status["results"] if r["id"] == 234567)
    assert result_row["source_rjcode"] == "RJ222222"
    assert result_row["source_lang"] == "JPN"
    assert result_row["source_work_type"] == "original"
    row = kdb.KikoeruDbService().query_table("t_work", search="作品B")["rows"][0]
    assert row["rate_average_2dp"] == 4.85


@pytest.mark.asyncio
async def test_run_none_when_all_zero(env, monkeypatch):
    linked = {"RJ444444": SimpleNamespace(work_type="original", lang="JPN")}
    _mock_dlsite(
        monkeypatch,
        {"RJ345678": _rating(0, 0.0), "RJ444444": _rating(0, 0.0)},
        {"RJ345678": linked},
    )
    await env.service.start_run(ids=[345678])
    await env.service._run_task
    status = env.service.get_run_status()
    assert status["applied"] == 0
    assert status["none"] == 1
    row = kdb.KikoeruDbService().query_table("t_work", search="作品C")["rows"][0]
    assert row["rate_average_2dp"] == 0.0


@pytest.mark.asyncio
async def test_run_dir_without_workno_uses_id_candidates(env, monkeypatch):
    """dir 无编号 → 用 id 推导候选（多形态依次尝试）。"""
    calls = {"rating": []}

    async def fake_rating(rjcode, locale=None):
        calls["rating"].append(rjcode)
        if rjcode == "RJ456789":
            return _rating(8, 4.2, "RJ456789")
        return None

    dlsite = SimpleNamespace(get_product_rating=fake_rating, get_linked_works=None)
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)
    await env.service.start_run(ids=[456789])
    await env.service._run_task
    assert "RJ00456789" in calls["rating"]  # zfill(8) 候选先试
    assert "RJ456789" in calls["rating"]    # 原始形态兜底
    row = kdb.KikoeruDbService().query_table("t_work", search="作品D")["rows"][0]
    assert row["rate_average_2dp"] == 4.2


# ------------------------------------------------------------ 满分规则
@pytest.mark.asyncio
async def test_perfect_score_with_original_lower_applies_original(env, monkeypatch):
    """重抓得满分 5 → 原版 4.85 → 不信任满分，套用日文原版评分。"""
    linked = {"RJ222222": SimpleNamespace(work_type="original", lang="JPN")}
    rating_map = {
        "RJ234567": _rating(5, 5.0, "RJ234567"),
        "RJ222222": _rating(67, 4.85, "RJ222222"),
    }
    _mock_dlsite(monkeypatch, rating_map, {"RJ234567": linked})
    await env.service.start_run(ids=[234567])
    await env.service._run_task
    result_row = next(r for r in env.service.get_run_status()["results"] if r["id"] == 234567)
    assert result_row["plan"] == "linked"
    assert result_row["new_rate_average_2dp"] == 4.85
    assert "满分" in result_row["reason"]


@pytest.mark.asyncio
async def test_perfect_score_with_original_perfect_is_valid(env, monkeypatch):
    """重抓得满分 5 → 原版同为 5 → 满分有效，套用本体分。"""
    linked = {"RJ222222": SimpleNamespace(work_type="original", lang="JPN")}
    rating_map = {
        "RJ234567": _rating(9, 5.0, "RJ234567"),
        "RJ222222": _rating(300, 5.0, "RJ222222"),
    }
    _mock_dlsite(monkeypatch, rating_map, {"RJ234567": linked})
    await env.service.start_run(ids=[234567])
    await env.service._run_task
    result_row = next(r for r in env.service.get_run_status()["results"] if r["id"] == 234567)
    assert result_row["plan"] == "own"
    assert result_row["new_rate_average_2dp"] == 5.0
    assert "满分有效" in result_row["reason"]


@pytest.mark.asyncio
async def test_perfect_score_original_unreachable_keeps_own(env, monkeypatch):
    """满分 5 且原版不可达 → 保留本体 5 分并标注未验证。"""
    _mock_dlsite(monkeypatch, {"RJ234567": _rating(5, 5.0, "RJ234567")}, {})
    await env.service.start_run(ids=[234567])
    await env.service._run_task
    result_row = next(r for r in env.service.get_run_status()["results"] if r["id"] == 234567)
    assert result_row["plan"] == "own"
    assert result_row["new_rate_average_2dp"] == 5.0
    assert "未验证" in result_row["reason"]


# ------------------------------------------------------------ 网络失败
@pytest.mark.asyncio
async def test_network_error_marks_error_not_none(env, monkeypatch):
    """网络失败（重试穷尽后仍异常）→ plan=error，绝不与"确认无评分"混淆。"""
    calls = []

    async def fake_rating(rjcode, locale=None):
        calls.append(rjcode)
        raise DLsiteNetworkError("连接超时")

    dlsite = SimpleNamespace(get_product_rating=fake_rating, get_linked_works=None)
    monkeypatch.setattr(rfs, "get_dlsite_service", lambda: dlsite)
    await env.service.start_run(ids=[126662])
    await env.service._run_task
    status = env.service.get_run_status()
    assert status["error"] == 1
    assert status["applied"] == 0
    result_row = status["results"][0]
    assert result_row["plan"] == "error"
    assert "网络失败" in result_row["reason"]
    assert calls.count("RJ126662") == 3  # 作品级自动重试 3 次


@pytest.mark.asyncio
async def test_run_skip_when_nothing_fixable(env, monkeypatch):
    _mock_dlsite(monkeypatch, {"RJ345678": _rating(0, 0.0)}, {})
    await env.service.start_run(ids=[345678])
    await env.service._run_task
    status = env.service.get_run_status()
    assert status["applied"] == 0
    assert status["none"] == 1


# ------------------------------------------------------------ 写入失败（v2.6.4 用户实测：Kikoeru 占用导致全部"跳过"）
@pytest.mark.asyncio
async def test_flush_write_failure_marks_rows_and_continues(env, monkeypatch):
    """批次写入最终失败 → 行标 write_error 而非误报"跳过"，任务不终止，库不被改动。

    用户实测场景：Kikoeru 容器占用 SQLite 锁（SMB），写入 423 后旧代码把
    全部已抓到评分的行标 applied=False，前端显示"跳过"，且任务整体 failed。
    """
    _mock_dlsite(monkeypatch, {"RJ126662": _rating(5, 4.5, "RJ126662"),
                               "RJ234567": _rating(9, 4.2, "RJ234567")}, {})
    monkeypatch.setattr(rfs, "_FLUSH_RETRY_DELAY", 0)
    attempts = {"n": 0}

    def failing_write(self, fn):
        attempts["n"] += 1
        raise kdb.KikoeruDbError("数据库被占用（Kikoeru 可能在写入）: database is locked", 423)

    monkeypatch.setattr(kdb.KikoeruDbService, "_write_with_snapshot", failing_write)
    await env.service.start_run(ids=[126662, 234567])
    await env.service._run_task
    status = env.service.get_run_status()
    # 任务正常收尾（不再 failed），失败行有明确标记
    assert status["phase"] == "done"
    assert attempts["n"] == 3  # 批级自动重试 3 次
    assert status["applied"] == 0
    assert status["write_failed"] == 2
    for r in status["results"]:
        assert "数据库写入失败" in r["write_error"]
    # 库未被改动（失败行保留 0 分，重跑评分修复会重新进入名单）
    row = kdb.KikoeruDbService().query_table("t_work", search="作品A")["rows"][0]
    assert row["rate_average_2dp"] == 0.0


@pytest.mark.asyncio
async def test_flush_retry_then_succeeds(env, monkeypatch):
    """第一次写入被占用、第二次成功 → 正常套用，不计 write_failed。"""
    _mock_dlsite(monkeypatch, {"RJ126662": _rating(5, 4.5, "RJ126662")}, {})
    monkeypatch.setattr(rfs, "_FLUSH_RETRY_DELAY", 0)
    calls = {"n": 0}
    real_write = kdb.KikoeruDbService._write_with_snapshot

    def flaky_write(self, fn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise kdb.KikoeruDbError("数据库被占用: database is locked", 423)
        return real_write(self, fn)

    monkeypatch.setattr(kdb.KikoeruDbService, "_write_with_snapshot", flaky_write)
    await env.service.start_run(ids=[126662])
    await env.service._run_task
    status = env.service.get_run_status()
    assert calls["n"] == 2
    assert status["applied"] == 1
    assert status["write_failed"] == 0
    result_row = status["results"][0]
    assert result_row["applied"] is True
    assert not result_row.get("write_error")
    row = kdb.KikoeruDbService().query_table("t_work", search="作品A")["rows"][0]
    assert row["rate_average_2dp"] == 4.5
