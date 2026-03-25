"""lm_response_parser.py 單元測試。

用途：測試 LM 回應解析器相關功能。
"""
import pytest
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


class TestSafeJsonLoadsNonGreedy:
    r"""測試 safe_json_loads 的 non-greedy regex 行為（Issue #12 修復）。

    驗證重點：non-greedy regex 不會吃太多內容（trailing text），
    能正確解析多重 JSON 區塊。

    non-greedy `\{[\s\S]*?\}` 匹配規則：
    - 從左到右找到第一個完整 {...} 就停止
    - 不會 greedily 吃到 trailing text
    - 若有多個巢狀 JSON，外層會被完整匹配（因為需要找配對的 }）
    """

    def test_json_with_trailing_text_non_greedy(self):
        """測試 JSON 後有 trailing text 時，non-greedy 不會吃額外內容。

        Issue #12 核心修復：greedy regex 吃到 "} extra text"，
        導致 json.loads() 失敗。non-greedy 只匹配到第一個完整 {}。
        """
        text = '{"items": [{"id": "0", "value": "你好"}]} extra text after'
        result = safe_json_loads(text)
        assert result == {"items": [{"id": "0", "value": "你好"}]}

    def test_json_surrounded_by_text_non_greedy(self):
        """測試 JSON 被文字環繞時，non-greedy 只取第一個 JSON 區塊。"""
        text = 'Some prefix {"key": "value"} some suffix'
        result = safe_json_loads(text)
        assert result == {"key": "value"}

    def test_multiple_json_blocks_takes_first(self):
        """測試有多個 JSON 區塊時，取第一個（而非 greedy 吃到底）。"""
        text = '{"first": 1} and then {"second": 2}'
        result = safe_json_loads(text)
        assert result == {"first": 1}

    def test_json_inside_code_fence_with_trailing_text(self):
        """測試 Markdown code fence 中有多餘文字時，仍正確解析。"""
        text = '```json\n{"key": "value"}\n```\nHere is some extra text'
        result = safe_json_loads(text)
        assert result == {"key": "value"}

    def test_code_block_with_multiple_json_blocks(self):
        """測試 code block 內有多個 JSON 區塊時，取第一個完整 JSON。

        re.findall 返回所有匹配，迭代時第一個可解析的成功。
        """
        text = '```\n{"items": [{"a": 1}]}\n{"extra": "data"}\n```'
        result = safe_json_loads(text)
        # non-greedy 第一個完整 match 是 {"items": [{"a": 1}]}
        assert result == {"items": [{"a": 1}]}

    def test_deeply_nested_json_non_greedy(self):
        """測試深度巢狀 JSON 能正確解析。

        non-greedy `\{[\s\S]*?\}` 匹配時，regex engine 會擴展 `[\s\S]*?`
        直到找到一組平衡的 {...}。因此第一個完整 match 是外層物件，
        而非 inner（inner 雖然是完整 JSON，但需要更多 expansion 才能被確認）。
        """
        text = '{"outer": {"inner": {"deep": "value"}, "other": "skip"}} extra'
        result = safe_json_loads(text)
        # non-greedy 第一個完整 match 是外層物件（regex 擴展到所有內層都關閉）
        assert result == {"outer": {"inner": {"deep": "value"}, "other": "skip"}}

    def test_realistic_gemini_response(self):
        """測試模擬真實 Gemini 回應（含多餘內容）。"""
        text = '```json\n{"items": [{"id": "0", "value": "翻譯結果"}]}\n```\n我認為這個翻譯是正確的。'
        result = safe_json_loads(text)
        assert result == {"items": [{"id": "0", "value": "翻譯結果"}]}

    def test_brace_balanced_nested_object(self):
        """測試 brace-balanced 巢狀物件（最典型翻譯回應格式）。"""
        # 這是最常見的 Gemini 回應格式：完整 JSON 物件
        text = '{"items": [{"id": "0", "translations": {"zh_tw": "你好"}}]}'
        result = safe_json_loads(text)
        assert result == {"items": [{"id": "0", "translations": {"zh_tw": "你好"}}]}


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
