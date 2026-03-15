"""lang_processing_format.py 單元測試。

用途：測試語言處理格式相關功能。
"""
import pytest
import re
from unittest.mock import patch, Mock


class TestConvertOnlyCjk:
    """測試 convert_only_cjk 函數。"""

    def test_empty_string(self):
        """測試空字串。"""
        from translation_tool.core.lang_processing_format import convert_only_cjk
        result = convert_only_cjk("")
        assert result == ""

    def test_no_chinese(self):
        """測試無中文的情況。"""
        from translation_tool.core.lang_processing_format import convert_only_cjk
        result = convert_only_cjk("Hello World")
        assert result == "Hello World"

    def test_chinese_only(self):
        """測試純中文。"""
        from translation_tool.core.lang_processing_format import convert_only_cjk
        result = convert_only_cjk("測試中文")
        assert isinstance(result, str)

    def test_mixed_content(self):
        """測試混合內容。"""
        from translation_tool.core.lang_processing_format import convert_only_cjk
        result = convert_only_cjk("Hello 你好 World 世界")
        assert "Hello" in result
        assert "World" in result

    def test_with_replace_rules(self):
        """測試套用替換規則。"""
        from translation_tool.core.lang_processing_format import convert_only_cjk
        rules = {"測試": "測試替換"}
        result = convert_only_cjk("這是測試", rules)
        assert isinstance(result, str)


class TestOpenccMarkdownSafe:
    """測試 opencc_markdown_safe 函數。"""

    def test_plain_text(self):
        """測試純文字。"""
        from translation_tool.core.lang_processing_format import opencc_markdown_safe
        result = opencc_markdown_safe("Hello World")
        assert result == "Hello World"

    def test_chinese_text(self):
        """測試中文文字。"""
        from translation_tool.core.lang_processing_format import opencc_markdown_safe
        result = opencc_markdown_safe("測試中文")
        assert isinstance(result, str)

    def test_code_block_preserved(self):
        """測試程式碼區塊保留。"""
        from translation_tool.core.lang_processing_format import opencc_markdown_safe
        md = "```python\nprint('hello')\n```"
        result = opencc_markdown_safe(md)
        assert "```python" in result
        assert "print('hello')" in result

    def test_code_block_translatable_json(self):
        """測試可翻譯的 JSON 程式碼區塊。"""
        from translation_tool.core.lang_processing_format import opencc_markdown_safe
        md = "```json\n{\"key\": \"值\"}\n```"
        result = opencc_markdown_safe(md)
        assert "```json" in result

    def test_inline_code(self):
        """測試行內程式碼。"""
        from translation_tool.core.lang_processing_format import opencc_markdown_safe
        md = "這是 `程式碼` 測試"
        result = opencc_markdown_safe(md)
        assert "`" in result

    def test_with_replace_rules(self):
        """測試套用替換規則。"""
        from translation_tool.core.lang_processing_format import opencc_markdown_safe
        rules = {"測試": "替換"}
        result = opencc_markdown_safe("測試", rules)
        assert isinstance(result, str)


class TestRemoveTranslatedKeys:
    """測試 remove_translated_keys 函數。"""

    def test_all_keys_untranslated(self):
        """測試所有鍵都未翻譯。"""
        from translation_tool.core.lang_processing_format import remove_translated_keys
        en_dict = {"key1": "value1", "key2": "value2"}
        tw_dict = {}
        result = remove_translated_keys(en_dict, tw_dict)
        assert len(result) == 2

    def test_all_keys_translated(self):
        """測試所有鍵都已翻譯。"""
        from translation_tool.core.lang_processing_format import remove_translated_keys
        en_dict = {"key1": "value1", "key2": "value2"}
        tw_dict = {"key1": "翻譯1", "key2": "翻譯2"}
        result = remove_translated_keys(en_dict, tw_dict)
        assert len(result) == 0

    def test_partial_translated(self):
        """測試部分翻譯。"""
        from translation_tool.core.lang_processing_format import remove_translated_keys
        en_dict = {"key1": "value1", "key2": "value2", "key3": "value3"}
        tw_dict = {"key1": "翻譯1"}
        result = remove_translated_keys(en_dict, tw_dict)
        assert "key2" in result
        assert "key3" in result
        assert "key1" not in result

    def test_empty_tw_dict(self):
        """測試空的目的端字典。"""
        from translation_tool.core.lang_processing_format import remove_translated_keys
        en_dict = {"key1": "value1"}
        tw_dict = {}
        result = remove_translated_keys(en_dict, tw_dict)
        assert "key1" in result

    def test_whitespace_translation(self):
        """測試空白翻譯視為未翻譯。"""
        from translation_tool.core.lang_processing_format import remove_translated_keys
        en_dict = {"key1": "value1", "key2": "value2"}
        tw_dict = {"key1": "   "}  # 空白視為未翻譯
        result = remove_translated_keys(en_dict, tw_dict)
        assert "key1" in result


