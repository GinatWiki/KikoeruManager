import asyncio

import httpx
import pytest

from app.core import update_check_service
from app.core.update_check_service import (
    is_newer,
    parse_release_tag_from_html,
    parse_version,
)

HTML_RELEASE_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta property="og:url" content="/GinatWiki/KikoeruManager/releases/tag/v2.4.54">
  <meta property="og:title" content="Release v2.4.54 &middot; GinatWiki/KikoeruManager">
</head>
<body><a href="/GinatWiki/KikoeruManager/releases/tag/v2.4.54">v2.4.54</a></body>
</html>
"""


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeClient:
    """按 URL 路由的假 httpx.AsyncClient；记录调用顺序供断言。"""

    def __init__(self, route, call_log=None, delay=0.0):
        self._route = route
        self._call_log = call_log
        self._delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        if self._call_log is not None:
            self._call_log.append(url)
        if self._delay:
            await asyncio.sleep(self._delay)
        if isinstance(self._route, dict):
            resp = self._route.get(url)
        else:
            resp = self._route.pop(0)
        if resp is None:
            raise httpx.ConnectError("mock connect error")
        return resp


def test_parse_version_accepts_v_prefix_and_plain():
    assert parse_version("2.4.52") == (2, 4, 52)
    assert parse_version("v2.4.52") == (2, 4, 52)
    assert parse_version("V2.4.52") == (2, 4, 52)
    assert parse_version(" 2.4.52 ") == (2, 4, 52)


def test_parse_version_rejects_invalid():
    assert parse_version("") is None
    assert parse_version("dev") is None
    assert parse_version("2.4") is None
    assert parse_version("2.4.52.1") is None


def test_is_newer_compares_semver():
    assert is_newer("v2.4.53", "2.4.52") is True
    assert is_newer("v2.4.52", "2.4.52") is False
    assert is_newer("v2.4.51", "2.4.52") is False
    assert is_newer("v2.5.0", "2.4.52") is True
    assert is_newer("v3.0.0", "2.9.99") is True


def test_is_newer_falls_back_safely_on_invalid_input():
    assert is_newer("dev", "2.4.52") is False
    assert is_newer("v2.4.53", "dev") is False
    assert is_newer("", "") is False


def test_parse_release_tag_from_html_relative_og_url():
    assert parse_release_tag_from_html(HTML_RELEASE_PAGE) == "v2.4.54"


def test_parse_release_tag_from_html_absolute_og_url():
    html = '<meta property="og:url" content="https://github.com/GinatWiki/KikoeruManager/releases/tag/v2.4.55">'
    assert parse_release_tag_from_html(html) == "v2.4.55"


def test_parse_release_tag_from_html_falls_back_to_tag_path():
    html = '<a href="/GinatWiki/KikoeruManager/releases/tag/v2.4.56">'
    assert parse_release_tag_from_html(html) == "v2.4.56"


def test_parse_release_tag_from_html_rejects_garbage():
    assert parse_release_tag_from_html("") is None
    assert parse_release_tag_from_html("<html><body>nothing here</body></html>") is None


@pytest.mark.asyncio
async def test_check_for_updates_caches_and_force_bypasses(monkeypatch):
    calls = {"count": 0}

    async def fake_fetch(current_version):
        calls["count"] += 1
        return {
            "success": True,
            "repo": update_check_service.GITHUB_REPO,
            "current_version": current_version,
            "latest_version": "2.4.54",
            "latest_tag": "v2.4.54",
            "has_update": is_newer("v2.4.54", current_version),
            "release_url": "https://github.com/GinatWiki/KikoeruManager/releases/tag/v2.4.54",
            "checked_at": 0,
        }

    monkeypatch.setattr(update_check_service, "_fetch_latest_release", fake_fetch)
    update_check_service._cache["payload"] = None

    first = await update_check_service.check_for_updates("2.4.53")
    second = await update_check_service.check_for_updates("2.4.53")
    assert calls["count"] == 1, "第二次调用应命中内存缓存，不再请求 GitHub"
    assert first["has_update"] is True
    assert second["has_update"] is True

    forced = await update_check_service.check_for_updates("2.4.53", force=True)
    assert calls["count"] == 2, "force=True 应绕过缓存重新请求"
    assert forced["has_update"] is True

    # 不传 force 时应再次命中缓存（上一轮 force 刷新过）
    await update_check_service.check_for_updates("2.4.53")
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_api_403_falls_back_to_html_channel(monkeypatch):
    calls = []
    fake = FakeClient(
        {
            update_check_service.RELEASES_LATEST_URL: FakeResponse(403),
            update_check_service.RELEASES_HTML_URL: FakeResponse(
                200, text=HTML_RELEASE_PAGE
            ),
        },
        call_log=calls,
    )
    monkeypatch.setattr(update_check_service.httpx, "AsyncClient", lambda *a, **k: fake)
    update_check_service._cache["payload"] = None

    payload = await update_check_service.check_for_updates("2.4.53", force=True)

    assert payload["success"] is True
    assert payload["latest_tag"] == "v2.4.54"
    assert payload["latest_version"] == "2.4.54"
    assert payload["has_update"] is True
    assert payload["source"] == "html"
    assert payload["release_url"].endswith("/releases/tag/v2.4.54")
    assert calls == [
        update_check_service.RELEASES_LATEST_URL,
        update_check_service.RELEASES_HTML_URL,
    ], "应先走 API，403 后回退网页通道"


@pytest.mark.asyncio
async def test_both_channels_fail_returns_degraded_payload(monkeypatch):
    calls = []
    fake = FakeClient(
        {
            update_check_service.RELEASES_LATEST_URL: FakeResponse(403),
            update_check_service.RELEASES_HTML_URL: FakeResponse(200, text="<html></html>"),
        },
        call_log=calls,
    )
    monkeypatch.setattr(update_check_service.httpx, "AsyncClient", lambda *a, **k: fake)
    update_check_service._cache["payload"] = None

    payload = await update_check_service.check_for_updates("2.4.53", force=True)

    assert payload["success"] is False
    assert payload["has_update"] is False
    assert payload["error"] == "all_channels_failed"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_concurrent_checks_coalesce_into_single_fetch(monkeypatch):
    calls = []
    fake = FakeClient(
        {
            update_check_service.RELEASES_LATEST_URL: FakeResponse(
                200,
                json_data={
                    "tag_name": "v2.4.54",
                    "html_url": "https://github.com/GinatWiki/KikoeruManager/releases/tag/v2.4.54",
                },
            )
        },
        call_log=calls,
        delay=0.05,
    )
    monkeypatch.setattr(update_check_service.httpx, "AsyncClient", lambda *a, **k: fake)
    update_check_service._cache["payload"] = None

    results = await asyncio.gather(
        update_check_service.check_for_updates("2.4.53"),
        update_check_service.check_for_updates("2.4.53"),
    )

    assert calls == [update_check_service.RELEASES_LATEST_URL], (
        "并发请求应经锁合并，只放一个真实查询出去"
    )
    assert all(r["success"] and r["latest_tag"] == "v2.4.54" for r in results)
