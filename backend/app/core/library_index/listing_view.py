"""库存浏览统一序列化层（重构阶段 1）。

背景：库存浏览曾有多个读入口（browser/files、library/files、folder-contents、
list-folders…），每个入口对同一条目录项的字段命名/含义都不完全一致，前端各处
各自解析——这是"显示的不是实际文件"的另一半原因。

本模块定义**唯一的条目形状**，并提供一个宽容的归一化函数：把各入口的原始行
映射到统一形状。它只做形状转换，不读磁盘、不碰数据库、不改变任何现有行为，
因此可以独立单测，也是后续 listing / verify / 薄适配器的共同地基。

统一形状（条目）::

    {
      "name": str,            # 显示名（不含路径）
      "is_dir": bool,
      "size": int,            # 文件字节数；目录为 0
      "mtime": float,         # 修改时间（秒，UTC 时间戳）；未知为 0.0
      "relative_path": str,   # 相对库存根的路径（以 / 分隔）
      "child_count": int,     # 目录可见子项数（未知为 -1）
      "stale": bool,          # verify 模式下与磁盘不一致
      "source": str,          # "index" | "verify" | "live"
    }

统一形状（响应）见 :func:`build_listing_response`。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

LISTING_ITEM_FIELDS = (
    "name",
    "is_dir",
    "size",
    "mtime",
    "relative_path",
    "child_count",
    "stale",
    "source",
)

# 旧端点（browser/files / library/files / folder-contents / list-folders）在统一 8 字段
# 之外使用的扩展字段。它们不进入新端点的默认输出（响应结构一字不改），但
# ``normalize_listing_item(keep_optional=True)`` 会把它们一并收进统一形状，
# 使统一序列化层成为所有读入口的唯一字段词汇表。
LISTING_ITEM_OPTIONAL_FIELDS = (
    "rjcode",
    "size_status",
    "has_children",
    "children_loaded",
    "file_count",
    "folder_count",
    "folder_count_status",
    "size_via_index",
    "index_refresh_pending",
    "browse_via_index",
    "absolute_path",
    "modified_time",
    "unzip_time",
    # 原始 size 值通道：canonical 对目录强制 size=0（新端点契约），旧端点目录行的
    # size 是「累计大小/未知(None)」，legacy_*_row 用 raw_size 恢复旧语义。
    "raw_size",
)

# 值语义为「int 或 None」的可选字段（None 与 0 含义不同：未知 vs 确认为 0）
_INT_OR_NONE_OPTIONAL_FIELDS = frozenset({"file_count", "folder_count"})
# 值语义为「bool 或 None」的可选字段
_BOOL_OR_NONE_OPTIONAL_FIELDS = frozenset({"has_children", "children_loaded"})

_DIR_TRUE = {"1", "true", "yes", "y", "dir", "directory", "folder"}


def _first(raw: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw and raw.get(key) is not None and raw.get(key) != "":
            return raw.get(key)
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _DIR_TRUE


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float_mtime(value: Any) -> float:
    """把 mtime / mtime_ns / ISO 字符串统一成秒级时间戳；失败返回 0.0。"""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        # 纳秒级时间戳（> year 3000 的秒值）自动降到秒
        return number / 1_000_000_000.0 if number > 1e11 else number
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _normalize_relative_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _join_relative(base_relative_path: str, name: str) -> str:
    base = _normalize_relative_path(base_relative_path)
    return f"{base}/{name}" if base else name


def normalize_listing_item(
    raw: Any,
    *,
    base_relative_path: str = "",
    source: str = "index",
    default_is_dir: bool = False,
    keep_optional: bool = False,
) -> Optional[Dict[str, Any]]:
    """把任意浏览入口的原始行映射成统一形状；无法识别的行返回 None。

    ``keep_optional=True`` 时额外保留旧端点扩展字段（见
    ``LISTING_ITEM_OPTIONAL_FIELDS``）：raw 里出现才带上，字段值尽量保持
    原语义（int/bool 可为 None），供旧端点薄适配器派生旧行使用。
    """
    if not isinstance(raw, dict):
        return None
    name = str(
        _first(raw, "name", "filename", "file_name", "display_name", "title") or ""
    ).strip()
    if not name and not raw.get("relative_path"):
        return None
    relative_path = _normalize_relative_path(
        _first(raw, "relative_path", "rel_path", "path", "full_relative_path")
    )
    if not relative_path:
        relative_path = _join_relative(base_relative_path, name)
    if not name:
        name = relative_path.rsplit("/", 1)[-1]

    is_dir = _as_bool(
        _first(raw, "is_dir", "is_folder", "folder", "directory", "type"),
        default=default_is_dir,
    )
    size = 0 if is_dir else max(0, _as_int(_first(raw, "size", "size_bytes", "length", "bytes")))
    mtime = _as_float_mtime(
        _first(raw, "mtime", "modified_time", "modified_at", "mtime_ns", "updated_at")
    )
    raw_child_count = _first(raw, "child_count", "children_count", "child_total", "file_count")
    if raw_child_count is None:
        child_count = -1
    else:
        child_count = max(0, _as_int(raw_child_count))
    stale = _as_bool(_first(raw, "stale", "outdated", "index_mismatch"))
    item: Dict[str, Any] = {
        "name": name,
        "is_dir": is_dir,
        "size": size,
        "mtime": mtime,
        "relative_path": relative_path,
        "child_count": child_count,
        "stale": stale,
        "source": str(_first(raw, "source") or source or "index"),
    }
    if keep_optional:
        for field in LISTING_ITEM_OPTIONAL_FIELDS:
            if field in raw:
                item[field] = _normalize_optional_field(field, raw.get(field))
    return item


def _normalize_optional_field(field: str, value: Any) -> Any:
    """可选字段按各自语义归一化；None 一律保持 None（未知 ≠ 0）。"""
    if value is None:
        return None
    if field in _INT_OR_NONE_OPTIONAL_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if field in _BOOL_OR_NONE_OPTIONAL_FIELDS:
        if isinstance(value, bool):
            return value
        return _as_bool(value)
    if field == "size_via_index" or field in {
        "index_refresh_pending",
        "browse_via_index",
    }:
        return _as_bool(value)
    return value


def normalize_listing_items(
    raw_items: Iterable[Any],
    *,
    base_relative_path: str = "",
    source: str = "index",
    keep_optional: bool = False,
) -> List[Dict[str, Any]]:
    """批量归一化，跳过无法识别的行，保持原顺序。"""
    out: List[Dict[str, Any]] = []
    for raw in raw_items or []:
        item = normalize_listing_item(
            raw,
            base_relative_path=base_relative_path,
            source=source,
            keep_optional=keep_optional,
        )
        if item is not None:
            out.append(item)
    return out


def build_listing_response(
    items: Iterable[Any],
    *,
    source: str = "index",
    base_relative_path: str = "",
    generation: int = 0,
    fresh_at: str = "",
    cursor: str = "",
    has_more: bool = False,
    verify_failed: bool = False,
    keep_optional: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造统一的 listing 响应；`source` 标明数据来源，前端据此判断新鲜度。"""
    payload: Dict[str, Any] = {
        "items": normalize_listing_items(
            items,
            base_relative_path=base_relative_path,
            source=source,
            keep_optional=keep_optional,
        ),
        "source": str(source or "index"),
        "generation": _as_int(generation),
        "fresh_at": str(fresh_at or ""),
        "cursor": str(cursor or ""),
        "has_more": bool(has_more),
    }
    if verify_failed:
        payload["verify_failed"] = True
    if extra:
        for key, value in extra.items():
            payload.setdefault(key, value)
    return payload


