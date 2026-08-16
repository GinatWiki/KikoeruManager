"""GitHub 版本更新检测服务。

查询主发布仓库 GinatWiki/KikoeruManager 的最新 Release，与当前运行版本
比较，供前端侧边栏版本标签高亮与跳转使用。结果带内存缓存，避免每次
前端请求都打 GitHub API（未认证限流 60 次/小时）；网络不可达时静默
降级，不影响正常业务。

检测链路：优先走官方 API ``releases/latest``；当 API 因未认证限流返回
403（家庭/办公共享出口 IP 很常见）或网络异常时，回退到不受 API 限流
影响的 ``github.com/{repo}/releases/latest`` 网页，解析 og:url 中的 tag。
多个前端页面（桌面壳 + 浏览器）同时请求时经事件循环内锁合并，只放
一个真实查询出去，避免重复打 GitHub。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

GITHUB_REPO = "GinatWiki/KikoeruManager"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_HTML_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

# 检测结果内存缓存：成功 30 分钟 / 失败 5 分钟
_SUCCESS_CACHE_TTL = 30 * 60
_FAILURE_CACHE_TTL = 5 * 60

# 网页通道用浏览器 UA，避免被 GitHub 按非浏览器 UA 拒绝
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")
# og:url content 可能是相对路径（/GinatWiki/.../releases/tag/vX.Y.Z）或完整 URL
_OG_URL_TAG_RE = re.compile(
    r'<meta[^>]+property="og:url"[^>]+content="[^"]*releases/tag/([A-Za-z0-9][A-Za-z0-9._-]*)"'
)
_TAG_PATH_RE = re.compile(r"releases/tag/([A-Za-z0-9][A-Za-z0-9._-]*)")

_cache: Dict[str, Any] = {"payload": None, "expires_at": 0.0}
# 并发去重锁：按事件循环各持一把，避免跨循环复用 asyncio.Lock 报绑定错误
_locks: Dict[int, asyncio.Lock] = {}


def _inflight_lock() -> asyncio.Lock:
    """返回当前事件循环的去重锁（首次使用时惰性创建）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.Lock()
    key = id(loop)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


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


def parse_release_tag_from_html(html: str) -> Optional[str]:
    """从 releases 网页解析最新 Release 的 tag：优先 og:url meta，其次扫 tag 路径。"""
    if not html:
        return None
    match = _OG_URL_TAG_RE.search(html)
    if match:
        return match.group(1)
    match = _TAG_PATH_RE.search(html)
    return match.group(1) if match else None


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


def _apply_release(
    payload: Dict[str, Any],
    tag_name: str,
    html_url: str,
    current_version: str,
    source: str,
) -> Dict[str, Any]:
    """把解析出的 Release 信息填进 payload 并给出版本比较结论。"""
    latest_version = tag_name.lstrip("vV") if re.match(r"^[vV]?\d", tag_name) else tag_name
    payload.update({
        "success": True,
        "source": source,
        "latest_version": latest_version,
        "latest_tag": tag_name,
        "has_update": is_newer(tag_name, current_version),
        "release_url": html_url or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}",
    })
    return payload


async def _fetch_via_api(current_version: str) -> Optional[Dict[str, Any]]:
    """官方 API 通道；成功返回 payload，失败（含 403 限流）返回 None。"""
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
        logger.warning("[更新检测] 查询 GitHub API 失败: %s", exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "[更新检测] GitHub API 返回 HTTP %s（403 通常是未认证限流），改用 releases 网页回退",
            response.status_code,
        )
        return None

    try:
        data = response.json()
    except Exception as exc:
        logger.warning("[更新检测] GitHub API 响应解析失败: %s", exc)
        return None

    tag_name = str(data.get("tag_name") or "").strip()
    html_url = str(data.get("html_url") or "").strip()
    if not tag_name:
        logger.warning("[更新检测] GitHub API 响应缺少 tag_name")
        return None
    return _apply_release(_base_payload(current_version), tag_name, html_url, current_version, "api")


async def _fetch_via_html(current_version: str) -> Optional[Dict[str, Any]]:
    """releases 网页回退通道（不受 API 限流影响）；成功返回 payload，失败返回 None。"""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=5.0),
            trust_env=True,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            response = await client.get(RELEASES_HTML_URL)
    except Exception as exc:
        logger.warning("[更新检测] releases 网页查询失败: %s", exc)
        return None

    if response.status_code != 200:
        logger.warning("[更新检测] releases 网页返回 HTTP %s", response.status_code)
        return None

    tag_name = parse_release_tag_from_html(response.text)
    if not tag_name:
        logger.warning("[更新检测] releases 网页未解析出 tag")
        return None
    return _apply_release(
        _base_payload(current_version),
        tag_name,
        f"https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}",
        current_version,
        "html",
    )


async def _fetch_latest_release(current_version: str) -> Dict[str, Any]:
    """先 API 后网页：两个通道都失败才返回 success=false 的降级 payload。"""
    payload = await _fetch_via_api(current_version)
    if payload is not None:
        return payload
    payload = await _fetch_via_html(current_version)
    if payload is not None:
        return payload
    failed = _base_payload(current_version)
    failed["error"] = "all_channels_failed"
    return failed


def _payload_from_cache(cached: Dict[str, Any], current_version: str) -> Dict[str, Any]:
    payload = dict(cached)
    payload["has_update"] = bool(payload.get("latest_version")) and is_newer(
        payload.get("latest_version"), current_version
    )
    return payload


async def check_for_updates(current_version: str, force: bool = False) -> Dict[str, Any]:
    """检测 GitHub 最新 Release 并给出是否可更新的结论（带内存缓存）。

    ``force=True``（用户手动点击版本号触发）时绕过缓存直接查询 GitHub，
    并用最新结果刷新缓存。手动点击频率低，不会打爆未认证限流。
    并发请求经事件循环内锁合并，同一时刻只放一个真实查询出去。
    """
    now = time.time()
    cached = _cache["payload"]
    if not force and cached is not None and now < _cache["expires_at"]:
        return _payload_from_cache(cached, current_version)

    async with _inflight_lock():
        # 拿到锁后再看一次缓存：等待期间可能有并发请求已经刷新过
        now = time.time()
        cached = _cache["payload"]
        if not force and cached is not None and now < _cache["expires_at"]:
            return _payload_from_cache(cached, current_version)

        payload = await _fetch_latest_release(current_version)
        _cache["payload"] = payload
        _cache["expires_at"] = now + (
            _SUCCESS_CACHE_TTL if payload.get("success") else _FAILURE_CACHE_TTL
        )
        return payload
