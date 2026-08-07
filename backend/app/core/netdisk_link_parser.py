"""网盘分享文本解析：识别链接、提取码与解压密码。"""

import hashlib
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

_ZERO_WIDTH_TRANSLATION = {
    ord("\u200b"): None,
    ord("\u200c"): None,
    ord("\u200d"): None,
    ord("\ufeff"): None,
    ord("\u2060"): None,
}

_FULLWIDTH_TRANSLATION = {0xFF01 + index: 0x21 + index for index in range(94)}

_BAIDU_HOST_HINTS = ("pan.baidu.com", "yun.baidu.com", "eyun.baidu.com")
_PIKPAK_HOST_HINTS = ("mypikpak.com", "drive.mypikpak.com")
_GOFILE_HOST_HINTS = ("gofile.io", "www.gofile.io")
_TRANSFERIT_HOST_HINTS = ("transfer.it", "www.transfer.it")
_ONEDRIVE_HOST_HINTS = ("1drv.ms", "onedrive.live.com", "onedrive.com")
_GOOGLE_DRIVE_HOST_HINTS = ("drive.google.com", "docs.google.com", "drive.usercontent.google.com")

_PLATFORM_BY_HOST: Dict[str, str] = {}
for _host in _BAIDU_HOST_HINTS:
    _PLATFORM_BY_HOST[_host] = "baidu"
for _host in _PIKPAK_HOST_HINTS:
    _PLATFORM_BY_HOST[_host] = "pikpak"
for _host in _GOFILE_HOST_HINTS:
    _PLATFORM_BY_HOST[_host] = "gofile"
for _host in _TRANSFERIT_HOST_HINTS:
    _PLATFORM_BY_HOST[_host] = "transferit"
for _host in _ONEDRIVE_HOST_HINTS:
    _PLATFORM_BY_HOST[_host] = "onedrive"
for _host in _GOOGLE_DRIVE_HOST_HINTS:
    _PLATFORM_BY_HOST[_host] = "google_drive"

_PLATFORM_ALIASES = {
    "baidu": "baidu",
    "baidu_netdisk": "baidu",
    "pikpak": "pikpak",
    "gofile": "gofile",
    "transferit": "transferit",
    "transfer.it": "transferit",
    "onedrive": "onedrive",
    "google_drive": "google_drive",
    "googledrive": "google_drive",
}

_URL_RE = re.compile(r"https?://[^\s<>\"'\u3000-\u303f\u4e00-\u9fff]+", re.IGNORECASE)
_NO_SCHEME_URL_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?:pan|yun|eyun)\.baidu\.com|(?:mypikpak|drive\.mypikpak)\.com|"
    r"(?:www\.)?gofile\.io|(?:www\.)?transfer\.it|1drv\.ms|onedrive\.live\.com|"
    r"onedrive\.com|drive\.google\.com|docs\.google\.com|drive\.usercontent\.google\.com)"
    r"(?:/[^\s<>\"'\u3000-\u303f\u4e00-\u9fff]*)?",
    re.IGNORECASE,
)
_MARKDOWN_URL_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_HTML_HREF_RE = re.compile(r"href\s*=\s*[\"'](https?://[^\"']+)[\"']", re.IGNORECASE)

_PASS_CODE_QUERY_KEYS = ("pwd", "password", "passcode", "pass_code", "code")
_PASS_CODE_RE = re.compile(r"[A-Za-z0-9]{4,12}")
_PASS_CODE_FULL_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")
_SEPARATOR_CODE_RE = re.compile(r"(?:----|---|--)\s*([A-Za-z0-9]{4,12})\s*$")

_ARCHIVE_PASSWORD_LABEL_RE = re.compile(
    r"(?:解压密码|解壓密碼|压缩包密码|壓縮包密碼|压缩密码|壓縮密碼|解压码|解壓碼|"
    r"rar密码|rar密碼|zip密码|zip密碼|7z密码|7z密碼|7z压缩密码|7z壓縮密碼|"
    r"archive\s*password|unzip\s*password|extract\s*password)"
    r"(?:\s*(?:[:：=]|是|为|為)?\s*)([^\s,，。;；、]+)",
    re.IGNORECASE,
)

