"""KikoeruDbService 单元测试（--noconftest 可独立运行，使用临时 sqlite 文件）

运行: cd backend && python -m pytest --noconftest tests/test_kikoeru_db_service.py -q
"""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import kikoeru_db_service as kdb  # noqa: E402
from app.core.kikoeru_db_service import KikoeruDbError, KikoeruDbService  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """构造临时 Kikoeru 库 + 备份目录，并把服务指向它们。"""
    db_path = tmp_path / "kikoeru_test.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE t_work (id integer primary key autoincrement, title text not null,
            dir text not null, circle_id integer, lyric_status text not null default '',
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
        INSERT INTO t_circle (name) VALUES ('サークルA');
        INSERT INTO t_tag (name) VALUES (' tag1');
        INSERT INTO t_work (title, dir, circle_id) VALUES
            ('日文标题A', 'RJ123456 中文名A', 1),
            ('日文标题B', '[社团][RJ234567][中文名B]', 1),
            ('无编号目录', '普通文件夹名', 1),
            ('titleD', '', 1);
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

    service = KikoeruDbService()
    return SimpleNamespace(service=service, db_path=db_path, backup_dir=backup_dir)


# ------------------------------------------------------------ 表权限 / 查询
def test_list_tables_hides_protected(env):
    names = {t["name"] for t in env.service.list_tables()}
    assert "t_work" in names
    assert "t_user" not in names
    assert "knex_migrations" not in names


def test_paginated_query(env):
    for i in range(30):
        env.service.insert_row("t_circle", {"name": f"circle{i}"})
    result = env.service.query_table("t_circle", page=2, size=10)
    assert result["total"] == 31  # 1 初始 + 30 新增
    assert len(result["rows"]) == 10
    assert result["page"] == 2


def test_query_search(env):
    result = env.service.query_table("t_work", search="中文名A")
    assert result["total"] == 1
    assert result["rows"][0]["title"] == "日文标题A"


def test_readonly_table_write_rejected(env):
    with pytest.raises(KikoeruDbError) as exc_info:
        env.service.update_row("t_play_histroy", {"user_name": "u", "work_id": 1}, {"state": "{}"})
    assert exc_info.value.status == 403


def test_hidden_table_rejected(env):
    with pytest.raises(KikoeruDbError) as exc_info:
        env.service.query_table("t_user")
    assert exc_info.value.status == 403
    with pytest.raises(KikoeruDbError):
        env.service.delete_row("knex_migrations", 1)


# ------------------------------------------------------------ 写路径
def test_update_and_delete_row_with_snapshot(env):
    env.service.update_row("t_work", 1, {"title": "新标题A"})
    row = env.service.query_table("t_work", search="新标题A")["rows"][0]
    assert row["id"] == 1

    snaps = env.service.list_backups("snapshot")
    assert len(snaps) == 1  # 写前自动快照

    env.service.delete_row("t_circle", 1)
    assert env.service.query_table("t_circle")["total"] == 0


def test_composite_pk_update_and_delete(env):
    env.service.insert_row("r_tag_work", {"tag_id": 9, "work_id": 1})
    env.service.update_row("r_tag_work", {"tag_id": 9, "work_id": 1}, {"work_id": 2})
    rows = env.service.query_table("r_tag_work")["rows"]
    assert {"tag_id": 9, "work_id": 2} in [{"tag_id": r["tag_id"], "work_id": r["work_id"]} for r in rows]
    env.service.delete_row("r_tag_work", {"tag_id": 9, "work_id": 2})
    assert env.service.query_table("r_tag_work")["total"] == 0


def test_write_rollback_on_failure(env):
    def _boom(conn):
        conn.execute('UPDATE "t_work" SET title = \'半途失败\' WHERE id = 1')
        raise RuntimeError("模拟写中异常")

    with pytest.raises(RuntimeError):
        env.service._write_with_snapshot(_boom)
    # 数据未被破坏
    title = env.service.query_table("t_work", search="日文标题A")["rows"][0]["title"]
    assert title == "日文标题A"


