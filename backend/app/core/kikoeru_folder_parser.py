"""Kikoeru 文件夹名反解器（v2.6 功能2/功能3 共用）

把 t_work.dir（作品文件夹名）按重命名模板的逆过程解析出 {work_name} 段内容，
用于把数据库里日文抓取标题重命名为文件夹命名中的 work_name。

支持的形态（均为「RJ/VJ/BJ 号 + 名字」结构）：
  1. [社团名][RJ123456][名字]   —— 默认模板 [{original_maker_name}][{rjcode}][{work_name}] 的产物，
                                   work_name 自带一层括号，取括号内内容
  2. [社团名][RJ123456]名字     —— work_name 不带括号，取剩余部分
  3. RJ01005311 名字 / VJ01005311 名字 —— RJ 号 + 空格/分隔符 + 名字
  4. 纯名字（无 RJ 号）        —— matched=False，apply 阶段跳过（无法确认经过模板命名）
  5. 空字符串                  —— skipped

编号匹配比 rjcode_utils.RJ_CODE_PATTERN 宽一档：6~8 位数字都认
（老库存在 7 位 RJ 号，如 id=1712000 → RJ1712000）。
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# RJ/VJ/BJ + 6~8 位数字（含老库 7 位号）；(?!\d) 防止从更长数字串中截断
_DIR_RJ_RE = re.compile(r"[RVB]J\d{6,8}(?!\d)", re.IGNORECASE)

# 前导分隔符：空格/下划线/连字符/点/中点/波浪等
_LEADING_SEP_RE = re.compile(r"^[\s_\-–—·•.、]+")

# 括号配对（全角/半角）
_BRACKET_CLOSER = {"【": "】", "[": "]", "（": "）", "(": ")"}

# 重命名模板变量 → 正则片段（work_name 位置特殊：末尾贪婪/中间非贪婪，单独处理）
_TEMPLATE_VAR_PATTERNS = {
    "rjcode": r"[RVB]J\d{6,8}",
    "maker_id": r".+?",
    "maker_name": r".+?",
    "original_maker_name": r".+?",
    "translator_name": r".+?",
    "release_date": r".+?",
    "cvs": r".+?",
    "tags": r".+?",
}

_TEMPLATE_VAR_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")


def _date_format_to_regex(date_format: str) -> str:
    """把 date_format（如 %y%m%d）翻译成正则片段，未知占位用非贪婪兜底。"""
    mapping = {"%y": r"\d{2}", "%Y": r"\d{4}", "%m": r"\d{2}", "%d": r"\d{2}",
               "%H": r"\d{2}", "%M": r"\d{2}", "%S": r"\d{2}"}
    out, i = [], 0
    while i < len(date_format):
        two = date_format[i:i + 2]
        if two in mapping:
            out.append(mapping[two])
            i += 2
        else:
            out.append(re.escape(date_format[i]))
            i += 1
    return "".join(out)


def build_template_regex(template: str):
    """把重命名模板编译成「结构匹配正则 + work_name 捕获组号」。

    模板里每个 {var} 变成一个捕获组：字面量部分 re.escape；rjcode 用精确
    编号模式；release_date 按 date_format 翻译；其余变量非贪婪；
    work_name 若是模板最后一个变量则贪婪到行尾，否则非贪婪。

    Returns:
        (compiled_regex, work_name_group_index) 或 (None, 原因)。
        模板里没有 {work_name} 变量时无法反解标题，返回 None。
    """
    if not template or "{work_name}" not in template:
        return None, "模板中不含 {work_name} 变量，无法反解"

    parts = _TEMPLATE_VAR_TOKEN_RE.split(str(template))
    # split（带捕获组）输出：[literal, var名, literal, var名, ...]——奇数索引是变量名
    pattern = ["^"]
    group_index = 0
    work_name_group: Optional[int] = None
    # 预扫描确定 work_name 是否是最后一个变量
    var_names = [m.group(1) for m in _TEMPLATE_VAR_TOKEN_RE.finditer(str(template))]
    work_name_is_last = bool(var_names) and var_names[-1] == "work_name"

    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            name = part
            group_index += 1
            if name == "work_name":
                work_name_group = group_index
                pattern.append(r"(.+)" if work_name_is_last else r"(.+?)")
            elif name == "rjcode":
                pattern.append(f"({_TEMPLATE_VAR_PATTERNS['rjcode']})")
            else:
                pattern.append(f"({_TEMPLATE_VAR_PATTERNS.get(name, r'.+?')})")
        elif part:
            pattern.append(re.escape(part))

    pattern.append(r"\s*$")
    try:
        compiled = re.compile("".join(pattern), re.IGNORECASE)
    except re.error as exc:
        return None, f"模板正则编译失败: {exc}"
    return compiled, work_name_group


def parse_work_name_by_template(dir_name: str, template: str) -> dict:
    """按重命名模板结构反解文件夹名（可信路径）。

    dir 必须整体匹配模板结构（含 {rjcode}/{work_name} 等变量的排列与字面量），
    匹配成功才返回 work_name——不匹配说明文件夹不是按当前模板命名的，
    调用方应跳过而不是靠启发式猜。
    """
    compiled, work_name_group = build_template_regex(template)
    if compiled is None:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": work_name_group or "template_unsupported", "rjcode": None}
    text = str(dir_name or "").strip()
    if not text:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "empty_dir", "rjcode": None}
    match = compiled.match(text)
    if not match:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "not_matching_template", "rjcode": None}
    work_name = (match.group(work_name_group) or "").strip()
    rj_match = _DIR_RJ_RE.search(text)
    if not work_name:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "empty_work_name", "rjcode": rj_match.group(0).upper() if rj_match else None}
    return {"work_name": work_name, "matched": True, "skipped": False,
            "reason": "", "rjcode": rj_match.group(0).upper() if rj_match else None}


def compile_user_regex(pattern: str):
    """编译用户自定义正则；非法时抛 ValueError（调用方转 400）。

    限制长度防极端回溯；正则来源是本机使用者本人，编译后按行使用。
    报错回显收到的正则原文与出错位置——粘贴变形（丢反斜杠/混入全角字符/
    断行等）一眼可辨；对常见误写（(?|…、给 \\b 等零宽断言加量词）附针对性提示。
    """
    text = str(pattern or "").strip()
    if not text:
        raise ValueError("正则为空")
    if len(text) > 500:
        raise ValueError("正则过长（上限 500 字符）")
    try:
        return re.compile(text)
    except re.error as exc:
        hints = []
        if "(?|" in text:
            hints.append("Python 正则不支持 PCRE 的 (?|…) 分支重置语法，"
                         "请把 (?| 改成 (?: ，并把每个分支的标题各自放进捕获组")
        message = str(exc)
        position = getattr(exc, "pos", None)
        if "nothing to repeat" in message and isinstance(position, int) \
                and position >= 2 and text[position - 2:position] in (r"\b", r"\B", r"\A", r"\Z"):
            hints.append("\\b 等是零宽断言（只表示位置，不消耗字符），不能加 ?/*/+/{n} 量词，"
                         "直接写 \\bRJ 即可表达词边界")
        message = f"正则无效: {message}"
        for hint in hints:
            message += f"。提示: {hint}"
        if isinstance(position, int) and 0 <= position <= len(text):
            shown = text if len(text) <= 120 else text[:117] + "..."
            message += f"｜收到: {shown!r}（出错位置 {position}）"
        raise ValueError(message) from exc


def parse_work_name_by_regex(dir_name: str, compiled) -> dict:
    """按用户自定义正则反解文件夹名。

    标题取**第一个参与匹配的捕获组**（多分支正则中未参与匹配的组自动跳过，
    等价于 PCRE 的 (?|…) 分支重置语义）；无捕获组时用整体匹配。
    compiled 必须来自 compile_user_regex（预览阶段统一编译，非法正则直接报错
    而不是逐行静默跳过）。不匹配的行 skipped，与模板模式同样保守。
    """
    text = str(dir_name or "")
    rj_match = _DIR_RJ_RE.search(text)
    rjcode = rj_match.group(0).upper() if rj_match else None
    if not text.strip():
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "empty_dir", "rjcode": rjcode}
    match = compiled.search(text)
    if not match:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "not_matching_regex", "rjcode": rjcode}
    work_name = ""
    if compiled.groups:
        # 多分支正则（PCRE 分支重置的 Python 等价写法）：取首个参与匹配的组
        for value in match.groups():
            if value is not None and value.strip():
                work_name = value.strip()
                break
    else:
        work_name = (match.group(0) or "").strip()
    if not work_name:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "empty_regex_group", "rjcode": rjcode}
    return {"work_name": work_name, "matched": True, "skipped": False,
            "reason": "", "rjcode": rjcode}


def parse_work_name_from_dir(dir_name: str) -> dict:
    """从文件夹名反解 work_name。

    Returns:
        {
            "work_name": str,   # 解析出的名字（可能为空）
            "matched": bool,    # 是否按模板语义成功提取（False 时 apply 应跳过）
            "skipped": bool,    # 是否整体跳过（空 dir / RJ 后无内容）
            "reason": str,      # 跳过/未匹配原因
            "rjcode": str|None, # 识别出的编号（大写）
        }
    """
    text = str(dir_name or "").strip()
    if not text:
        return {"work_name": "", "matched": False, "skipped": True, "reason": "empty_dir", "rjcode": None}

    match = _DIR_RJ_RE.search(text)
    if not match:
        # 无编号：无法确认经过模板命名，原样返回但 matched=False（apply 跳过）
        return {"work_name": text, "matched": False, "skipped": False, "reason": "no_rjcode", "rjcode": None}

    rjcode = match.group(0).upper()
    rest = text[match.end():]

    # 编号被括号包裹时（如 [RJ123456]），剥掉收括号
    open_char = text[match.start() - 1] if match.start() > 0 else ""
    if open_char in _BRACKET_CLOSER:
        closer = _BRACKET_CLOSER[open_char]
        stripped = rest.lstrip()
        if stripped.startswith(closer):
            rest = stripped[len(closer):]

    rest = _LEADING_SEP_RE.sub("", rest)
    if not rest:
        return {"work_name": "", "matched": False, "skipped": True,
                "reason": "no_work_name_after_rj", "rjcode": rjcode}

    # 模板形态：work_name 自带一层括号（[社团][RJ..][名字]）→ 取整段内容
    if rest[0] in _BRACKET_CLOSER:
        open_c = rest[0]
        close_c = _BRACKET_CLOSER[open_c]
        end = rest.find(close_c, 1)
        if end > 0:
            inner = rest[1:end].strip()
            tail = rest[end + 1:].strip()
            if inner:
                return {"work_name": inner, "matched": True, "skipped": False,
                        "reason": "", "rjcode": rjcode}
            # 括号内为空（如空段 []）→ 回退用括号后的尾巴
            if tail:
                return {"work_name": tail, "matched": True, "skipped": False,
                        "reason": "", "rjcode": rjcode}
            return {"work_name": "", "matched": False, "skipped": True,
                    "reason": "empty_bracket_after_rj", "rjcode": rjcode}
        # 只有开括号没有闭括号（异常命名）→ 整段当名字
        return {"work_name": rest.strip(), "matched": True, "skipped": False,
                "reason": "", "rjcode": rjcode}

    # 普通形态：RJ 号 + 分隔符 + 名字
    return {"work_name": rest.strip(), "matched": True, "skipped": False,
            "reason": "", "rjcode": rjcode}
