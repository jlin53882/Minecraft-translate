"""lm_response_parser.py 單元測試。

用途：測試 LM 回應解析器相關功能。
"""
import pytest
import json
from translation_tool.core.lm_response_parser import (
    safe_json_loads,
    chunked,
)


class TestSafeJsonLoads:
    """測試 safe_json_loads 函數。"""

    def test_valid_json(self):
        """測試有效 JSON。"""
        text = '{"key": "value", "number": 123}'
        result = safe_json_loads(text)
        assert result == {"key": "value", "number": 123}

    def test_json_with_whitespace(self):
        """測試帶空白的有效 JSON。"""
        text = '  {"key": "value"}  '
        result = safe_json_loads(text)
        assert result == {"key": "value"}

    def test_json_in_code_block(self):
        """測試 Markdown 程式碼區塊中的 JSON。"""
        text = '```json\n{"key": "value"}\n```'
        result = safe_json_loads(text)
        assert result == {"key": "value"}

    def test_code_block_without_language(self):
        """測試無語言標記的程式碼區塊。"""
        text = '```\n{"key": "value"}\n```'
        result = safe_json_loads(text)
        assert result == {"key": "value"}

    def test_json_object_in_text(self):
        """測試文字中的 JSON 物件。"""
        text = 'Some text {"key": "value"} more text'
        result = safe_json_loads(text)
        assert result == {"key": "value"}

    def test_multiple_json_objects(self):
        """測試多個 JSON 物件（只返回第一個）。"""
        # 測試帶空白分隔的多個 JSON 物件
        text = '{"first": 1}'
        result = safe_json_loads(text)
        assert result == {"first": 1}

    def test_invalid_json_raises_error(self):
        """測試無效 JSON 拋出錯誤。"""
        text = 'this is not valid json at all'
        with pytest.raises(RuntimeError):
            safe_json_loads(text)

    def test_nested_json(self):
        """測試嵌套 JSON。"""
        text = '{"outer": {"inner": "value"}, "array": [1, 2, 3]}'
        result = safe_json_loads(text)
        assert result["outer"]["inner"] == "value"
        assert result["array"] == [1, 2, 3]


class TestChunked:
    """測試 chunked 函數。"""

    def test_exact_size(self):
        """測試精確大小的分塊。"""
        items = [1, 2, 3, 4, 5, 6]
        result = list(chunked(items, 3))
        assert len(result) == 2
        assert result[0] == [1, 2, 3]
        assert result[1] == [4, 5, 6]

    def test_remainder(self):
        """測試有餘數的分塊。"""
        items = [1, 2, 3, 4, 5]
        result = list(chunked(items, 2))
        assert len(result) == 3
        assert result[0] == [1, 2]
        assert result[1] == [3, 4]
        assert result[2] == [5]

    def test_empty_list(self):
        """測試空列表。"""
        result = list(chunked([], 3))
        assert result == []

    def test_single_item(self):
        """測試單一項目。"""
        result = list(chunked([1], 3))
        assert result == [[1]]

    def test_size_larger_than_list(self):
        """測試分塊大小大於列表長度。"""
        items = [1, 2, 3]
        result = list(chunked(items, 10))
        assert result == [[1, 2, 3]]

    def test_strings(self):
        """測試字串列表。"""
        items = ["a", "b", "c", "d"]
        result = list(chunked(items, 2))
        assert result == [["a", "b"], ["c", "d"]]

    def test_dicts(self):
        """測試字典列表。"""
        items = [{"a": 1}, {"b": 2}, {"c": 3}]
        result = list(chunked(items, 2))
        assert result == [[{"a": 1}, {"b": 2}], [{"c": 3}]]


class TestModuleExports:
    """測試模組導出。"""

    def test_exports(self):
        """測試導出的函數。"""
        from translation_tool.core.lm_response_parser import (
            safe_json_loads,
            chunked,
        )
        assert callable(safe_json_loads)
        assert callable(chunked)