_ARCHIVE_KEYWORDS = ("解压", "解壓", "压缩", "壓縮", "rar", "zip", "7z", "archive", "unzip", "extract")
_SHORTHAND_ARCHIVE_PASSWORD_RE = re.compile(
    r"(?:解压|解壓)(?:\s*(?:[:：=]|是|为|為)?\s*)(?![密碼])([A-Za-z0-9@!#$%&*+_.:/-]{3,64})",
    re.IGNORECASE,
)
_GENERIC_PASSWORD_RE = re.compile(r"密[码碼](?:\s*(?:[:：=]|是|为|為)?\s*)([^\s,，。;；、]+)")


def normalize_link_text(text: str) -> str:
    """统一文本中的零宽字符、全角字符和常见网盘混淆写法。"""
    value = str(text or "")
    value = value.translate(_ZERO_WIDTH_TRANSLATION)
    value = value.translate(_FULLWIDTH_TRANSLATION)
    value = value.replace("点", ".").replace("點", ".")
    value = value.replace("％", "%")
    return value


def _clean_url_candidate(raw: str) -> tuple[str, str]:
    url = str(raw or "").strip()
    if not url:
        return "", ""
    trailing_code = ""
    separator_match = _SEPARATOR_CODE_RE.search(url)
    if separator_match:
        trailing_code = _valid_pass_code(separator_match.group(1))
        url = url[: separator_match.start()]
    trailing_label = re.search(
        r"(?<![?&=])(?:提取码|提取口令|访问码|密[码碼]|pwd|passcode|pass_code|password|code|key)"
        r"\s*[:：=]?\s*([A-Za-z0-9]{4,12})$",
        url,
        re.IGNORECASE,
    )
    if trailing_label:
        trailing_code = _valid_pass_code(trailing_label.group(1))
        url = url[: trailing_label.start()]
    else:
        bare_trailing = re.search(r":([A-Za-z0-9]{4,12})$", url)
        if bare_trailing:
            trailing_code = _valid_pass_code(bare_trailing.group(1))
            url = url[: bare_trailing.start()]
    while url and url[-1] in "。，；：、！？)]}>》」】'\"`":
        url = url[:-1]
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "", ""
    hostname = str(parsed.hostname or "").lower()
    if any(hint in hostname for hint in _PLATFORM_BY_HOST):
        return url, trailing_code
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url, trailing_code
    return "", ""


def _url_candidates(text: str) -> List[Dict[str, str]]:
    """提取文本中的候选链接，返回 {url, start, end}。"""
    candidates: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add(raw: str, start: int, end: int) -> None:
        cleaned, code = _clean_url_candidate(raw)
        if not cleaned:
            return
        key = cleaned.rstrip("/")
        if key in seen:
            return
        seen.add(key)
        candidates.append({"url": cleaned, "start": start, "end": end, "code": code})

    for match in _MARKDOWN_URL_RE.finditer(text):
        add(match.group(1), match.start(), match.end())
    for match in _HTML_HREF_RE.finditer(text):
        add(match.group(1), match.start(), match.end())
    for match in _URL_RE.finditer(text):
        add(match.group(0), match.start(), match.end())
    for match in _NO_SCHEME_URL_RE.finditer(text):
        add(match.group(0), match.start(), match.end())
    candidates.sort(key=lambda item: item["start"])
    return candidates


