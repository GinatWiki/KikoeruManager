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
