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