def _seconds_to_iso_mtime(seconds: Any) -> Optional[str]:
    """秒级时间戳 → 本地时区 ISO 字符串；无效值返回 None（旧端点行的 modified_time 语义）。"""
    try:
        seconds_f = float(seconds or 0)
    except (TypeError, ValueError):
        return None
    if seconds_f <= 0:
        return None
    try:
        from datetime import datetime

        return datetime.fromtimestamp(seconds_f).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def legacy_browser_file_row(canonical: Dict[str, Any]) -> Dict[str, Any]:
    """统一形状 → ``browser/files``/``library/files`` 的 files[] 旧行（字段名与值语义逐字段对齐旧实现）。

    输入应为 ``normalize_listing_item(keep_optional=True)`` 的产物（或与其同键的
    canonical dict），可选扩展字段原样透传；契约快照测试锁定该映射。

    特例：canonical 对目录强制 ``size=0``（新端点契约），但旧端点目录行的 size 是
    「累计大小，synology 下可为 None=未知」—— raw 里带 ``raw_size``（原始 size 值）
    时优先透传，否则回退 canonical 的 size。
    """
    is_dir = bool(canonical.get("is_dir"))
    if "raw_size" in canonical:
        size = None if canonical.get("raw_size") is None else int(canonical["raw_size"])
    else:
        size_raw = canonical.get("size")
        size = None if size_raw is None else int(size_raw)
    size_status = canonical.get("size_status")
    if size_status is None:
        size_status = "ready" if (not is_dir or size) else "pending"
    modified_time = canonical.get("modified_time")
    if modified_time is None:
        modified_time = _seconds_to_iso_mtime(canonical.get("mtime"))
    row: Dict[str, Any] = {
        "name": canonical.get("name") or "",
        "is_directory": is_dir,
        "size": size,
        "size_status": size_status,
        "modified_time": modified_time,
        "unzip_time": canonical.get("unzip_time") or modified_time,
        "relative_path": canonical.get("relative_path") or "",
        "rjcode": canonical.get("rjcode"),
        "file_count": canonical.get("file_count"),
        "folder_count": canonical.get("folder_count"),
        "size_via_index": bool(canonical.get("size_via_index")),
        "index_refresh_pending": bool(canonical.get("index_refresh_pending")),
        "browse_via_index": bool(canonical.get("browse_via_index")),
    }
    return row