class TestCompareAndRemoveTranslatedFromEn:
    """測試 compare_and_remove_translated_from_en 函數。"""

    def test_basic_functionality(self):
        """測試基本功能。"""
        from translation_tool.core.lang_processing_format import (
            compare_and_remove_translated_from_en,
        )
        en_source = {"key1": "value1", "key2": "value2"}
        tw_base = {"key1": "翻譯1"}
        result = compare_and_remove_translated_from_en(en_source, tw_base)
        assert "key2" in result
        assert "key1" not in result

    def test_empty_en_source(self):
        """測試空來源字典。"""
        from translation_tool.core.lang_processing_format import (
            compare_and_remove_translated_from_en,
        )
        en_source = {}
        tw_base = {"key1": "翻譯1"}
        result = compare_and_remove_translated_from_en(en_source, tw_base)
        assert len(result) == 0


class TestDumpJsonBytes:
    """測試 dump_json_bytes 函數。"""

    def test_dict_to_bytes(self):
        """測試字典轉換為位元組。"""
        from translation_tool.core.lang_processing_format import dump_json_bytes
        obj = {"key": "value", "number": 123}
        result = dump_json_bytes(obj)
        assert isinstance(result, bytes)
        assert b"key" in result

    def test_nested_dict(self):
        """測試嵌套字典。"""
        from translation_tool.core.lang_processing_format import dump_json_bytes
        obj = {"outer": {"inner": "value"}}
        result = dump_json_bytes(obj)
        assert isinstance(result, bytes)

    def test_list_to_bytes(self):
        """測試列表轉換為位元組。"""
        from translation_tool.core.lang_processing_format import dump_json_bytes
        obj = [1, 2, 3, "test"]
        result = dump_json_bytes(obj)
        assert isinstance(result, bytes)


class TestTranslateMarkdown:
    """測試 translate_markdown 函數。"""

    def test_plain_markdown(self):
        """測試普通 Markdown。"""
        from translation_tool.core.lang_processing_format import translate_markdown
        
        def mock_translate(text, rules):
            return text.replace("測試", "測試翻譯")
        
        result = translate_markdown("# 標題\n\n這是測試內容", mock_translate, None)
        assert isinstance(result, str)

    def test_patchouli_books_preserved(self):
        """測試 Patchouli 書籍保留 XML 標籤。"""
        from translation_tool.core.lang_processing_format import translate_markdown
        
        def mock_translate(text, rules):
            return text
        
        md_content = "<page>測試內容</page>"
        result = translate_markdown(md_content, mock_translate, None, file_path="assets/mod/patchouli_books/book/test.md")
        assert "<page>" in result

    def test_yaml_front_matter(self):
        """測試 YAML 前置內容。"""
        from translation_tool.core.lang_processing_format import translate_markdown
        
        def mock_translate(text, rules):
            return text.replace("標題", "標題翻譯")
        
        md = "---\ntitle: 標題\n---\n內容"
        result = translate_markdown(md, mock_translate, None)
        assert "---" in result


class TestTranslatePlainText:
    """測試 translate_plain_text 函數。"""

    def test_basic_translation(self):
        """測試基本翻譯。"""
        from translation_tool.core.lang_processing_format import translate_plain_text
        
        def mock_translate(text, rules):
            return text.replace("測試", "測試翻譯")
        
        result = translate_plain_text("這是測試", mock_translate, None, "test.txt")
        assert "測試翻譯" in result


class TestGetTextProcessor:
    """測試 get_text_processor 函數。"""

    def test_markdown_processor(self):
        """測試 Markdown 處理器。"""
        from translation_tool.core.lang_processing_format import get_text_processor
        processor = get_text_processor(".md")
        assert callable(processor)

    def test_json_processor(self):
        """測試 JSON 處理器。"""
        from translation_tool.core.lang_processing_format import get_text_processor
        processor = get_text_processor(".json")
        assert callable(processor)

    def test_unknown_extension(self):
        """測試未知副檔名。"""
        from translation_tool.core.lang_processing_format import get_text_processor
        processor = get_text_processor(".unknown")
        assert processor is None

    def test_case_insensitive(self):
        """測試大小寫不敏感。"""
        from translation_tool.core.lang_processing_format import get_text_processor
        processor = get_text_processor(".MD")
        assert callable(processor)
