import os

from app.core.archive_volume_utils import (
    detect_archive_volume_group,
    get_archive_total_size,
    get_archive_volume_paths,
    is_small_archive,
)


def _write_bytes(path, size: int) -> None:
    path.write_bytes(b"x" * size)


def test_exe_e_volume_total_size_from_main(tmp_path):
    exe = tmp_path / "RJ01629292.exe"
    e01 = tmp_path / "RJ01629292.e01"
    e02 = tmp_path / "RJ01629292.e02"
    _write_bytes(exe, 700)
    _write_bytes(e01, 701)
    _write_bytes(e02, 123)

    group = detect_archive_volume_group(str(exe))

    assert group is not None
    assert group.main_filename == "RJ01629292.exe"
    assert [os.path.basename(path) for path in group.volumes] == [
        "RJ01629292.exe",
        "RJ01629292.e01",
        "RJ01629292.e02",
    ]
    assert get_archive_total_size(str(exe)) == 1524


def test_exe_e_volume_total_size_from_member(tmp_path):
    exe = tmp_path / "RJ01629292.exe"
    e01 = tmp_path / "RJ01629292.e01"
    e02 = tmp_path / "RJ01629292.e02"
    _write_bytes(exe, 700)
    _write_bytes(e01, 701)
    _write_bytes(e02, 123)

    paths = get_archive_volume_paths(str(e02))

    assert [os.path.basename(path) for path in paths] == [
        "RJ01629292.exe",
        "RJ01629292.e01",
        "RJ01629292.e02",
    ]
    assert get_archive_total_size(str(e02)) == 1524


def test_zip_numeric_volume_keeps_zip_as_main(tmp_path):
    zip_path = tmp_path / "RJ00000001.zip"
    p002 = tmp_path / "RJ00000001.002"
    p003 = tmp_path / "RJ00000001.003"
    _write_bytes(zip_path, 10)
    _write_bytes(p002, 20)
    _write_bytes(p003, 30)

    group = detect_archive_volume_group(str(p003))

    assert group is not None
    assert group.main_filename == "RJ00000001.zip"
    assert get_archive_total_size(str(zip_path)) == 60


def test_single_exe_sfx_does_not_become_volume_group(tmp_path):
    exe = tmp_path / "RJ00000002.exe"
    _write_bytes(exe, 128)

    assert detect_archive_volume_group(str(exe)) is None
    assert get_archive_volume_paths(str(exe)) == [str(exe)]
    assert get_archive_total_size(str(exe)) == 128


def test_small_archive_judgement_uses_whole_volume_group_size(tmp_path):
    """回归：分卷头包（.7z.001 只有 1KB）不能按单文件大小误判成小型字幕包。

    v2.5.26 用户实测 RJ01609179.7z.001 = 1KB + .7z.002 = 3.31GB，
    task_engine 按首卷 os.path.getsize 判定 < 10MB → 误入"小型压缩包内未发现
    字幕文件"人工核查链路。
    """
    head = tmp_path / "RJ01609179.7z.001"
    tail = tmp_path / "RJ01609179.7z.002"
    _write_bytes(head, 1024)
    _write_bytes(tail, 32 * 1024 * 1024)  # 32MB

    threshold = 10 * 1024 * 1024

    # 从任意一个分卷成员进入都必须看到整组大小
    assert not is_small_archive(str(head), threshold)
    assert not is_small_archive(str(tail), threshold)
    assert get_archive_total_size(str(head)) == 1024 + 32 * 1024 * 1024

    # 真正的小包（单文件、无分卷）仍判定为小包
    tiny = tmp_path / "RJ00000003.zip"
    _write_bytes(tiny, 5 * 1024 * 1024)
    assert is_small_archive(str(tiny), threshold)

    # 空文件不算小包（保持 0 < size 语义）
    empty = tmp_path / "RJ00000004.zip"
    empty.write_bytes(b"")
    assert not is_small_archive(str(empty), threshold)
