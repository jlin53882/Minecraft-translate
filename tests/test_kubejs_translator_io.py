"""kubejs_translator_io.py 模組的單元測試。

用途：測試 kubejs_translator_io 中的 JSON 讀寫功能。
"""
from __future__ import annotations

import sys
from pathlib import Path

import orjson
import pytest

# 確保可以導入 translation_tool
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation_tool.core.kubejs_translator_io import (
    read_json_dict_orjson_impl,
    write_json_orjson_impl,
)


class TestReadJsonDictOrjsonImpl:
    """測試 read_json_dict_orjson_impl 函式。"""

    @pytest.fixture
    def tmp_json_file(self, tmp_path: Path):
        """建立臨時 JSON 檔案。"""
        return tmp_path / "test.json"

    def test_read_valid_json(self, tmp_json_file: Path):
        """測試讀取有效 JSON。"""
        data = {"key": "value", "number": 123}
        tmp_json_file.write_bytes(orjson.dumps(data))

        result = read_json_dict_orjson_impl(tmp_json_file)
        assert result == data

    def test_read_nonexistent_file(self, tmp_path: Path):
        """測試讀取不存在的檔案應回傳空字典。"""
        result = read_json_dict_orjson_impl(tmp_path / "not_exist.json")
        assert result == {}

    def test_read_none_path(self):
        """測試傳入 None 路徑應回傳空字典。"""
        result = read_json_dict_orjson_impl(None)
        assert result == {}

    def test_read_invalid_json(self, tmp_json_file: Path):
        """測試讀取無效 JSON 應回傳空字典。"""
        tmp_json_file.write_text("not valid json {{{")

        result = read_json_dict_orjson_impl(tmp_json_file)
        assert result == {}

    def test_read_with_bom(self, tmp_json_file: Path):
        """測試讀取帶 BOM 的 JSON。"""
        data = {"key": "value"}
        content = "\ufeff" + orjson.dumps(data).decode("utf-8")
        tmp_json_file.write_bytes(content.encode("utf-8"))

        result = read_json_dict_orjson_impl(tmp_json_file)
        assert result == data

    def test_read_with_trailing_comma(self, tmp_json_file: Path):
        """測試讀取帶尾部逗號的 JSON。"""
        content = '{"key1": "value1", "key2": "value2", }'
        tmp_json_file.write_text(content)

        result = read_json_dict_orjson_impl(tmp_json_file)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_read_array_returns_empty_dict(self, tmp_json_file: Path):
        """測試讀取 JSON 陣列應回傳空字典。"""
        tmp_json_file.write_bytes(orjson.dumps([1, 2, 3]))

        result = read_json_dict_orjson_impl(tmp_json_file)
        assert result == {}


class TestWriteJsonOrjsonImpl:
    """測試 write_json_orjson_impl 函式。"""

    @pytest.fixture
    def tmp_output_file(self, tmp_path: Path):
        """建立臨時輸出檔案路徑。"""
        return tmp_path / "output" / "test.json"

    def test_write_valid_dict(self, tmp_output_file: Path):
        """測試寫入有效字典。"""
        data = {"key": "value", "number": 123}

        write_json_orjson_impl(tmp_output_file, data)

        assert tmp_output_file.exists()
        result = orjson.loads(tmp_output_file.read_bytes())
        assert result == data

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        """測試寫入時自動建立父目錄。"""
        output_file = tmp_path / "nested" / "dir" / "test.json"
        data = {"key": "value"}

        write_json_orjson_impl(output_file, data)

        assert output_file.exists()
        assert output_file.parent.exists()

    def test_write_with_numeric_keys(self, tmp_output_file: Path):
        """測試寫入數值鍵名會被轉換為字串。"""
        data = {123: "value", 456: "value2"}

        write_json_orjson_impl(tmp_output_file, data)

        result = orjson.loads(tmp_output_file.read_bytes())
        assert result == {"123": "value", "456": "value2"}

    def test_write_nested_dict(self, tmp_output_file: Path):
        """測試寫入巢狀字典。"""
        data = {"outer": {"inner": "value", "list": [1, 2, 3]}}

        write_json_orjson_impl(tmp_output_file, data)

        result = orjson.loads(tmp_output_file.read_bytes())
        assert result == data

    def test_write_empty_dict(self, tmp_output_file: Path):
        """測試寫入空字典。"""
        write_json_orjson_impl(tmp_output_file, {})

        assert tmp_output_file.exists()
        result = orjson.loads(tmp_output_file.read_bytes())
        assert result == {}

    def test_write_list_normalizes_keys(self, tmp_output_file: Path):
        """測試列表中的字典鍵名也被正規化。"""
        data = [{"key": "value"}, {"key2": "value2"}]

        write_json_orjson_impl(tmp_output_file, data)

        result = orjson.loads(tmp_output_file.read_bytes())
        assert result == data

    def test_overwrite_existing_file(self, tmp_output_file: Path):
        """測試覆寫已存在的檔案。"""
        tmp_output_file.parent.mkdir(parents=True)
        tmp_output_file.write_bytes(orjson.dumps({"old": "data"}))

        new_data = {"new": "data"}
        write_json_orjson_impl(tmp_output_file, new_data)

        result = orjson.loads(tmp_output_file.read_bytes())
        assert result == new_data
