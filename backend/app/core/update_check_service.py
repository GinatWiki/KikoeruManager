"""GitHub 版本更新检测服务。

查询主发布仓库 GinatWiki/KikoeruManager 的最新 Release，与当前运行版本
比较，供前端侧边栏版本标签高亮与跳转使用。结果带内存缓存，避免每次
前端请求都打 GitHub API（未认证限流 60 次/小时）；网络不可达时静默
降级，不影响正常业务。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GITHUB_REPO = "GinatWiki/KikoeruManager"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# 检测结果内存缓存：成功 30 分钟 / 失败 5 分钟
_SUCCESS_CACHE_TTL = 30 * 60
_FAILURE_CACHE_TTL = 5 * 60

_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")

_cache: Dict[str, Any] = {"payload": None, "expires_at": 0.0}


def parse_version(value: Any) -> Optional[Tuple[int, int, int]]:
    """把 ``2.4.52`` / ``v2.4.52`` 解析成 (major, minor, patch)，失败返回 None。"""
    match = _VERSION_RE.match(str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_newer(latest: Any, current: Any) -> bool:
    """判断 latest 是否比 current 更新；任一版本号无法解析时返回 False。"""
    latest_parsed = parse_version(latest)
    current_parsed = parse_version(current)
    if latest_parsed is None or current_parsed is None:
        return False
    return latest_parsed > current_parsed


def _base_payload(current_version: str) -> Dict[str, Any]:
    return {
        "success": False,
        "repo": GITHUB_REPO,
        "current_version": current_version,
        "latest_version": None,
        "latest_tag": None,
        "has_update": False,
        "release_url": None,
        "checked_at": int(time.time()),
    }


async def _fetch_latest_release(current_version: str) -> Dict[str, Any]:
    import httpx

    payload = _base_payload(current_version)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=5.0),
            trust_env=True,  # 跟随系统代理，开启梯子即可访问 GitHub
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "KikoeruManager",
            },
        ) as client:
            response = await client.get(RELEASES_LATEST_URL)
    except Exception as exc:
        logger.warning("[更新检测] 查询 GitHub 失败: %s", exc)
        payload["error"] = "network_error"
        return payload

    if response.status_code != 200:
        logger.warning("[更新检测] GitHub 返回 HTTP %s", response.status_code)
        payload["error"] = f"http_{response.status_code}"
        return payload

    try:
        data = response.json()
    except Exception as exc:
        logger.warning("[更新检测] GitHub 响应解析失败: %s", exc)
        payload["error"] = "invalid_response"
        return payload

    tag_name = str(data.get("tag_name") or "").strip()
    html_url = str(data.get("html_url") or "").strip()
    if not tag_name:
        payload["error"] = "missing_tag"
        return payload

    latest_version = tag_name.lstrip("vV") if re.match(r"^[vV]?\d", tag_name) else tag_name
    payload.update({
        "success": True,
        "latest_version": latest_version,
        "latest_tag": tag_name,
        "has_update": is_newer(tag_name, current_version),
        "release_url": html_url or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}",
    })
    return payload


async def check_for_updates(current_version: str) -> Dict[str, Any]:
    """检测 GitHub 最新 Release 并给出是否可更新的结论（带内存缓存）。"""
    now = time.time()
    cached = _cache["payload"]
    if cached is not None and now < _cache["expires_at"]:
        payload = dict(cached)
        payload["has_update"] = bool(payload.get("latest_version")) and is_newer(
            payload.get("latest_version"), current_version
        )
        return payload

    payload = await _fetch_latest_release(current_version)
    _cache["payload"] = payload
    _cache["expires_at"] = now + (_SUCCESS_CACHE_TTL if payload.get("success") else _FAILURE_CACHE_TTL)
    return payload
