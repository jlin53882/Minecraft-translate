"""tests/test_zip_safety.py

驗證 ZIP/JAR 不信任輸入防護：
- zip_safety.read_limited / safe_join
- 各 ZIP 讀取點套用大小上限
- JAR 提取、隔離複製不會寫到輸出根目錄之外（路徑遍歷 / zip-slip）
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path

import pytest

from translation_tool.core.jar_processor_extract import extract_from_jar_impl
from translation_tool.core.lang_merge_io import ZipReader, quarantine_copy
from translation_tool.core.lang_merge_zip_io import quarantine_copy_from_zip
from translation_tool.utils.jar_browser import _scan_single_jar
from translation_tool.utils.zip_safety import (
    UnsafePathError,
    ZipSizeError,
    read_limited,
    safe_join,
)


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    """建立 ZIP；成員名稱原樣寫入（可含 .. 與絕對路徑）。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


# ---------------------------------------------------------------------------
# read_limited
# ---------------------------------------------------------------------------


def test_read_limited_returns_content_within_limit():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    with zipfile.ZipFile(buf) as zf:
        assert read_limited(zf, "a.txt", max_bytes=5) == b"hello"


def test_read_limited_rejects_oversized_header():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"X" * 2048)
    with zipfile.ZipFile(buf) as zf, pytest.raises(ZipSizeError, match="ZIP bomb"):
        read_limited(zf, "big.bin", max_bytes=1024)


def test_read_limited_counts_actual_bytes_when_header_lies():
    """header 宣告很小但實際串流超過上限時仍要拒絕。"""

    class _FakeInfo:
        filename = "lie.bin"
        file_size = 10  # 偽造的小尺寸

    class _FakeStream(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeZip:
        def open(self, info):
            return _FakeStream(b"Z" * 5000)

    fake_info = zipfile.ZipInfo("lie.bin")
    fake_info.file_size = 10
    with pytest.raises(ZipSizeError):
        read_limited(_FakeZip(), fake_info, max_bytes=1000)  # type: ignore[arg-type]


def test_zip_size_error_is_runtime_error():
    assert issubclass(ZipSizeError, RuntimeError)


# ---------------------------------------------------------------------------
# safe_join
# ---------------------------------------------------------------------------


def test_safe_join_allows_normal_nested_path(tmp_path):
    result = safe_join(tmp_path, "assets", "mod", "lang", "en_us.json")
    assert result == os.path.abspath(
        os.path.join(tmp_path, "assets", "mod", "lang", "en_us.json")
    )


@pytest.mark.parametrize(
    "evil",
    [
        "../escape.txt",
        "assets/../../escape.txt",
        "a/b/../../../escape.txt",
        "/etc/passwd",
    ],
)
def test_safe_join_rejects_escape(tmp_path, evil):
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, evil)


def test_safe_join_rejects_sibling_prefix_dir(tmp_path):
    """/out 與 /out_evil 共用前綴，不可被誤判為在內。"""
    root = tmp_path / "out"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        safe_join(root, "..", "out_evil", "x.txt")


# ---------------------------------------------------------------------------
# jar_browser
# ---------------------------------------------------------------------------


def test_scan_single_jar_skips_oversized_member(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "translation_tool.utils.jar_browser.read_limited",
        lambda zf, name, *a, **k: read_limited(zf, name, max_bytes=100),
    )
    jar = _make_zip(
        tmp_path / "m.jar",
        {
            "assets/m/lang/en_us.json": b"{}",
            "assets/m/lang/big.json": b"X" * 500,
        },
    )
    _, content = _scan_single_jar(jar, [r"assets/m/lang/.*\.json$"])
    assert content == {"assets/m/lang/en_us.json": "{}"}


# ---------------------------------------------------------------------------
# JAR 提取：路徑遍歷
# ---------------------------------------------------------------------------


def test_extract_from_jar_blocks_path_traversal(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    jar = _make_zip(
        tmp_path / "evil.jar",
        {
            "assets/mod/lang/en_us.json": b'{"k": "v"}',
            "assets/../../escaped.json": b'{"pwn": 1}',
        },
    )
    regex = re.compile(r"\.json$")

    result = extract_from_jar_impl(str(jar), str(out), regex)

    assert (out / "assets" / "mod" / "lang" / "en_us.json").exists()
    assert not (tmp_path / "escaped.json").exists()
    assert not list(tmp_path.rglob("escaped.json"))
    assert result["extracted"] == 1
    assert result["skipped"] == 1


def test_extract_from_jar_blocks_absolute_member_path(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    jar = _make_zip(
        tmp_path / "evil2.jar",
        {f"{victim.as_posix()}/abs.json": b"{}"},
    )
    regex = re.compile(r"\.json$")

    extract_from_jar_impl(str(jar), str(out), regex)

    assert not (victim / "abs.json").exists()


def test_extract_from_jar_skips_oversized_member(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    jar = _make_zip(
        tmp_path / "big.jar",
        {"assets/mod/textures/huge.png": b"\xff" * 4096},
    )
    monkeypatch.setattr(
        "translation_tool.core.jar_processor_extract.MAX_FILE_BYTES", 1024
    )

    result = extract_from_jar_impl(str(jar), str(out), re.compile(r"\.png$"))

    assert result["extracted"] == 0
    assert not (out / "assets" / "mod" / "textures" / "huge.png").exists()


# ---------------------------------------------------------------------------
# 隔離複製：路徑遍歷
# ---------------------------------------------------------------------------


def test_quarantine_copy_from_zip_blocks_traversal(tmp_path):
    zip_path = _make_zip(tmp_path / "src.zip", {"../../evil.json": b"{}"})
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        quarantine_copy_from_zip(zf, "../../evil.json", str(out), "bad")

    assert not list(tmp_path.rglob("evil.json"))


def test_quarantine_copy_blocks_traversal(tmp_path):
    zip_path = _make_zip(tmp_path / "src.zip", {"../../evil.json": b"{}"})
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        quarantine_copy(ZipReader(zf), "../../evil.json", str(out), "bad")

    assert not list(tmp_path.rglob("evil.json"))


def test_quarantine_copy_from_zip_normal_path_still_works(tmp_path):
    zip_path = _make_zip(tmp_path / "src.zip", {"assets/m/lang/x.json": b"{}"})
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        quarantine_copy_from_zip(zf, "assets/m/lang/x.json", str(out), "bad")

    copied = list(out.rglob("x.json"))
    assert len(copied) == 1
    assert Path(str(copied[0]) + ".reason.txt").read_text(encoding="utf-8") == "bad"


def test_safe_join_ignores_realpath_mismatch_without_any_link(tmp_path, monkeypatch):
    """沒有任何 symlink/junction、只是 realpath 解析結果不同（如 OneDrive）時不應誤判逃逸。"""
    import os

    from translation_tool.utils import zip_safety

    real = os.path.realpath

    def odd_realpath(path, *a, **k):
        if str(path).endswith("a.json"):
            return os.path.join(os.path.sep, "somewhere", "else", "a.json")
        return real(path, *a, **k)

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(zip_safety.os.path, "realpath", odd_realpath)
    assert safe_join(root, "待翻譯", "a.json").endswith("a.json")
