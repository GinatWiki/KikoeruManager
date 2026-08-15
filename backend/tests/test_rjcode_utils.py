from app.core.rjcode_utils import (
    canonicalize_rj_input,
    extract_rjcode,
    extract_rjcode_from_path,
    scan_existing_folder_candidates,
)


def test_canonicalize_rj_input_supports_three_input_formats():
    # 三种历史写法都要能搜索
    assert canonicalize_rj_input("RJ01144225") == "RJ01144225"
    assert canonicalize_rj_input("01144225") == "RJ01144225"
    assert canonicalize_rj_input("1144225") == "RJ01144225"


def test_canonicalize_rj_input_strips_impurities():
    # 粘贴内容带表情 / 状态标记 / 大小写混乱时也能正确归一化
    assert canonicalize_rj_input("RJ01144225🔴") == "RJ01144225"
    assert canonicalize_rj_input("rj01144225") == "RJ01144225"
    assert canonicalize_rj_input("RJ01144225 已下载") == "RJ01144225"
    assert canonicalize_rj_input("🔴1144225") == "RJ01144225"


def test_canonicalize_rj_input_keeps_six_digit_legacy_codes():
    # 6 位旧作编号保持原样，不补零，避免查询到错误作品
    assert canonicalize_rj_input("123456") == "RJ123456"
    assert canonicalize_rj_input("RJ123456") == "RJ123456"
    assert canonicalize_rj_input("VJ01570159") == "VJ01570159"


def test_canonicalize_rj_input_rejects_invalid_input():
    assert canonicalize_rj_input("") is None
    assert canonicalize_rj_input("abc") is None
    assert canonicalize_rj_input("12345") is None
    assert canonicalize_rj_input("011442250") is None


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_extract_rjcode_supports_prefixed_and_numeric_names():
    assert extract_rjcode("RJ01234567 标题") == "RJ01234567"
    assert extract_rjcode("39.RJ01570159") == "RJ01570159"
    assert extract_rjcode("01503161") == "RJ01503161"
    assert extract_rjcode("社团名") is None


def test_extract_rjcode_from_path_searches_nested_folders(tmp_path):
    root = _mkdir(tmp_path / "existing" / "社团A")
    _mkdir(root / "RJ01234567 作品")

    assert extract_rjcode_from_path(str(root)) == "RJ01234567"


def test_scan_existing_folder_candidates_returns_top_level_rj_folder(tmp_path):
    existing_root = _mkdir(tmp_path / "existing")
    work_dir = _mkdir(existing_root / "RJ01234567 作品")
    (work_dir / "track01.wav").write_text("audio", encoding="utf-8")

    candidates = scan_existing_folder_candidates(str(existing_root))

    assert len(candidates) == 1
    assert candidates[0]["path"] == str(work_dir)
    assert candidates[0]["rjcode"] == "RJ01234567"
    assert candidates[0]["relative_path"] == "RJ01234567 作品"
    assert candidates[0]["is_nested"] is False


def test_scan_existing_folder_candidates_finds_circle_nested_rj_folder(tmp_path):
    existing_root = _mkdir(tmp_path / "existing")
    work_dir = _mkdir(existing_root / "社团A" / "RJ01234567 作品")
    (work_dir / "track01.wav").write_text("audio", encoding="utf-8")

    candidates = scan_existing_folder_candidates(str(existing_root))

    assert len(candidates) == 1
    assert candidates[0]["path"] == str(work_dir)
    assert candidates[0]["rjcode"] == "RJ01234567"
    assert candidates[0]["source_root_name"] == "社团A"
    assert candidates[0]["relative_path"] == "社团A/RJ01234567 作品"
    assert candidates[0]["is_nested"] is True
    assert candidates[0]["scan_depth"] == 2


def test_scan_existing_folder_candidates_keeps_multiple_nested_works_separate(tmp_path):
    existing_root = _mkdir(tmp_path / "existing")
    first = _mkdir(existing_root / "社团A" / "RJ01111111 作品1")
    second = _mkdir(existing_root / "社团A" / "RJ02222222 作品2")
    _mkdir(existing_root / "社团B" / "RJ03333333 作品3")

    candidates = scan_existing_folder_candidates(str(existing_root))
    by_rjcode = {item["rjcode"]: item for item in candidates}

    assert set(by_rjcode) == {"RJ01111111", "RJ02222222", "RJ03333333"}
    assert by_rjcode["RJ01111111"]["path"] == str(first)
    assert by_rjcode["RJ02222222"]["path"] == str(second)
    assert by_rjcode["RJ01111111"]["source_root_name"] == "社团A"
    assert by_rjcode["RJ02222222"]["source_root_name"] == "社团A"


def test_scan_existing_folder_candidates_skips_unrecognized_top_folder(tmp_path):
    existing_root = _mkdir(tmp_path / "existing")
    unknown = _mkdir(existing_root / "社团A" / "没有RJ")
    (unknown / "note.txt").write_text("no rj here", encoding="utf-8")

    candidates = scan_existing_folder_candidates(str(existing_root))

    assert candidates == []


def test_scan_existing_folder_candidates_does_not_return_circle_folder_when_nested_work_exists(tmp_path):
    existing_root = _mkdir(tmp_path / "existing")
    circle = _mkdir(existing_root / "Whisp")
    work_dir = _mkdir(circle / "[Whisp][RJ01234567] 作品")
    (work_dir / "track01.wav").write_text("audio", encoding="utf-8")

    candidates = scan_existing_folder_candidates(str(existing_root))

    assert len(candidates) == 1
    assert candidates[0]["path"] == str(work_dir)
    assert candidates[0]["rjcode"] == "RJ01234567"
    assert candidates[0]["source_root_name"] == "Whisp"
