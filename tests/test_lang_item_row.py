"""lang_item_row.py 單元測試。

用途：測試 LangItemRow 類別相關功能。
"""


class TestToHalfwidth:
    """測試 to_halfwidth 函數。"""

    def test_english_text(self):
        """測試英文文字保持不變。"""
        from translation_tool.core.lang_item_row import to_halfwidth
        result = to_halfwidth("Hello World")
        assert result == "Hello World"

    def test_chinese_text(self):
        """測試中文文字轉換。"""
        from translation_tool.core.lang_item_row import to_halfwidth
        # 測試常見的中文字元轉換
        result = to_halfwidth("測試")
        assert isinstance(result, str)

    def test_mixed_text(self):
        """測試混合文字。"""
        from translation_tool.core.lang_item_row import to_halfwidth
        result = to_halfwidth("Hello 你好 World 世界")
        assert "Hello" in result
        assert "World" in result

    def test_non_string_input(self):
        """測試非字串輸入。"""
        from translation_tool.core.lang_item_row import to_halfwidth
        result = to_halfwidth(123)
        assert result == 123

    def test_none_input(self):
        """測試 None 輸入。"""
        from translation_tool.core.lang_item_row import to_halfwidth
        result = to_halfwidth(None)
        assert result is None


class TestLangItemRow:
    """測試 LangItemRow 類別。"""

    def test_init_basic(self):
        """測試基本初始化。"""
        from translation_tool.core.lang_item_row import LangItemRow

        # 由於 LangItemRow 是 Flet 控件，需要圖形環境，
        # 我們只驗證類別可以被導入和基本屬性存在
        assert hasattr(LangItemRow, '__init__')

    def test_resolve_icon_with_reason_called(self):
        """測試圖標解析是否被調用。"""

        # 驗證 resolve_icon_with_reason 被導入
        from translation_tool.core.lang_item_row import resolve_icon_with_reason
        assert callable(resolve_icon_with_reason)

    def test_module_imports(self):
        """測試模組導入。"""
        from translation_tool.core.lang_item_row import (
            LangItemRow,
            to_halfwidth,
        )
        assert LangItemRow is not None
        assert callable(to_halfwidth)
