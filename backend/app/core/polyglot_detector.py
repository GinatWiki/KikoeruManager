"""嵌入压缩包检测（polyglot 文件）

检测"媒体文件 + 压缩包"的 polyglot 文件（如可播放的 MP4 尾部附加 ZIP/RAR/7z，
改名为压缩包后缀即可解压），并定位压缩包在文件中的精确起始偏移。

检测策略：
1. ZIP（精确快速）：从文件尾部解析 EOCD（End of Central Directory），
   通过中央目录的大小与偏移精确计算压缩包起点，只需读取尾部约 66KB
2. RAR / 7z：签名为 6 字节以上且带版本约束，随机数据几乎不可能出现，
   流式顺序扫描整个文件
3. ZIP 兜底：EOCD 解析失败时，流式扫描 PK\\x03\\x04 并验证头部结构
"""

import os
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

ZIP_LOCAL_SIG = b'PK\x03\x04'
ZIP_EOCD_SIG = b'PK\x05\x06'
ZIP64_EOCD_SIG = b'PK\x06\x06'
ZIP64_LOCATOR_SIG = b'PK\x06\x07'
RAR_SIG = b'Rar!\x1a\x07'
SEVENZ_SIG = b'7z\xBC\xAF\x27\x1C'

# 小于 8KB 的文件不可能是有意义的嵌入压缩包
MIN_EMBEDDED_SIZE = 8192


def find_embedded_archive(file_path: str) -> Optional[Tuple[str, int]]:
    """检测文件中嵌入的压缩包并返回其精确偏移

    Args:
        file_path: 文件路径

    Returns:
        (压缩包类型 'zip'/'rar'/'7z', 起始偏移) 或 None。
        压缩包从文件头开始（普通压缩包）时也返回 None。
    """
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return None

    if file_size < MIN_EMBEDDED_SIZE:
        return None

    with open(file_path, 'rb') as f:
        # 1. ZIP 快速路径：通过尾部 EOCD 精确定位
        zip_offset = _find_zip_start_via_eocd(f, file_size)
        if zip_offset is not None:
            if zip_offset == 0:
                return None  # 普通 zip，不属于嵌入
            logger.info(f"[Polyglot] 通过 EOCD 定位到嵌入 ZIP: {file_path} (偏移: {zip_offset})")
            return ('zip', zip_offset)

        # 2. 顺序流式扫描 RAR/7z 签名及 ZIP 兜底
        result = _sequential_scan(f, file_size)
        if result:
            logger.info(f"[Polyglot] 顺序扫描找到嵌入 {result[0].upper()}: {file_path} (偏移: {result[1]})")
        return result


def _find_zip_start_via_eocd(f, file_size: int) -> Optional[int]:
    """通过文件尾部的 EOCD 记录精确计算 ZIP 压缩包的起始偏移

    ZIP 结构末尾固定为 EOCD（22 字节 + 最长 65535 字节注释）。
    EOCD 中记录中央目录的大小和（相对于压缩包起点的）偏移：
        zip_start = eocd_pos - cd_size - cd_offset
    """
    eocd_min = 22
    if file_size < eocd_min + MIN_EMBEDDED_SIZE:
        return None

    tail_size = min(file_size, eocd_min + 65535 + 1024)
    tail_base = file_size - tail_size
    f.seek(tail_base)
    tail = f.read(tail_size)

    # 从后往前查找 EOCD（文件尾附近可能有多个候选，逐个验证）
    idx = len(tail)
    while True:
        idx = tail.rfind(ZIP_EOCD_SIG, 0, idx)
        if idx < 0:
            return None

        eocd = tail[idx:]
        if len(eocd) >= eocd_min:
            comment_len = int.from_bytes(eocd[20:22], 'little')
            # 注释必须恰好延伸到文件末尾，否则是随机字节
            if eocd_min + comment_len == len(eocd):
                eocd_pos = tail_base + idx
                cd_size = int.from_bytes(eocd[12:16], 'little')
                cd_offset = int.from_bytes(eocd[16:20], 'little')

                if cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
                    # ZIP64：通过 ZIP64 EOCD locator 定位
                    start = _zip64_start(f, eocd_pos, file_size)
                else:
                    start = eocd_pos - cd_size - cd_offset

                if start is not None and 0 <= start < file_size:
                    if start == 0:
                        return 0
                    # 验证起点处确实是 ZIP local file header
                    f.seek(start)
                    if f.read(4) == ZIP_LOCAL_SIG:
                        return start
        idx -= 1  # 验证失败，继续向前找下一个候选