def test_backup_restore_roundtrip(env):
    env.service.create_backup("manual")
    env.service.update_row("t_work", 1, {"title": "改动后的标题"})
    assert env.service.query_table("t_work", search="改动后的标题")["total"] == 1

    backup = env.service.list_backups("manual")[0]
    env.service.restore_backup(backup["filename"])
    assert env.service.query_table("t_work", search="改动后的标题")["total"] == 0
    assert env.service.query_table("t_work", search="日文标题A")["total"] == 1
    # pre-restore 自动生成
    assert len(env.service.list_backups("pre-restore")) == 1


def test_restore_rejects_bad_filename(env):
    with pytest.raises(KikoeruDbError) as exc_info:
        env.service.restore_backup("../../etc/passwd")
    assert exc_info.value.status == 400


def test_snapshot_retention_cleanup(env):
    for i in range(6):
        env.service.update_row("t_circle", 1, {"name": f"name{i}"})
    snaps = env.service.list_backups("snapshot")
    assert len(snaps) <= 3  # snapshot_retention=3


# ------------------------------------------------------------ 功能2
def test_rename_preview_and_apply(env):
    preview = env.service.preview_rename()
    items = {i["id"]: i for i in preview["items"]}
    # RJ123456 中文名A：dir 与 title 不同 → changed
    assert items[1]["changed"] is True
    assert items[1]["new_title"] == "中文名A"
    # 无编号目录 → skipped
    assert items[3]["skipped"] is True
    # 空 dir → skipped
    assert items[4]["skipped"] is True

    result = env.service.apply_rename([1, 2, 3, 4])
    assert result["applied"] == 2

    row = env.service.query_table("t_work", search="中文名B")["rows"][0]
    assert row["title"] == "中文名B"
    assert row["is_custom_meta"] == 1
    # skipped 行未被改动
    assert env.service.query_table("t_work", search="日文标题A")["total"] == 0  # 1 号已改名
    unchanged = env.service.query_table("t_work", search="无编号目录")["rows"][0]
    assert unchanged["is_custom_meta"] == 0


def test_apply_rename_single(env):
    assert env.service.apply_rename_single(2) is True
    assert env.service.query_table("t_work", search="中文名B")["rows"][0]["is_custom_meta"] == 1
    # 已一致 → False；无法解析 → False
    assert env.service.apply_rename_single(2) is False
    assert env.service.apply_rename_single(3) is False
    assert env.service.apply_rename_single(999999) is False


# ------------------------------------------------------------ 诊断
def test_diagnose_all_ok(env):
    result = env.service.diagnose()
    assert result["ok"] is True
    steps = {s["step"]: s["ok"] for s in result["steps"]}
    assert steps == {"路径可达": True, "结构匹配": True, "锁可用": True, "写入能力": True}


def test_diagnose_missing_tables(env, tmp_path, monkeypatch):
    db_path = tmp_path / "incomplete.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t_work (id integer primary key)")
    conn.commit()
    conn.close()
    fake_cfg = SimpleNamespace(kikoeru_db=SimpleNamespace(
        enabled=True, db_path=str(db_path), backup_dir=str(tmp_path / "b"),
        backup_interval_hours=24.0, backup_retention=2, snapshot_retention=3,
        scan_listen_enabled=False, scan_poll_interval_minutes=30,
        scan_checkpoint='', last_scan_finished_at=''))
    monkeypatch.setattr(kdb, "get_config", lambda: fake_cfg)
    result = KikoeruDbService().diagnose()
    assert result["ok"] is False
    structure = next(s for s in result["steps"] if s["step"] == "结构匹配")
    assert structure["ok"] is False
    assert "t_circle" in structure["detail"]


def test_diagnose_locked(env, monkeypatch):
    """Kikoeru 持有写锁时，锁可用步骤应失败。"""
    holder = sqlite3.connect(str(env.db_path))
    holder.execute("BEGIN EXCLUSIVE")
    try:
        result = env.service.diagnose()
        lock_step = next(s for s in result["steps"] if s["step"] == "锁可用")
        assert lock_step["ok"] is False
    finally:
        holder.rollback()
        holder.close()
