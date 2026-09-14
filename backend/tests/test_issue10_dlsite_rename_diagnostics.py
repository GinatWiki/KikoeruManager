"""Issue #10 回归：api-rename 元数据失败时的诊断质量。

用户场景（EXE 版，直连 DLsite 被墙）：右键 API 重命名报 422
「DLsite 元数据不可用，已跳过重命名」——提示不可执行，用户以为软件坏了。
实际是网络问题（voicehub.top 能 404 = 出网正常，dlsite.com 连接超时 = 被墙）。

修复点：
1. _record_dlsite_http_failure / _record_dlsite_metadata_failure：
   裸 str(exc) 为空（httpx 异常常见）时兜底取类型名，熔断日志 last_error= 不再空。
2. metadata_service.fetch 降级 minimal 时：
   有近期传输失败 → rename_skipped_reason 标「网络不可达」（带原因）。
3. _api_rename_metadata_skip_reason：
   网络失败给出可执行提示（配代理/换节点后重试），与「作品不存在」区分。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core import dlsite_service as dlsite_mod
from app.core import metadata_service as metadata_mod


# ---------------------------------------------------------------------------
# 修复点 1：空 str 异常的 last_error 兜底
# ---------------------------------------------------------------------------

def test_http_circuit_last_error_fallback_for_empty_str_exc():
    # httpx.ConnectTimeout("") —— str() 为空串的场景（信息在 __cause__ 里）
    exc = dlsite_mod.httpx.ConnectTimeout("")
    dlsite_mod._record_dlsite_http_failure(exc)
    assert dlsite_mod._DLSITE_HTTP_CIRCUIT["last_error"] == "ConnectTimeout"
    # 未知 None 也不崩
    dlsite_mod._record_dlsite_http_failure(None)
    assert dlsite_mod._DLSITE_HTTP_CIRCUIT["last_error"] == "unknown"
    # 正常文本原样保留
    dlsite_mod._record_dlsite_http_failure(Exception("boom"))
    assert dlsite_mod._DLSITE_HTTP_CIRCUIT["last_error"] == "boom"


def test_transport_failure_last_error_fallback():
    exc = dlsite_mod.httpx.ConnectError("  ")
    dlsite_mod._record_dlsite_transport_failure(exc)
    assert dlsite_mod._DLSITE_TRANSPORT_FAILURE["error"] == "ConnectError"
    # recent_dlsite_transport_failure 也能读到
    assert dlsite_mod.recent_dlsite_transport_failure() == "ConnectError"


def test_metadata_circuit_last_error_fallback():
    exc = metadata_mod.httpx.ConnectTimeout("") if hasattr(metadata_mod, "httpx") else RuntimeError("")
    metadata_mod._record_dlsite_metadata_failure(exc)
    assert metadata_mod._DLSITE_METADATA_CIRCUIT["last_error"] in {
        "ConnectTimeout", "RuntimeError",
    }


# ---------------------------------------------------------------------------
# 修复点 3：skip_reason 的可执行提示
# ---------------------------------------------------------------------------

def _routes_skip_reason(metadata):
    from app.api.routes import _api_rename_metadata_skip_reason

    return _api_rename_metadata_skip_reason(metadata, "RJ01264966")


def test_skip_reason_network_unreachable_gives_actionable_hint():
    metadata = {
        "metadata_source": "minimal",
        "rename_skipped_reason": "DLsite 网络不可达（连接超时/DNS失败），请检查网络或代理后重试",
        "metadata_verification_reason": "DLsite 网络不可达: ConnectTimeout",
    }
    reason = _routes_skip_reason(metadata)
    assert "网络" in reason
    assert "重试" in reason
    # 不能是旧的笼统文案
    assert reason != "DLsite 元数据不可用，已跳过重命名"
    # 可执行指引：要提到代理或节点
    assert ("代理" in reason) or ("节点" in reason)


def test_skip_reason_not_found_vs_network():
    # voicehub/DLsite 都 404（无传输失败标记）→ 数据侧终态文案
    metadata = {
        "metadata_source": "minimal",
        "rename_skipped_reason": "所有元数据源均未找到该作品",
    }
    reason = _routes_skip_reason(metadata)
    assert "未找到" in reason


def test_skip_reason_circuit_open():
    metadata = {"metadata_source": "minimal", "dlsite_circuit_open": True}
    reason = _routes_skip_reason(metadata)
    assert "短熔断" in reason
    assert "重试" in reason


def test_skip_reason_fallback_unchanged_for_old_payload():
    # 老版本 payload（没有 rename_skipped_reason 字段）→ 原文案，向后兼容
    metadata = {"metadata_source": "minimal"}
    reason = _routes_skip_reason(metadata)
    assert reason == "DLsite 元数据不可用，已跳过重命名"


def test_skip_reason_verified_metadata_still_validated():
    # 非 minimal 的旧逻辑不受影响：未验证的元数据仍拒绝（验证层会覆盖 reason）
    metadata = {
        "metadata_source": "dlsite",
        "metadata_verification_status": "unverified",
        "metadata_verification_reason": "元数据来源缺少可验证的结构化证据",
    }
    reason = _routes_skip_reason(metadata)
    # attach_dlsite_metadata_verification 会依据输入重算 reason——
    # 这里缺 resolved_workno，返回 RJ 不一致原因（非空即代表「拒绝重命名」发生）
    assert reason
    assert reason != ""
