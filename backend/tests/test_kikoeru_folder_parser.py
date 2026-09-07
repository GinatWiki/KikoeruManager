"""kikoeru_folder_parser 单元测试（--noconftest 可独立运行）

运行: cd backend && python -m pytest --noconftest tests/test_kikoeru_folder_parser.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.kikoeru_folder_parser import (  # noqa: E402
    compile_user_regex,
    parse_work_name_by_regex,
    parse_work_name_by_template,
    parse_work_name_from_dir,
)


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


# ============================================================
# 模板结构反解（可信路径）：parse_work_name_by_template
# ============================================================

def test_template_simple_form():
    """用户实际模板 {rjcode} {work_name}：work_name 贪婪取整段"""
    r = parse_work_name_by_template("RJ126662 把儿子培养成自己喜欢的样子", "{rjcode} {work_name}")
    assert r["work_name"] == "把儿子培养成自己喜欢的样子"
    assert r["matched"] is True
    assert r["rjcode"] == "RJ126662"


def test_template_work_name_with_brackets_keeps_whole_segment():
    """真实案例：work_name 天然含 [社团]/【系列】/(CV) 段 → 必须整段保留，不得只取社团名"""
    d = "RJ249730 [Whisp]【采耳·戏水】妖异乡愁谭~荷叶小人·帕罗波罗篇· 初夏~【薄毛毯、陪睡】(CV 浅見ゆい)"
    r = parse_work_name_by_template(d, "{rjcode} {work_name}")
    assert r["work_name"] == "[Whisp]【采耳·戏水】妖异乡愁谭~荷叶小人·帕罗波罗篇· 初夏~【薄毛毯、陪睡】(CV 浅見ゆい)"
    assert r["matched"] is True


def test_template_work_name_leading_zips():
    """7 位 id 的 8 位补零号"""
    r = parse_work_name_by_template("RJ01712000 某个作品", "{rjcode} {work_name}")
    assert r["work_name"] == "某个作品"
    assert r["rjcode"] == "RJ01712000"


def test_template_bracketed_form():
    """带 [] 包裹的模板也能反解"""
    t = "[{original_maker_name}][{rjcode}][{work_name}]"
    r = parse_work_name_by_template("[サークル][RJ123456][中文名]", t)
    assert r["work_name"] == "中文名"
    assert r["matched"] is True


def test_template_not_matching_skipped():
    """文件夹名不符合当前模板结构 → skipped（绝不靠启发式猜标题）"""
    r = parse_work_name_by_template("RJ126662 名字", "[{original_maker_name}][{rjcode}][{work_name}]")
    assert r["skipped"] is True
    assert r["reason"] == "not_matching_template"


def test_template_without_work_name_unsupported():
    r = parse_work_name_by_template("RJ126662 名字", "{rjcode}")
    assert r["skipped"] is True
    assert "work_name" in r["reason"]


def test_template_empty_dir():
    r = parse_work_name_by_template("", "{rjcode} {work_name}")
    assert r["skipped"] is True


# ------------------------------------------------------------ 自定义正则识别（v2.6 新增）
def test_parse_work_name_by_regex_user_example():
    """用户实例：RJ号 + 空格后整段作为 $1 → 新标题保留社团/CV 段。"""
    rx = compile_user_regex(r"^RJ\d+\s+(.+)$")
    r = parse_work_name_by_regex(
        "RJ192588 [ベレス解部]新生代风格婴儿游戏 小夜子(CV ゆづきひな。)", rx
    )
    assert r["matched"] is True
    assert r["work_name"] == "[ベレス解部]新生代风格婴儿游戏 小夜子(CV ゆづきひな。)"
    assert r["rjcode"] == "RJ192588"


def test_parse_work_name_by_regex_no_group_uses_whole_match():
    """无捕获组 → 整体匹配结果作为标题。"""
    rx = compile_user_regex(r"RJ\d+")
    r = parse_work_name_by_regex("RJ123456 名字", rx)
    assert r["matched"] is True
    assert r["work_name"] == "RJ123456"


def test_parse_work_name_by_regex_optional_group_not_participating():
    """可选捕获组未参与匹配 → 自动落到下一个参与匹配的组（分支重置语义）。"""
    rx = compile_user_regex(r"^(RJ\d+)?\s*(.+)$")
    r = parse_work_name_by_regex("普通文件夹名", rx)
    assert r["matched"] is True
    assert r["work_name"] == "普通文件夹名"  # 组 1 未参与 → 取组 2


def test_parse_work_name_by_regex_all_groups_empty_skipped():
    """所有捕获组都为空/未参与 → skipped。"""
    rx = compile_user_regex(r"^(RJ\d+)?x$")
    r = parse_work_name_by_regex("x", rx)
    assert r["matched"] is False
    assert r["skipped"] is True
    assert r["reason"] == "empty_regex_group"


def test_parse_work_name_by_regex_no_match_skipped():
    rx = compile_user_regex(r"^RJ\d+\s+(.+)$")
    r = parse_work_name_by_regex("普通文件夹名", rx)
    assert r["matched"] is False
    assert r["reason"] == "not_matching_regex"


def test_compile_user_regex_rejects_bad_patterns():
    with pytest.raises(ValueError):
        compile_user_regex("(")
    with pytest.raises(ValueError):
        compile_user_regex("   ")
    with pytest.raises(ValueError):
        compile_user_regex("a" * 501)
    with pytest.raises(ValueError):
        compile_user_regex("")


def test_compile_user_regex_branch_reset_hint():
    """PCRE 的 (?|…) 分支重置不被 Python re 支持 → 报错附改写提示。"""
    with pytest.raises(ValueError) as exc_info:
        compile_user_regex(r"\[?RJ(?:\d{6}|\d{8})\]?\s*(?|\[([^\[\]]+)\]\s*$|(.+))")
    assert "分支重置" in str(exc_info.value)
    assert "(?:" in str(exc_info.value)


def test_compile_user_regex_zero_width_assertion_hint():
    """\\b 等零宽断言加量词 → nothing to repeat，报错回显原文并附提示。"""
    with pytest.raises(ValueError) as exc_info:
        compile_user_regex(r"\b?RJ\d+")
    message = str(exc_info.value)
    assert "nothing to repeat" in message
    assert "零宽断言" in message
    assert "收到" in message
    assert "b?RJ" in message  # repr 中反斜杠会翻倍，但片段可见


def test_compile_user_regex_dollar_dollar_hint():
    """网页/聊天复制把 \\[ \\] 转成 $$ → 报错附还原提示（v2.6.7 用户实测案例）。"""
    with pytest.raises(ValueError) as exc_info:
        compile_user_regex(r"\b$$?RJ(?:\d{6}|\d{8})$$?\s*(?:$$([^\[$$]+)\]\s*$|(.+))")
    message = str(exc_info.value)
    assert "nothing to repeat" in message  # $? 给锚点加量词 → 出错位置 4
    assert "$$" in message
    assert "复制" in message


def test_parse_work_name_by_regex_multi_branch():
    """用户的多分支正则（(?: 等价写法）：取首个参与匹配的捕获组。

    编号必须用 \\d{6,8}(?!\\d) 贪婪取最长——写成 (?:\\d{6}|\\d{8}) 有序分支
    会先匹配 6 位，8 位号（RJ01693368）的剩余「68]」会被卷进标题
    （v2.6.8 用户实测：新标题变成「68][將一切收入囊中的最強」）。
    """
    rx = compile_user_regex(r"\b\[?RJ\d{6,8}(?!\d)\]?\s*(?:\[([^\[\]]+)\]\s*$|(.+))")

    # 8 位号 + [RJ][标题] 双括号形态（用户实测案例）
    r = parse_work_name_by_regex(
        "[RJ01693368][將一切收入囊中的最強魔王，被作為奴○帶來的戰敗國下級淫魔用不可思議的道具徹底銘刻快樂的故事]", rx
    )
    assert r["matched"] is True
    assert r["work_name"] == "將一切收入囊中的最強魔王，被作為奴○帶來的戰敗國下級淫魔用不可思議的道具徹底銘刻快樂的故事"
    assert r["rjcode"] == "RJ01693368"

    # RJ 号后的括号段不在行尾 → 分支 B 取整段（保留社团/CV）
    r_own = parse_work_name_by_regex(
        "RJ192588 [ベレス解部]新生代风格婴儿游戏 小夜子(CV ゆづきひな。)", rx
    )
    assert r_own["matched"] is True
    assert r_own["work_name"] == "[ベレス解部]新生代风格婴儿游戏 小夜子(CV ゆづきひな。)"

    # 括号段恰在行尾 → 分支 A 取括号内内容
    r_tail = parse_work_name_by_regex("[RJ123456] [标题]", rx)
    assert r_tail["matched"] is True
    assert r_tail["work_name"] == "标题"

    r_rest = parse_work_name_by_regex("[RJ123456] 名字", rx)
    assert r_rest["matched"] is True
    assert r_rest["work_name"] == "名字"
