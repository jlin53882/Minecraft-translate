"""lang_merge_zip_io.py 單元測試。

用途：測試 ZIP 檔案 IO 操作的各種情況。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from translation_tool.core.lang_merge_zip_io import (
    _read_json_from_zip,
    _read_text_from_zip,
    _write_bytes_atomic,
    _write_text_atomic,
    quarantine_copy_from_zip,
)


def _mock_config() -> dict:
    """提供測試用的 mock config。"""
    return {
        "lang_merger": {
            "quarantine_folder_name": "skipped_json",
        }
    }


class TestReadTextFromZip:
    """測試 _read_text_from_zip 函式。"""

    def test_read_utf8_text(self, tmp_path: Path) -> None:
        """測試讀取 UTF-8 編碼的文字檔案。"""
        zip_path = tmp_path / "test.zip"
        test_content = "你好世界 Hello World"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", test_content.encode("utf-8"))

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_text_from_zip(zf, "test.txt")

        assert result == test_content

    def test_read_utf8_with_bom(self, tmp_path: Path) -> None:
        """測試讀取帶 BOM 的 UTF-8 檔案。"""
        zip_path = tmp_path / "test.zip"
        test_content = "測試內容"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "\ufeff" + test_content)

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_text_from_zip(zf, "test.txt")

        assert result == test_content

    def test_read_gbk_encoded_text(self, tmp_path: Path) -> None:
        """測試讀取 GBK 編碼的文字檔案（fallback）。"""
        zip_path = tmp_path / "test.zip"
        test_content = "簡體中文"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", test_content.encode("gbk"))

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_text_from_zip(zf, "test.txt")

        assert result == test_content


class TestReadJsonFromZip:
    """測試 _read_json_from_zip 函式。"""

    def test_read_valid_json(self, tmp_path: Path) -> None:
        """測試讀取有效的 JSON 檔案。"""
        zip_path = tmp_path / "test.zip"
        data = {"key1": "value1", "key2": 123}

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.json", json.dumps(data))

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_json_from_zip(zf, "test.json")

        assert result == data

    def test_read_json_with_bom(self, tmp_path: Path) -> None:
        """測試讀取帶 BOM 的 JSON 檔案。"""
        zip_path = tmp_path / "test.zip"
        data = {"key": "value"}

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.json", "\ufeff" + json.dumps(data))

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_json_from_zip(zf, "test.json")

        assert result == data

    def test_read_empty_json(self, tmp_path: Path) -> None:
        """測試讀取空內容的 JSON 檔案。"""
        zip_path = tmp_path / "test.zip"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.json", "")

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_json_from_zip(zf, "test.json")

        assert result == {}

    def test_read_invalid_json_returns_empty_dict(self, tmp_path: Path) -> None:
        """測試讀取無效 JSON 時返回空字典。"""
        zip_path = tmp_path / "test.zip"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.json", "not valid json {")

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_json_from_zip(zf, "test.json")

        assert result == {}


class TestWriteBytesAtomic:
    """測試 _write_bytes_atomic 函式。"""

    def test_write_bytes_creates_file(self, tmp_path: Path) -> None:
        """測試原子性寫入位元組資料。"""
        file_path = tmp_path / "output.bin"
        data = b"\x00\x01\x02\x03"

        _write_bytes_atomic(str(file_path), data)

        assert file_path.exists()
        assert file_path.read_bytes() == data

    def test_write_bytes_creates_parent_dirs(self, tmp_path: Path) -> None:
        """測試自動建立父目錄。"""
        file_path = tmp_path / "subdir" / "nested" / "output.bin"
        data = b"test data"

        _write_bytes_atomic(str(file_path), data)

        assert file_path.exists()
        assert file_path.read_bytes() == data

    def test_write_bytes_overwrites_existing(self, tmp_path: Path) -> None:
        """測試覆蓋已存在的檔案。"""
        file_path = tmp_path / "output.bin"
        file_path.write_bytes(b"old data")

        _write_bytes_atomic(str(file_path), b"new data")

        assert file_path.read_bytes() == b"new data"


class TestWriteTextAtomic:
    """測試 _write_text_atomic 函式。"""

    def test_write_text_creates_file(self, tmp_path: Path) -> None:
        """測試原子性寫入文字資料。"""
        file_path = tmp_path / "output.txt"
        text = "測試中文\n第二行"

        _write_text_atomic(str(file_path), text)

        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == text

    def test_write_text_utf8_encoding(self, tmp_path: Path) -> None:
        """測試 UTF-8 編碼正確處理。"""
        file_path = tmp_path / "output.txt"
        text = "你好世界 αβγδ"

        _write_text_atomic(str(file_path), text)

        assert file_path.read_text(encoding="utf-8") == text


class TestQuarantineCopyFromZip:
    """測試 quarantine_copy_from_zip 函式。"""

    def test_copy_file_to_quarantine(self, tmp_path: Path, monkeypatch) -> None:
        """測試將檔案複製到隔離區。"""
        zip_path = tmp_path / "test.zip"
        output_dir = tmp_path / "output"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("assets/mod/lang/en_us.json", '{"key": "value"}')

        monkeypatch.setattr("translation_tool.core.lang_merge_zip_io.load_config", _mock_config)

        with zipfile.ZipFile(zip_path, "r") as zf:
            quarantine_copy_from_zip(
                zf=zf,
                zip_path="assets/mod/lang/en_us.json",
                output_dir=str(output_dir),
                reason="test_reason",
            )

        quarantine_path = output_dir / "skipped_json" / "assets" / "mod" / "lang" / "en_us.json"
        reason_path = output_dir / "skipped_json" / "assets" / "mod" / "lang" / "en_us.json.reason.txt"

        assert quarantine_path.exists()
        assert quarantine_path.read_bytes() == b'{"key": "value"}'
        assert reason_path.exists()
        assert reason_path.read_text(encoding="utf-8") == "test_reason"

    def test_copy_file_with_extra_text(self, tmp_path: Path, monkeypatch) -> None:
        """測試攜帶額外资訊的隔離複製。"""
        zip_path = tmp_path / "test.zip"
        output_dir = tmp_path / "output"

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.json", "{}")

        monkeypatch.setattr("translation_tool.core.lang_merge_zip_io.load_config", _mock_config)

        with zipfile.ZipFile(zip_path, "r") as zf:
            quarantine_copy_from_zip(
                zf=zf,
                zip_path="test.json",
                output_dir=str(output_dir),
                reason="parse_error",
                extra_text="Detailed error info",
            )

        detail_path = output_dir / "skipped_json" / "test.json.detail.txt"
        assert detail_path.exists()
        assert detail_path.read_text(encoding="utf-8") == "Detailed error info"
