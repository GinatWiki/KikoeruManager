"""解压入库音频查重服务。

清理同一作品解压产物里的重复音频：多个语言目录（如 日本語/WAV、
简体中文/WAV、繁體中文/WAV）携带同一音轨时，自动去重并优先保留
简体中文目录下的版本。

判重口径保守：扩展名 + 文件大小 + 首尾各 1MB 的 SHA1 指纹完全一致
才算重复，避免误删不同编码 / 不同音质的同名文件。被清理的文件移入
过滤恢复区（任务详情可还原），不直接物理删除。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".wma", ".aac"}

# 优先保留的语言目录标记（简体中文排最前）
PREFERRED_LANG_MARKERS = ("简体中文", "簡體中文")

# 首尾指纹各读 1MB；小于 64KB 的文件不参与判重，避免 0 字节 / 占位文件误伤
_FINGERPRINT_CHUNK = 1024 * 1024
_MIN_DEDUP_SIZE = 64 * 1024


def language_priority_of(relative_path: str) -> Optional[int]:
    """路径命中简体中文标记时返回 0（最高优先），否则返回 None。"""
    normalized = str(relative_path or "").replace("\\", "/").casefold()
    for marker in PREFERRED_LANG_MARKERS:
        if marker.casefold() in normalized:
            return 0
    return None


def file_fingerprint(path: str) -> Optional[str]:
    """计算文件的轻量内容指纹（首尾各 1MB）；失败返回 None。"""
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return None
        with open(path, "rb") as handle:
            head = handle.read(_FINGERPRINT_CHUNK)
            tail = b""
            if size > _FINGERPRINT_CHUNK * 2:
                handle.seek(-_FINGERPRINT_CHUNK, os.SEEK_END)
                tail = handle.read(_FINGERPRINT_CHUNK)
        digest = hashlib.sha1()
        digest.update(head)
        if tail:
            digest.update(b"\x00--tail--\x00")
            digest.update(tail)
        return digest.hexdigest()
    except Exception as exc:
        logger.debug("计算音频指纹失败，跳过判重: path=%s error=%s", path, exc)
        return None


def _collect_audio_groups(root: str) -> Dict[Tuple[str, int, str], List[Dict[str, Any]]]:
    """扫描目录树，按 (扩展名, 大小, 指纹) 聚合音频文件。"""
    groups: Dict[Tuple[str, int, str], List[Dict[str, Any]]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue
            path = os.path.join(dirpath, name)
            try:
                size = int(os.path.getsize(path))
            except OSError:
                continue
            if size < _MIN_DEDUP_SIZE:
                continue
            fingerprint = file_fingerprint(path)
            if fingerprint is None:
                continue
            relative_path = os.path.relpath(path, root).replace("\\", "/")
            key = (ext, size, fingerprint)
            groups.setdefault(key, []).append({
                "path": path,
                "relative_path": relative_path,
                "name": name,
                "size": size,
            })
    return groups


def _pick_winner(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """选保留项：简体中文路径最优先，其次路径更浅，再次相对路径字典序。"""
    def sort_key(item: Dict[str, Any]) -> tuple:
        relative_path = str(item.get("relative_path") or "")
        preferred = language_priority_of(relative_path)
        return (
            0 if preferred == 0 else 1,
            relative_path.count("/"),
            relative_path.casefold(),
        )

    return min(items, key=sort_key)


async def deduplicate_audio_versions(root: str, task: Any) -> Dict[str, Any]:
    """执行音频查重，返回本次清理结果（removed_items 含恢复区信息）。"""
    result: Dict[str, Any] = {
        "removed_count": 0,
        "removed_items": [],
        "skipped": None,
    }
    if not root or not os.path.isdir(root):
        result["skipped"] = "work_dir_missing"
        return result

    groups = await asyncio.to_thread(_collect_audio_groups, root)
    from .filter_recovery_service import get_filter_recovery_service

    recovery_service = get_filter_recovery_service()
    removed_items: List[Dict[str, Any]] = []
    for items in groups.values():
        if len(items) < 2:
            continue
        winner = _pick_winner(items)
        for item in items:
            if item is winner:
                continue
            try:
                recovery = await asyncio.to_thread(
                    recovery_service.capture_item,
                    task.id,
                    item["path"],
                    relative_path=item["relative_path"],
                    entry_type="file",
                    size=item["size"],
                )
            except Exception as exc:
                logger.warning(
                    "音频查重移除失败，保留原文件: path=%s error=%s",
                    item["path"],
                    exc,
                    exc_info=True,
                )
                continue
            public_item = {
                **item,
                "type": "file",
                **recovery,
            }
            removed_items.append(public_item)
            logger.info(
                "音频查重移除重复音频: %s（保留 %s）",
                item["relative_path"],
                winner["relative_path"],
            )

    result["removed_items"] = removed_items
    result["removed_count"] = len(removed_items)
    return result
