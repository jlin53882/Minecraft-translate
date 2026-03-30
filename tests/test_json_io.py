"""plugins/shared/json_io.py 單元測試

測試目標：JSON IO 工具函數。
"""

import json
from pathlib import Path

import pytest

from translation_tool.plugins.shared import json_io


class TestReadJsonDict:
    """read_json_dict 測試"""

    def test_read_valid_json(self, tmp_path: Path):
        """測試讀取有效 JSON"""
        test_file = tmp_path / "test.json"
        test_file.write_text('{"key": "value"}', encoding="utf-8")

        result = json_io.read_json_dict(test_file)

        assert result == {"key": "value"}

    def test_read_nested_json(self, tmp_path: Path):
        """測試讀取嵌套 JSON"""
        test_file = tmp_path / "nested.json"
        test_file.write_text('{"a": {"b": {"c": 1}}}', encoding="utf-8")

        result = json_io.read_json_dict(test_file)

        assert result == {"a": {"b": {"c": 1}}}

    def test_read_invalid_json(self, tmp_path: Path):
        """測試讀取無效 JSON"""
        test_file = tmp_path / "invalid.json"
        test_file.write_text('not json', encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            json_io.read_json_dict(test_file)

    def test_read_non_dict_json(self, tmp_path: Path):
        """測試讀取非字典 JSON"""
        test_file = tmp_path / "array.json"
        test_file.write_text('[1, 2, 3]', encoding="utf-8")

        with pytest.raises(ValueError, match="JSON must be an object/dict"):
            json_io.read_json_dict(test_file)


class TestWriteJsonDict:
    """write_json_dict 測試"""

    def test_write_simple_dict(self, tmp_path: Path):
        """測試寫入簡單字典"""
        output_file = tmp_path / "output.json"

        json_io.write_json_dict(output_file, {"key": "value"})

        assert output_file.exists()
        assert json.loads(output_file.read_text(encoding="utf-8")) == {"key": "value"}

    def test_write_nested_dict(self, tmp_path: Path):
        """測試寫入嵌套字典"""
        output_file = tmp_path / "nested.json"

        json_io.write_json_dict(output_file, {"a": {"b": 1}})

        assert output_file.exists()
        result = json.loads(output_file.read_text(encoding="utf-8"))
        assert result == {"a": {"b": 1}}

    def test_create_parent_dirs(self, tmp_path: Path):
        """測試自動建立父目錄"""
        output_file = tmp_path / "subdir" / "nested" / "output.json"

        json_io.write_json_dict(output_file, {"test": True})

        assert output_file.exists()
        assert output_file.parent.exists()

    def test_unicode_content(self, tmp_path: Path):
        """測試 Unicode 內容"""
        output_file = tmp_path / "unicode.json"

        json_io.write_json_dict(output_file, {"中文": "測試", "emoji": "🎉"})

        result = json.loads(output_file.read_text(encoding="utf-8"))
        assert result == {"中文": "測試", "emoji": "🎉"}


class TestCollectJsonFiles:
    """collect_json_files 測試"""

    def test_collect_single_file(self, tmp_path: Path):
        """測試收集單一檔案"""
        (tmp_path / "file1.json").write_text("{}")

        result = json_io.collect_json_files(tmp_path)

        assert len(result) == 1

    def test_collect_multiple_files(self, tmp_path: Path):
        """測試收集多個檔案"""
        (tmp_path / "a.json").write_text("{}")
        (tmp_path / "b.json").write_text("{}")
        (tmp_path / "c.json").write_text("{}")

        result = json_io.collect_json_files(tmp_path)

        assert len(result) == 3

    def test_collect_nested_files(self, tmp_path: Path):
        """測試收集嵌套檔案"""
        (tmp_path / "root.json").write_text("{}")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.json").write_text("{}")

        result = json_io.collect_json_files(tmp_path)

        assert len(result) == 2

    def test_collect_sorted(self, tmp_path: Path):
        """測試收集結果已排序"""
        (tmp_path / "c.json").write_text("{}")
        (tmp_path / "a.json").write_text("{}")
        (tmp_path / "b.json").write_text("{}")

        result = json_io.collect_json_files(tmp_path)

        names = [p.name for p in result]
        assert names == ["a.json", "b.json", "c.json"]

    def test_collect_no_json_files(self, tmp_path: Path):
        """測試無 JSON 檔案"""
        (tmp_path / "text.txt").write_text("text")

        result = json_io.collect_json_files(tmp_path)

        assert len(result) == 0