def _platform_for_url(url: str) -> str:
    try:
        hostname = str(urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    for hint, platform in _PLATFORM_BY_HOST.items():
        if hint in hostname:
            return platform
    return ""


def _platform_matches(platform: str, expected: Optional[str]) -> bool:
    if not expected:
        return True
    normalized = _PLATFORM_ALIASES.get(str(platform or "").lower(), str(platform or "").lower())
    expected_normalized = _PLATFORM_ALIASES.get(str(expected or "").lower(), str(expected or "").lower())
    return bool(normalized and normalized == expected_normalized)


def _valid_pass_code(value: str) -> str:
    code = str(value or "").strip()
    return code if _PASS_CODE_FULL_RE.match(code) else ""


def _pass_code_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query or "")
    for key in _PASS_CODE_QUERY_KEYS:
        values = query.get(key) or []
        if values:
            code = _valid_pass_code(str(values[0]))
            if code:
                return code
    fragment = unquote(parsed.fragment or "")
    if not fragment:
        return ""
    if "=" in fragment and fragment.split("=", 1)[0].lower() in {
        "pwd",
        "password",
        "passcode",
        "pass_code",
        "code",
        "p",
    }:
        fragment = fragment.split("=", 1)[1]
    return _valid_pass_code(fragment)


def _segment_has_archive_context(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(keyword in lowered for keyword in _ARCHIVE_KEYWORDS)


def _extract_pass_code_from_segment(value: str, *, allow_bare: bool = False) -> str:
    segment = str(value or "").strip()
    if not segment:
        return ""
    archive_context = _segment_has_archive_context(segment)
    label_pattern = (
        r"(?:提取码|提取口令|访问码|pwd|passcode|pass_code|code|key)\s*[:：=]?\s*([A-Za-z0-9]{4,12})"
        if archive_context
        else r"(?:提取码|提取口令|访问码|pwd|passcode|pass_code|password|code|key)\s*[:：=]?\s*([A-Za-z0-9]{4,12})"
    )
    explicit = re.search(label_pattern, segment, re.IGNORECASE)
    if explicit:
        return _valid_pass_code(explicit.group(1))
    if not archive_context:
        generic = re.search(r"密[码碼]\s*[:：=]?\s*([A-Za-z0-9]{4,12})", segment)
        if generic:
            return _valid_pass_code(generic.group(1))
    if allow_bare:
        bare = _PASS_CODE_RE.search(segment)
        if bare:
            return bare.group(0)
    return ""


def _extract_pass_code_from_line(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _segment_has_archive_context(text):
        return ""
    return _extract_pass_code_from_segment(text, allow_bare=True)


def _share_identity(url: str) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    query_pairs = [
        (key, val)
        for key, val in parse_qs(parsed.query or "", keep_blank_values=True).items()
        if str(key or "").lower() not in _PASS_CODE_QUERY_KEYS
    ]
    from urllib.parse import urlencode

    return parsed._replace(query=urlencode(query_pairs, doseq=True), fragment="").geturl().rstrip("/")


def _append_pass_code(url: str, code: str) -> str:
    value = str(url or "").strip()
    if not value or not code:
        return value
    if _pass_code_from_url(value):
        return value
    return f"{value}{'&' if '?' in value else '?'}pwd={quote(code)}"


def _share_id_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    match = re.search(r"/s/([A-Za-z0-9_-]+)", parsed.path or "")
    if match:
        return match.group(1)
    query = parse_qs(parsed.query or "")
    for key in ("surl", "shareid", "uk", "id", "share_id"):
        values = query.get(key) or []
        if values:
            return str(values[0]).strip()
    return ""


def _build_share_dict(url: str, code: str, platform: str) -> Dict[str, str]:
    cleaned = _append_pass_code(url, code)
    share_id = _share_id_from_url(url)
    title = f"{platform} 分享 {share_id[:10]}" if share_id else f"{platform} 分享"
    return {
        "share_url": cleaned,
        "raw_url": url,
        "shorturl": share_id,
        "share_id": share_id or hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest()[:12],
        "pass_code": code,
        "title": title,
        "platform": platform,
    }


def extract_share_inputs(text: str, platform: Optional[str] = None) -> List[Dict[str, str]]:
    """从一段文本中识别网盘分享链接，并把附近的提取码合并到分享上。"""
    normalized = normalize_link_text(text)
    shares: List[Dict[str, str]] = []
    seen: Dict[str, int] = {}
    last_index: Optional[int] = None

    for line in re.split(r"[\r\n]+", normalized):
        candidates = _url_candidates(line)
        if candidates:
            previous_end = 0
            for index, candidate in enumerate(candidates):
                platform_name = _platform_for_url(candidate["url"])
                if not _platform_matches(platform_name, platform):
                    previous_end = candidate["end"]
                    continue
                after_start = candidate["end"]
                next_start = candidates[index + 1]["start"] if index + 1 < len(candidates) else len(line)
                before = line[previous_end:candidate["start"]]
                after = line[after_start:next_start]
                previous_end = candidate["end"]
                code = _valid_pass_code(str(candidate.get("code") or ""))
                if not code:
                    code = _pass_code_from_url(candidate["url"])
                if not code:
                    code = _extract_pass_code_from_segment(after, allow_bare=True)
                if not code:
                    code = _extract_pass_code_from_segment(before, allow_bare=False)
                share = _build_share_dict(candidate["url"], code, platform_name)
                identity = _share_identity(share["share_url"])
                if identity in seen:
                    existing_index = seen[identity]
                    if share.get("pass_code") and not shares[existing_index].get("pass_code"):
                        shares[existing_index]["pass_code"] = share["pass_code"]
                        shares[existing_index]["share_url"] = _append_pass_code(
                            shares[existing_index].get("share_url") or "",
                            share["pass_code"],
                        )
                    last_index = existing_index
                else:
                    shares.append(share)
                    seen[identity] = len(shares) - 1
                    last_index = len(shares) - 1
            continue
        code = _extract_pass_code_from_line(line)
        if code and last_index is not None and not shares[last_index].get("pass_code"):
            shares[last_index]["pass_code"] = code
            shares[last_index]["share_url"] = _append_pass_code(
                shares[last_index].get("share_url") or "",
                code,
            )
    return shares


def extract_baidu_urls(text: str) -> List[str]:
    return [share["share_url"] for share in extract_share_inputs(text, platform="baidu")]


def extract_http_urls(text: str) -> List[str]:
    """提取 HTTP 下载面板可用的链接，直接链接原样保留。"""
    normalized = normalize_link_text(text)
    result: List[str] = []
    seen: set[str] = set()
    shares = extract_share_inputs(normalized)
    for share in shares:
        url = str(share.get("share_url") or "")
        if url and url not in seen:
            result.append(url)
            seen.add(url)
    for line in re.split(r"[\r\n]+", normalized):
        for candidate in _url_candidates(line):
            url = candidate["url"]
            platform_name = _platform_for_url(url)
            if platform_name or url not in seen:
                if url not in seen:
                    result.append(url)
                    seen.add(url)
    return result


def _strip_password_value(value: str) -> str:
    text = str(value or "").strip()
    while text and text[-1] in ")]}>》」】'\"`。，；、":
        text = text[:-1]
    return text


def extract_archive_passwords(text: str) -> List[str]:
    """从文本中提取解压密码，兼容“解压/解压码/密码/压缩密码”等常见写法。"""
    normalized = normalize_link_text(text)
    passwords: List[str] = []
    seen: set[str] = set()
    share_codes = {
        str(share.get("pass_code") or "").strip()
        for share in extract_share_inputs(normalized)
        if share.get("pass_code")
    }

    def add(password: str) -> None:
        value = _strip_password_value(password)
        if value and value not in seen:
            passwords.append(value)
            seen.add(value)

    for match in _ARCHIVE_PASSWORD_LABEL_RE.finditer(normalized):
        add(match.group(1))
    for match in _SHORTHAND_ARCHIVE_PASSWORD_RE.finditer(normalized):
        add(match.group(1))
    for match in _GENERIC_PASSWORD_RE.finditer(normalized):
        value = _strip_password_value(match.group(1))
        if value and value not in share_codes:
            add(value)
    return passwords
