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
