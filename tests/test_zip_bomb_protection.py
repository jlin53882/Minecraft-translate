"""tests/test_zip_bomb_protection.py
用途：驗證 lang_merge_zip_io.py 的 ZIP bomb 保護邏輯。
單一 ZIP 條目解壓後若超過 50MB，_read_text_from_zip 應拋出 RuntimeError。
"""
import io
import zipfile
import pytest
from translation_tool.core.lang_merge_zip_io import (
    _read_text_from_zip,
    _MAX_UNCOMPRESSED_SIZE,
)


def test_max_uncompressed_size_is_50mb():
    """確認常數值為 50MB。"""
    assert _MAX_UNCOMPRESSED_SIZE == 50 * 1024 * 1024


def test_oversized_zip_entry_raises():
    """單一檔案解壓縮後超過 50MB 時應拋出 RuntimeError。"""
    # 建立一個超大檔案的 ZIP（60MB > 50MB 上限）
    zf_out = io.BytesIO()
    with zipfile.ZipFile(zf_out, "w", zipfile.ZIP_DEFLATED) as zf:
        large_content = b"X" * (60 * 1024 * 1024)  # 60MB
        zf.writestr("huge.json", large_content)

    zf_out.seek(0)
    with zipfile.ZipFile(zf_out, "r") as zf:
        with pytest.raises(RuntimeError, match="50MB|ZIP bomb"):
            _read_text_from_zip(zf, "huge.json")


def test_exactly_at_limit_does_not_raise():
    """剛好 50MB 的檔案（邊界值）不應拋出例外。"""
    zf_out = io.BytesIO()
    with zipfile.ZipFile(zf_out, "w", zipfile.ZIP_DEFLATED) as zf:
        # 正好 50MB
        content = b"Y" * _MAX_UNCOMPRESSED_SIZE
        zf.writestr("exact_50mb.json", content)

    zf_out.seek(0)
    with zipfile.ZipFile(zf_out, "r") as zf:
        # 不應拋出例外
        result = _read_text_from_zip(zf, "exact_50mb.json")
        assert len(result) == _MAX_UNCOMPRESSED_SIZE


def test_normal_size_file_reads_successfully():
    """正常大小的檔案應可正常讀取。"""
    zf_out = io.BytesIO()
    with zipfile.ZipFile(zf_out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("normal.json", b'{"key": "value"}')

    zf_out.seek(0)
    with zipfile.ZipFile(zf_out, "r") as zf:
        result = _read_text_from_zip(zf, "normal.json")
        assert result == '{"key": "value"}'
