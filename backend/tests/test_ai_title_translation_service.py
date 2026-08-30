# -*- coding: utf-8 -*-
"""AI 标题汉化服务的输出健壮性回归测试。

历史事故：max_tokens 硬编码 200，文件级重命名返回「标题 + 全部文件名」的 JSON
映射时输出被截断成半个 JSON；旧清理逻辑把 JSON 残骸当翻译成功结果传下去，
项目文件夹被重命名成 `RJxxxx {` 之类的垃圾名。这里固定回归：
- finish_reason=length（截断）必须判失败并进入重试；
- 有 `{` 无 `}` 的不完整 JSON 必须判失败；
- 完整 JSON 映射、纯文本翻译仍按原逻辑接受。
"""
from __future__ import annotations

import sys
import types

import pytest

from app.core.ai_title_translation_service import AITitleTranslationService


def _make_response(content: str, finish_reason: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _install_fake_litellm(monkeypatch: pytest.MonkeyPatch, responses: list) -> None:
    """把假 litellm 塞进 sys.modules，acompletion 依次弹出 responses。"""
    queue = list(responses)

    async def acompletion(**kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(acompletion=acompletion))


def _config(**overrides) -> dict:
    base = {
        "enabled": True,
        "model": "openai/gpt-4o-mini",
        "api_key": "sk-test",
        "max_retries": 0,
        "temperature": 0.1,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_truncated_json_finish_reason_length_is_failure(monkeypatch):
    _install_fake_litellm(monkeypatch, [_make_response('{"作品": "标题", "01文件": "01翻', finish_reason="length")])
    service = AITitleTranslationService()
    result = await service.translate_single("作品", _config())
    assert result["success"] is False
    assert result["translated_title"] is None
    assert "截断" in str(result.get("error", {}).get("suggestion", "")) or result.get("status") == "failed"


@pytest.mark.asyncio
async def test_incomplete_json_without_closing_brace_is_failure(monkeypatch):
    _install_fake_litellm(monkeypatch, [_make_response('{\n  "作品": "标题",\n  "01文件": "01翻译', finish_reason="stop")])
    service = AITitleTranslationService()
    result = await service.translate_single("作品", _config())
    assert result["success"] is False
    assert result["translated_title"] is None


@pytest.mark.asyncio
async def test_truncation_retries_then_succeeds(monkeypatch):
    _install_fake_litellm(monkeypatch, [
        _make_response('{"作品": "标题", "01文件": "01翻', finish_reason="length"),
        _make_response('{"作品": "标题翻译", "01文件": "01翻译"}', finish_reason="stop"),
    ])
    service = AITitleTranslationService()
    result = await service.translate_single("作品", _config(max_retries=1))
    assert result["success"] is True
    assert "标题翻译" in result["translated_title"]
    assert "01翻译" in result["translated_title"]


@pytest.mark.asyncio
async def test_complete_json_mapping_returns_redumped_json(monkeypatch):
    payload = '{"作品": "标题翻译", "01文件": "01翻译"}'
    _install_fake_litellm(monkeypatch, [_make_response(payload)])
    service = AITitleTranslationService()
    result = await service.translate_single("作品", _config())
    assert result["success"] is True
    assert "标题翻译" in result["translated_title"]
    assert "01翻译" in result["translated_title"]


@pytest.mark.asyncio
async def test_plain_text_translation_still_accepted(monkeypatch):
    _install_fake_litellm(monkeypatch, [_make_response("这是翻译后的标题")])
    service = AITitleTranslationService()
    result = await service.translate_single("タイトル", _config())
    assert result["success"] is True
    assert result["translated_title"] == "这是翻译后的标题"


def test_completion_kwargs_uses_configured_max_tokens():
    service = AITitleTranslationService()
    assert service._completion_kwargs(_config(max_tokens=8192), [])["max_tokens"] == 8192
    assert service._completion_kwargs(_config(), [])["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# /api/ai-title-translation/test 接口回归
#
# v2.5.26 用户实测：设置页"AI 标题汉化 → 测试连接"点击无反应。双重根因：
#   1. 前端 <stateful-button :click="..."> 传了不存在的 prop，组件只认 @click；
#   2. 后端 /api/ai-title-translation/test 接口根本不存在（404）。
# 这里固定后端接口行为：普通模式用表单草稿参数 + 掩码 Key 回退；
# use_ai_subtitle_api=true 时复用字幕配对连接配置。
# ---------------------------------------------------------------------------

def _install_fake_litellm_capture(monkeypatch: pytest.MonkeyPatch, capture: dict):
    async def acompletion(**kwargs):
        capture["kwargs"] = kwargs
        return _make_response('{"テスト": "测试"}')

    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(acompletion=acompletion))


def test_test_endpoint_exists_and_translates(client, monkeypatch):
    capture: dict = {}
    _install_fake_litellm_capture(monkeypatch, capture)

    resp = client.post(
        "/api/ai-title-translation/test",
        json={"config": {"enabled": True, "model": "openai/gpt-4o-mini", "api_key": "sk-test"}},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["translated_title"] == "测试"
    assert capture["kwargs"]["model"] == "openai/gpt-4o-mini"


def test_test_endpoint_reuses_subtitle_api_config(client, monkeypatch):
    capture: dict = {}
    _install_fake_litellm_capture(monkeypatch, capture)

    saved_model = client.app.state  # noqa: F841  仅确保 app 可访问
    from app.config.settings import get_config
    cfg = get_config()
    monkeypatch.setattr(cfg, "ai_subtitle_matching", type(
        "SubCfg",
        (),
        {
            "model": "openai/subtitle-model",
            "api_key": "sk-sub",
            "api_base": "https://sub.example.com/v1",
            "api_version": "",
            "organization": "",
            "proxy_url": "",
            "timeout_seconds": 45,
            "max_retries": 1,
            "temperature": 0.2,
        },
    )(), raising=False)

    resp = client.post(
        "/api/ai-title-translation/test",
        json={"config": {"use_ai_subtitle_api": True}},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert capture["kwargs"]["model"] == "openai/subtitle-model"
    assert capture["kwargs"]["api_key"] == "sk-sub"
    assert capture["kwargs"]["api_base"] == "https://sub.example.com/v1"


def test_test_endpoint_masked_api_key_falls_back_to_disk(client, monkeypatch):
    capture: dict = {}
    _install_fake_litellm_capture(monkeypatch, capture)

    import app.api.routes as routes_mod
    monkeypatch.setattr(
        routes_mod,
        "_read_ai_title_translation_api_key_from_disk",
        lambda: "sk-saved-on-disk",
    )

    resp = client.post(
        "/api/ai-title-translation/test",
        json={"config": {"enabled": True, "model": "openai/gpt-4o-mini", "api_key": "********"}},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert capture["kwargs"]["api_key"] == "sk-saved-on-disk"
