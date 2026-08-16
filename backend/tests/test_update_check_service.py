import pytest

from app.core import update_check_service
from app.core.update_check_service import is_newer, parse_version


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