def legacy_folder_row(canonical: Dict[str, Any]) -> Dict[str, Any]:
    """统一形状 → ``folder-contents``/``list-folders`` 的 items[]/folders[] 旧行。

    两个端点的行结构几乎一致（``is_directory`` 命名一致，folder-contents 额外带
    ``type``/``has_children``/``children_loaded``），这里产出超集行，各适配器按需取字段。

    特例同 legacy_browser_file_row：``raw_size`` 优先透传（旧端点目录 size 可为 None）。
    """
    is_dir = bool(canonical.get("is_dir"))
    if "raw_size" in canonical:
        size = None if canonical.get("raw_size") is None else int(canonical["raw_size"])
    else:
        size_raw = canonical.get("size")
        size = None if size_raw is None else int(size_raw)
    modified_time = canonical.get("modified_time")
    if modified_time is None:
        modified_time = _seconds_to_iso_mtime(canonical.get("mtime"))
    file_count = canonical.get("file_count")
    if file_count is None:
        file_count = 1 if not is_dir else None
    has_children = canonical.get("has_children")
    if has_children is None:
        has_children = bool(is_dir and ((file_count or 0) > 0 or (canonical.get("folder_count") or 0) > 0))
    children_loaded = canonical.get("children_loaded")
    if children_loaded is None:
        children_loaded = not has_children
    folder_count = canonical.get("folder_count")
    if folder_count is None and not is_dir:
        folder_count = 0  # 旧实现文件行 folder_count 恒为 0
    row: Dict[str, Any] = {
        "name": canonical.get("name") or "",
        "path": canonical.get("absolute_path") or "",
        "relative_path": canonical.get("relative_path") or "",
        "size": size,
        "size_status": canonical.get("size_status") or ("ready" if (not is_dir or size) else "pending"),
        "modified_time": modified_time,
        "type": "dir" if is_dir else "file",
        "is_directory": is_dir,
        "has_children": has_children,
        "children_loaded": children_loaded,
        "file_count": file_count,
        "folder_count": folder_count,
        "folder_count_status": canonical.get("folder_count_status")
        or ("ready" if (not is_dir or canonical.get("folder_count") is not None) else "lazy"),
        "browse_via_index": bool(canonical.get("browse_via_index")),
    }
    # 这两个字段并非所有旧端点行都有：folder-contents/list-folders 的索引行没有；
    # 只有 raw 里显式提供（如 fs 浅扫行 / stale 标记调用方追加）时才带上。
    if "size_via_index" in canonical:
        row["size_via_index"] = bool(canonical["size_via_index"])
    if "index_refresh_pending" in canonical:
        row["index_refresh_pending"] = bool(canonical["index_refresh_pending"])
    return row