def _zip64_start(f, eocd_pos: int, file_size: int) -> Optional[int]:
    """解析 ZIP64 EOCD locator，计算 ZIP64 压缩包的起始偏移"""
    locator_size = 20
    locator_pos = eocd_pos - locator_size
    if locator_pos < 0:
        return None

    f.seek(locator_pos)
    locator = f.read(locator_size)
    if len(locator) != locator_size or locator[:4] != ZIP64_LOCATOR_SIG:
        return None

    # locator 中记录 ZIP64 EOCD 记录相对于压缩包起点的偏移
    zip64_eocd_rel = int.from_bytes(locator[8:16], 'little')
    # ZIP64 EOCD 记录紧跟在 locator 之前（记录本身 56 字节）
    zip64_eocd_pos = locator_pos - 56
    if zip64_eocd_pos < 0:
        return None

    f.seek(zip64_eocd_pos)
    record = f.read(56)
    if len(record) != 56 or record[:4] != ZIP64_EOCD_SIG:
        return None

    start = zip64_eocd_pos - zip64_eocd_rel
    if 0 <= start < file_size:
        return start
    return None


def _sequential_scan(f, file_size: int) -> Optional[Tuple[str, int]]:
    """流式顺序扫描整个文件，查找 RAR/7z/ZIP 签名

    块之间保留重叠区域，避免签名跨越块边界被漏检。
    """
    chunk_size = 16 * 1024 * 1024  # 16MB
    overlap = 8  # 最长签名 8 字节

    pos = 0
    while pos < file_size:
        f.seek(pos)
        data = f.read(min(chunk_size, file_size - pos))
        if not data:
            break

        # RAR（RAR4: ...\x00 / RAR5: ...\x01，共 7/8 字节，无随机碰撞风险）
        idx = data.find(RAR_SIG)
        while idx >= 0:
            if idx + 8 <= len(data) and data[idx + 7:idx + 8] in (b'\x00', b'\x01'):
                return ('rar', pos + idx)
            idx = data.find(RAR_SIG, idx + 1)

        # 7z（6 字节签名）
        idx = data.find(SEVENZ_SIG)
        if idx >= 0:
            return ('7z', pos + idx)

        # ZIP local header 兜底（需验证头部结构，降低误判）
        idx = data.find(ZIP_LOCAL_SIG)
        while idx >= 0:
            if _validate_zip_header(data, idx):
                return ('zip', pos + idx)
            idx = data.find(ZIP_LOCAL_SIG, idx + 1)

        if len(data) < chunk_size:
            break  # 已到文件末尾
        pos += chunk_size - overlap

    return None


def _validate_zip_header(data: bytes, sig_offset: int) -> bool:
    """验证 ZIP local header 签名后是否有合理的头部结构"""
    try:
        if sig_offset + 30 > len(data):
            return False
        after_sig = data[sig_offset + 4:]

        # 解压方法：0=stored, 8=deflated, 9=deflate64, 12=bzip2, 14=lzma, 93=zstd, 95=xz
        method = int.from_bytes(after_sig[4:6], 'little')
        if method in (0, 8, 9, 12, 14, 93, 95):
            return True

        # 版本号通常在 10-63 之间
        version = int.from_bytes(after_sig[0:2], 'little')
        return 10 <= version <= 63
    except Exception:
        return False
