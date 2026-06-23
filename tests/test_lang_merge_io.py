"""lang_merge_io.py 模組的單元測試。

用途：測試 DirReader 抽象介面、ZipReader、FolderReader 以及 quarantine_copy 函式。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from translation_tool.core.lang_merge_io import (
    DirReader,
    ZipReader,
    FolderReader,
    quarantine_copy,
)


class TestDirReaderAbstract:
    """測試 DirReader 抽象介面。"""

    def test_cannot_instantiate_directly(self):
        """DirReader 是抽象類別，不能直接實例化。"""
        with pytest.raises(TypeError) as exc_info:
            DirReader()
        assert "abstract" in str(exc_info.value).lower()


class TestZipReader:
    """測試 ZipReader。"""

    def test_read_bytes_returns_correct_content(self, tmp_path: Path):
        """測試 ZipReader.read_bytes 回傳正確內容。"""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("dir/file.txt", b"hello world")

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            content = reader.read_bytes("dir/file.txt")
            assert content == b"hello world"

    def test_read_text_with_utf8_bom(self, tmp_path: Path):
        """測試 ZipReader.read_text 處理 UTF-8 BOM（bytes 有 BOM 前綴時 strip 掉）。"""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file.txt", b"\xef\xbb\xbfhello")

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            text = reader.read_text("file.txt")
            assert text == "hello"

    def test_read_json_parses_valid_json(self, tmp_path: Path):
        """測試 ZipReader.read_json 解析有效 JSON。"""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data.json", '{"key": "value"}')

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            data = reader.read_json("data.json")
            assert data == {"key": "value"}

    def test_read_json_raises_on_invalid_json(self, tmp_path: Path):
        """測試 ZipReader.read_json 在無效 JSON 時拋出 RuntimeError。"""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("bad.json", "not-json{")

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            with pytest.raises(RuntimeError):
                reader.read_json("bad.json")

    def test_list_all_returns_namelist(self, tmp_path: Path):
        """測試 ZipReader.list_all 回傳所有檔案路徑。"""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.txt", b"a")
            zf.writestr("b/c.txt", b"b")

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            names = reader.list_all()
            assert "a.txt" in names
            assert "b/c.txt" in names

    def test_exists_returns_true_for_existing_file(self, tmp_path: Path):
        """測試 ZipReader.exists 回傳 True 當檔案存在。"""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("exists.txt", b"")

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            assert reader.exists("exists.txt") is True
            assert reader.exists("missing.txt") is False


class TestFolderReader:
    """測試 FolderReader。"""

    def test_read_bytes_returns_correct_content(self, tmp_path: Path):
        """測試 FolderReader.read_bytes 回傳正確內容。"""
        file_path = tmp_path / "dir" / "file.txt"
        file_path.parent.mkdir()
        file_path.write_text("hello world", encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        content = reader.read_bytes("dir/file.txt")
        assert content == b"hello world"

    def test_read_text_returns_string(self, tmp_path: Path):
        """測試 FolderReader.read_text 回傳字串。"""
        file_path = tmp_path / "file.txt"
        file_path.write_text("hello", encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        text = reader.read_text("file.txt")
        assert text == "hello"

    def test_read_json_parses_valid_json(self, tmp_path: Path):
        """測試 FolderReader.read_json 解析有效 JSON。"""
        file_path = tmp_path / "data.json"
        file_path.write_text('{"key": "value"}', encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        data = reader.read_json("data.json")
        assert data == {"key": "value"}

    def test_read_json_raises_on_invalid_json(self, tmp_path: Path):
        """測試 FolderReader.read_json 在無效 JSON 時拋出 RuntimeError。"""
        file_path = tmp_path / "bad.json"
        file_path.write_text("not-json{", encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        with pytest.raises(RuntimeError):
            reader.read_json("bad.json")

    def test_list_all_returns_relative_paths(self, tmp_path: Path):
        """測試 FolderReader.list_all 回傳相對路徑。"""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "sub" / "b.txt").parent.mkdir()
        (tmp_path / "sub" / "b.txt").write_text("b", encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        names = reader.list_all()
        assert "a.txt" in names
        assert "sub/b.txt" in names

    def test_exists_returns_true_for_existing_file(self, tmp_path: Path):
        """測試 FolderReader.exists 回傳 True 當檔案存在。"""
        file_path = tmp_path / "exists.txt"
        file_path.write_text("", encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        assert reader.exists("exists.txt") is True
        assert reader.exists("missing.txt") is False

    def test_copy_to_copies_file_to_target(self, tmp_path: Path):
        """測試 FolderReader.copy_to 複製檔案到目標路徑。"""
        source = tmp_path / "source.txt"
        source.write_text("content", encoding="utf-8")
        target = tmp_path / "target.txt"

        reader = FolderReader(str(tmp_path))
        reader.copy_to("source.txt", str(target))

        assert target.read_text(encoding="utf-8") == "content"

    def test_backslash_in_list_all_normalized(self, tmp_path: Path):
        """測試 FolderReader.list_all 將反斜線 normalize 為斜線。"""
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "file.txt").write_text("x", encoding="utf-8")

        reader = FolderReader(str(tmp_path))
        names = reader.list_all()
        assert any("sub/file.txt" in n for n in names)


class TestQuarantineCopy:
    """測試 quarantine_copy 函式。"""

    def test_quarantine_copy_folder_writer_creates_files(self, tmp_path: Path, monkeypatch):
        """測試 quarantine_copy 使用 FolderReader 時會在 quarantine 目錄建立檔案。"""
        file_path = tmp_path / "file.txt"
        file_path.write_text("test content", encoding="utf-8")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        quarantine_dir = out_dir / "skipped_json"
        quarantine_dir.mkdir()

        reader = FolderReader(str(tmp_path))
        quarantine_copy(
            reader=reader,
            rel_path="file.txt",
            output_dir=str(out_dir),
            reason="test_reason",
            errordata_dir=str(quarantine_dir),
        )

        quarantined = quarantine_dir / "file.txt"
        assert quarantined.read_text(encoding="utf-8") == "test content"
        assert (quarantine_dir / "file.txt.reason.txt").read_text(encoding="utf-8") == "test_reason"

    def test_quarantine_copy_zip_writer_creates_files(self, tmp_path: Path):
        """測試 quarantine_copy 使用 ZipReader 時會在 quarantine 目錄建立檔案。"""
        zip_path = tmp_path / "test.zip"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        quarantine_dir = out_dir / "skipped_json"
        quarantine_dir.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file.txt", "test content")

        with zipfile.ZipFile(zip_path, "r") as zf:
            reader = ZipReader(zf)
            quarantine_copy(
                reader=reader,
                rel_path="file.txt",
                output_dir=str(out_dir),
                reason="zip_reason",
                errordata_dir=str(quarantine_dir),
            )

        quarantined = quarantine_dir / "file.txt"
        assert quarantined.read_text(encoding="utf-8") == "test content"
        assert (quarantine_dir / "file.txt.reason.txt").read_text(encoding="utf-8") == "zip_reason"