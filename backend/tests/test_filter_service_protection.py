"""FilterService 防空任务保护的精确判定回归测试。

背景（用户实测 bug）：旧逻辑「目录中只有 MP3 格式 → pattern 含 mp3 的规则一律
静默禁用」会吞掉用户自定义规则（如 (?i).*mp3.*），导致 MP3 文件与 02_MP3版
文件夹未被过滤且无任何提示。新逻辑：
- 目录中还有其他文件（图片/文档等）→ 尊重用户规则，正常过滤；
- 音频会被全部过滤且目录无其他文件 → 才禁用命中音频的规则，并返回被禁用
  规则名（由调用方写进任务进度与返回值，明示不再静默）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.filter_service import FilterService  # noqa: E402


def _rule(name, pattern, target="all", enabled=True):
    return FilterService()._create_filter_rule(name, pattern, target, "exclude", enabled)


def _walk(files):
    # 单层目录：[(root, dirs, files)]，dirs 为空即可覆盖文件维度判定
    return [("/root/RJ000000000", [], list(files))]


def test_mixed_formats_respect_user_rule():
    """wav+mp3 混合：mp3 命中过滤、wav 保留，保护不触发（旧 bug 场景）。"""
    service = FilterService()
    rules = [_rule("用户规则", r"(?i).*mp3.*")]
    walk = _walk(["01.wav", "02.mp3", "02_MP3版/03.mp3"])
    new_rules, disabled = service._resolve_audio_wipeout_protection(rules, walk)
    assert disabled == []
    assert service._should_filter_file("02.mp3", new_rules) is True
    assert service._should_filter_file("01.wav", new_rules) is False
    # 文件夹名匹配（target=all）
    assert service._should_filter_dir("02_MP3版", new_rules) is True


def test_mp3_only_without_other_files_triggers_protection():
    """仅 mp3 且无其他文件：音频会被全灭 → 保护触发并返回被禁用规则名。"""
    service = FilterService()
    rules = [_rule("用户规则", r"(?i).*mp3.*")]
    walk = _walk(["01.mp3", "02.mp3"])
    new_rules, disabled = service._resolve_audio_wipeout_protection(rules, walk)
    assert disabled == ["用户规则"]
    # 禁用后 mp3 保留（防空任务）
    assert service._should_filter_file("01.mp3", new_rules) is False


def test_mp3_only_with_other_files_respects_user_rule():
    """仅 mp3 但目录里还有图片等文件：过滤后任务仍有内容 → 尊重用户规则。"""
    service = FilterService()
    rules = [_rule("用户规则", r"(?i).*mp3.*")]
    walk = _walk(["01.mp3", "cover.jpg", "特典.pdf"])
    new_rules, disabled = service._resolve_audio_wipeout_protection(rules, walk)
    assert disabled == []
    assert service._should_filter_file("01.mp3", new_rules) is True


def test_non_mp3_rules_not_touched():
    """不含 mp3 的规则不受保护逻辑影响。"""
    service = FilterService()
    rules = [_rule("SE规则", r"(?:SE|音効?)(?:な無し|CUT).*\.wav$")]
    walk = _walk(["01.mp3", "02.mp3"])
    new_rules, disabled = service._resolve_audio_wipeout_protection(rules, walk)
    # SE 规则不匹配 mp3 音频 → 音频不会全灭 → 不禁用
    assert disabled == []
    assert new_rules[0].enabled is True


def test_case_insensitive_user_pattern_matches_mp3_folder():
    """用户规则 (?i).*mp3.* 对 02_MP3版 目录名匹配（大小写不敏感）。"""
    service = FilterService()
    rules = [_rule("用户规则", r"(?i).*mp3.*")]
    assert service._should_filter_dir("02_MP3版", rules) is True
    assert service._should_filter_file("track.MP3", rules) is True
