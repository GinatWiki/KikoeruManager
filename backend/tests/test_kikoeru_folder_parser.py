"""kikoeru_folder_parser 单元测试（--noconftest 可独立运行）

运行: cd backend && python -m pytest --noconftest tests/test_kikoeru_folder_parser.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.kikoeru_folder_parser import parse_work_name_from_dir  # noqa: E402


def test_parse_bracket_form():
    """[社团][RJ123456][名字] → 取最后一个括号段内容"""
    r = parse_work_name_from_dir("[サークル名][RJ123456][很遗憾眼里只有你的青梅竹马]")
    assert r["work_name"] == "很遗憾眼里只有你的青梅竹马"
    assert r["matched"] is True
    assert r["skipped"] is False
    assert r["rjcode"] == "RJ123456"


def test_parse_bracket_without_trailing_bracket():
    """[社团][RJ123456]名字 → 取剩余部分"""
    r = parse_work_name_from_dir("[サークル名][RJ123456]名字")
    assert r["work_name"] == "名字"
    assert r["matched"] is True


def test_parse_space_form():
    """RJ01005311 名字 → RJ 前缀剥离"""
    r = parse_work_name_from_dir("RJ01005311 爱上火车 少女们的秘事簿 ～秋～")
    assert r["work_name"] == "爱上火车 少女们的秘事簿 ～秋～"
    assert r["matched"] is True
    assert r["rjcode"] == "RJ01005311"


def test_parse_vj_form():
    r = parse_work_name_from_dir("VJ01005311 爱上火车 少女们的秘事簿")
    assert r["work_name"] == "爱上火车 少女们的秘事簿"
    assert r["matched"] is True
    assert r["rjcode"] == "VJ01005311"


def test_parse_seven_digit_rj():
    """老库存在 7 位 RJ 号"""
    r = parse_work_name_from_dir("RJ1712000 七位号测试")
    assert r["work_name"] == "七位号测试"
    assert r["rjcode"] == "RJ1712000"


def test_parse_no_rj_passthrough():
    """纯名字（无编号）→ matched=False，apply 阶段跳过"""
    r = parse_work_name_from_dir("纯名字没有编号")
    assert r["work_name"] == "纯名字没有编号"
    assert r["matched"] is False
    assert r["skipped"] is False
    assert r["reason"] == "no_rjcode"


def test_parse_empty_dir_skipped():
    r = parse_work_name_from_dir("")
    assert r["skipped"] is True
    assert r["matched"] is False
    assert r["work_name"] == ""


def test_parse_rj_only_skipped():
    """dir 只有 RJ 号 → 无 work_name，跳过"""
    r = parse_work_name_from_dir("RJ123456")
    assert r["skipped"] is True
    assert r["reason"] == "no_work_name_after_rj"


def test_parse_bracket_rj_only_skipped():
    r = parse_work_name_from_dir("[RJ123456]")
    assert r["skipped"] is True


def test_parse_empty_bracket_falls_back_to_tail():
    """空括号段（如模板渲染出 []）→ 回退用括号后的尾巴"""
    r = parse_work_name_from_dir("[社团][RJ123456][]尾巴名字")
    assert r["work_name"] == "尾巴名字"
    assert r["matched"] is True
