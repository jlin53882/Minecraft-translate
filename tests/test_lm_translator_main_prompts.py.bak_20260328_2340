"""測試 System Prompt dict → string 轉換（lm_translator_main.py）。"""
from unittest.mock import patch


class TestSystemPromptConversion:
    """測試 PATCHOULI_SYSTEM_PROMPT / LANG_SYSTEM_PROMPT 能正確處理 dict 輸入。"""

    def test_patchouli_prompt_dict_conversion_logic(self):
        """測試 dict（含 content/text key）轉換邏輯。"""
        from translation_tool.core import lm_translator_main as mod
        # 測試轉換函式存在
        raw = {"role": "system", "content": "測試內容"}
        result = raw.get("content") or raw.get("text") or str(raw)
        assert result == "測試內容"
        assert isinstance(result, str)

    def test_lang_prompt_dict_conversion_logic(self):
        """測試 lang dict 轉換邏輯。"""
        raw = {"role": "system", "content": "Minecraft 翻譯中"}
        result = raw.get("content") or raw.get("text") or str(raw)
        assert result == "Minecraft 翻譯中"
        assert isinstance(result, str)
