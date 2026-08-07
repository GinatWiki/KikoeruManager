"""网盘分享文本解析与解压密码提取测试。"""

from app.core.netdisk_link_parser import (
    extract_archive_passwords,
    extract_baidu_urls,
    extract_http_urls,
    extract_share_inputs,
)


def test_extract_share_inputs_from_prose_markdown_and_code():
    text = "分享：[网盘](https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA) 提取码：38a2"
    shares = extract_share_inputs(text, platform="baidu")

    assert len(shares) == 1
    assert shares[0]["pass_code"] == "38a2"
    assert shares[0]["share_url"] == "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA?pwd=38a2"


def test_normalize_obfuscated_baidu_link_and_fullwidth_punctuation():
    text = "链接：pan点baidu点com/s/13EU1GlLvUULM43mkqhoZxA 提取码：abcd"
    shares = extract_share_inputs(text, platform="baidu")

    assert len(shares) == 1
    assert shares[0]["share_url"] == "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA?pwd=abcd"


def test_baidu_separator_next_line_code_and_duplicate_merge():
    text = (
        "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA----38a2\n"
        "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA?pwd=38a2\n"
        "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA"
    )
    shares = extract_share_inputs(text, platform="baidu")

    assert len(shares) == 1
    assert shares[0]["pass_code"] == "38a2"
    assert shares[0]["share_url"] == "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA?pwd=38a2"


def test_http_urls_keep_direct_and_share_platforms():
    text = (
        "https://drive.google.com/file/d/abc123/view\n"
        "https://mypikpak.com/s/xyz123\n"
        "提取码：abcd\n"
        "https://example.com/file.bin"
    )
    urls = extract_http_urls(text)

    assert "https://drive.google.com/file/d/abc123/view" in urls
    assert "https://mypikpak.com/s/xyz123?pwd=abcd" in urls
    assert "https://example.com/file.bin" in urls


def test_archive_password_is_not_used_as_share_code():
    text = "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA 提取码：abcd 解压密码：southplus@mzh1051"
    shares = extract_share_inputs(text, platform="baidu")
    passwords = extract_archive_passwords(text)

    assert shares[0]["pass_code"] == "abcd"
    assert shares[0]["share_url"] == "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA?pwd=abcd"
    assert passwords == ["southplus@mzh1051"]


def test_generic_password_with_archive_context_is_imported():
    text = "压缩包 密码：hello123\nhttps://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA"

    assert extract_archive_passwords(text) == ["hello123"]


def test_generic_password_after_share_is_share_code_not_vault():
    text = "https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA 密码：abcd"
    shares = extract_share_inputs(text, platform="baidu")

    assert shares[0]["pass_code"] == "abcd"
    assert extract_archive_passwords(text) == []


def test_extract_baidu_urls_returns_normalized_share_list():
    urls = extract_baidu_urls(
        "文字说明 https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA 提取码：38a2"
    )

    assert urls == ["https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA?pwd=38a2"]

def test_exact_user_text_extracts_komo():
    text = """链接: [https://pan.baidu.com/s/104v4TdB-3VDrSQ7zsquwfg?pwd=thsv](https://pan.baidu.com/s/104v4TdB-3VDrSQ7zsquwfg?pwd=thsv) 提取码: thsv
解压komo"""
    shares = extract_share_inputs(text, platform="baidu")
    passwords = extract_archive_passwords(text)

    assert shares[0]["pass_code"] == "thsv"
    assert shares[0]["share_url"] == "https://pan.baidu.com/s/104v4TdB-3VDrSQ7zsquwfg?pwd=thsv"
    assert passwords == ["komo"]


def test_archive_password_variants():
    cases = [
        ("解压密码：komo", ["komo"]),
        ("解压密码komo", ["komo"]),
        ("解压码：komo", ["komo"]),
        ("解压码komo", ["komo"]),
        ("解压：komo", ["komo"]),
        ("解压 komo", ["komo"]),
        ("解压是komo", ["komo"]),
        ("解压 是 komo", ["komo"]),
        ("密码：komo", ["komo"]),
        ("密码是komo", ["komo"]),
        ("压缩包密码：komo", ["komo"]),
        ("压缩密码komo", ["komo"]),
        ("rar密码: komo", ["komo"]),
        ("zip密码：komo", ["komo"]),
        ("7z密码 komo", ["komo"]),
        ("解压密码是komo", ["komo"]),
        ("解压码为komo", ["komo"]),
        ("密码为komo", ["komo"]),
        ("压缩密码是komo", ["komo"]),
    ]
    for text, expected in cases:
        assert extract_archive_passwords(text) == expected, text


def test_generic_password_same_as_share_code_not_imported():
    text = "链接：https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA 提取码：thsv\n密码：thsv"
    shares = extract_share_inputs(text, platform="baidu")

    assert shares[0]["pass_code"] == "thsv"
    assert extract_archive_passwords(text) == []


def test_archive_password_differs_from_share_code_imported():
    text = "链接：https://pan.baidu.com/s/13EU1GlLvUULM43mkqhoZxA 提取码：thsv\n密码：komo"

    assert extract_archive_passwords(text) == ["komo"]


def test_shorthand_does_not_capture_common_words():
    assert extract_archive_passwords("解压失败") == []
    assert extract_archive_passwords("解压完成后") == []
