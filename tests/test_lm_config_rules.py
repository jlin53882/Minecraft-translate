"""lm_config_rules.py 單元測試。

用途：測試 LM 翻譯配置與規則相關功能。
"""
import pytest
from unittest.mock import patch


class TestAPIKeyManagement:
    """測試 API Key 管理函數。"""

    @pytest.fixture(autouse=True)
    def reset_key_tracker(self):
        """每個測試執行後重置 KeyIndexTracker 狀態，避免跨測試污染。"""
        yield
        from translation_tool.core.lm_config_rules import reset_key_index
        reset_key_index()

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_get_current_api_key_with_keys(self, mock_load_config):
        """測試取得當前 API Key（有金鑰時）。"""
        from translation_tool.core.lm_config_rules import get_current_api_key
        
        mock_load_config.return_value = {
            "lm_translator": {
                "keys": ["AIzaTestKey123", "AIzaTestKey456"]
            }
        }
        
        result = get_current_api_key()
        
        assert result == "AIzaTestKey123"

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_get_current_api_key_empty(self, mock_load_config):
        """測試取得當前 API Key（無金鑰時）。"""
        from translation_tool.core.lm_config_rules import get_current_api_key
        
        mock_load_config.return_value = {"lm_translator": {"keys": []}}
        
        result = get_current_api_key()
        
        assert result == ""

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_rotate_api_key_success(self, mock_load_config):
        """測試 API Key 輪換（成功）。"""
        from translation_tool.core.lm_config_rules import rotate_api_key, reset_key_index, get_current_key_index
        
        mock_load_config.return_value = {
            "lm_translator": {
                "keys": ["AIzaTestKey123", "AIzaTestKey456"]
            }
        }
        
        # 重置索引
        reset_key_index()
        
        result = rotate_api_key()
        
        assert result is True
        assert get_current_key_index() == 1

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_rotate_api_key_no_more_keys(self, mock_load_config):
        """測試 API Key 輪換（無更多金鑰）。"""
        from translation_tool.core.lm_config_rules import rotate_api_key, reset_key_index
        
        mock_load_config.return_value = {
            "lm_translator": {
                "keys": ["AIzaTestKey123"]
            }
        }
        
        # 重置索引
        reset_key_index()
        
        result = rotate_api_key()
        
        assert result is False

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_validate_api_keys_success(self, mock_load_config):
        """測試 API Key 驗證（成功）。"""
        from translation_tool.core.lm_config_rules import validate_api_keys
        
        mock_load_config.return_value = {
            "lm_translator": {
                "keys": ["AIzaTestKey123", "AIzaTestKey456"]
            }
        }
        
        # 不應該拋出異常
        validate_api_keys()

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_validate_api_keys_empty(self, mock_load_config):
        """測試 API Key 驗證（無金鑰）。"""
        from translation_tool.core.lm_config_rules import validate_api_keys
        
        mock_load_config.return_value = {"lm_translator": {"keys": []}}
        
        with pytest.raises(RuntimeError, match="沒有找到任何 API Key"):
            validate_api_keys()

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_validate_api_keys_invalid_format(self, mock_load_config):
        """測試 API Key 驗證（無效格式）。"""
        from translation_tool.core.lm_config_rules import validate_api_keys
        
        mock_load_config.return_value = {
            "lm_translator": {
                "keys": ["InvalidKey123"]
            }
        }
        
        with pytest.raises(RuntimeError, match="無效的 API Key 格式"):
            validate_api_keys()

    def test_validate_api_keys_from_ui_success(self):
        """測試 UI API Key 驗證（成功）。"""
        from translation_tool.core.lm_config_rules import validate_api_keys_from_ui
        
        # 不應該拋出異常
        validate_api_keys_from_ui(["AIzaTestKey123"])

    def test_validate_api_keys_from_ui_invalid(self):
        """測試 UI API Key 驗證（無效）。"""
        from translation_tool.core.lm_config_rules import validate_api_keys_from_ui
        
        with pytest.raises(RuntimeError, match="無效的 API Key 格式"):
            validate_api_keys_from_ui(["InvalidKey"])


class TestCJKDetection:
    """測試 CJK 文字檢測。"""

    def test_contains_cjk_chinese(self):
        """測試包含中文。"""
        from translation_tool.core.lm_config_rules import contains_cjk
        
        assert contains_cjk("你好世界") is True

    def test_contains_cjk_japanese(self):
        """測試包含日文。"""
        from translation_tool.core.lm_config_rules import contains_cjk
        
        assert contains_cjk("こんにちは") is True

    def test_contains_cjk_korean(self):
        """測試包含韓文。"""
        from translation_tool.core.lm_config_rules import contains_cjk
        
        assert contains_cjk("안녕하세요") is True

    def test_contains_cjk_english_only(self):
        """測試只有英文。"""
        from translation_tool.core.lm_config_rules import contains_cjk
        
        assert contains_cjk("Hello World") is False

    def test_contains_cjk_empty_string(self):
        """測試空字串。"""
        from translation_tool.core.lm_config_rules import contains_cjk
        
        assert contains_cjk("") is False

    def test_contains_cjk_none_input(self):
        """測試 None 輸入。"""
        from translation_tool.core.lm_config_rules import contains_cjk
        
        assert contains_cjk(None) is False


class TestNeedsTranslationText:
    """測試是否需要翻譯的判斷。"""

    def test_needs_translation_empty(self):
        """測試空字串。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text("") is False

    def test_needs_translation_none(self):
        """測試 None 輸入。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text(None) is False

    def test_needs_translation_chinese(self):
        """測試中文（不需要翻譯）。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text("你好") is False

    def test_needs_translation_english(self):
        """測試英文（需要翻譯）。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text("Hello") is True

    def test_needs_translation_digit(self):
        """測試純數字。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text("123") is False

    def test_needs_translation_section_symbol(self):
        """測試章節符號開頭。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text("§lBold Text") is False

    def test_needs_translation_token(self):
        """測試 token 格式。"""
        from translation_tool.core.lm_config_rules import needs_translation_text
        
        assert needs_translation_text("$(some.token)") is False


class TestValueFullyTranslated:
    """測試值是否完全翻譯的判斷。"""

    def test_value_fully_translated_string(self):
        """測試字串翻譯狀態。"""
        from translation_tool.core.lm_config_rules import value_fully_translated
        
        assert value_fully_translated("已翻譯的文字") is True
        assert value_fully_translated("") is False

    def test_value_fully_translated_list(self):
        """測試列表翻譯狀態。"""
        from translation_tool.core.lm_config_rules import value_fully_translated
        
        # 全部非空視為已翻譯
        assert value_fully_translated(["item1", "item2"]) is True
        # 包含空字串
        assert value_fully_translated(["item1", ""]) is False

    def test_value_fully_translated_other_types(self):
        """測試其他類型。"""
        from translation_tool.core.lm_config_rules import value_fully_translated
        
        assert value_fully_translated(123) is True
        assert value_fully_translated({"key": "value"}) is True
        assert value_fully_translated(None) is True


class TestBuildSkipTermsPattern:
    """測試跳過術語正則表達式建構。"""

    def test_build_skip_terms_pattern_single(self):
        """測試單一術語。"""
        from translation_tool.core.lm_config_rules import build_skip_terms_pattern
        
        pattern = build_skip_terms_pattern(["discord"])
        
        assert pattern is not None
        assert pattern.search("discord") is not None
        assert pattern.search("DISCORD") is not None

    def test_build_skip_terms_pattern_multiple(self):
        """測試多個術語。"""
        from translation_tool.core.lm_config_rules import build_skip_terms_pattern
        
        pattern = build_skip_terms_pattern(["api", "discord", "github"])
        
        assert pattern.search("api") is not None
        assert pattern.search("discord") is not None
        assert pattern.search("github") is not None

    def test_build_skip_terms_pattern_escape(self):
        """測試特殊字元轉義。"""
        from translation_tool.core.lm_config_rules import build_skip_terms_pattern
        
        pattern = build_skip_terms_pattern(["test.key"])
        
        assert pattern.search("test.key") is not None


class TestIsValueTranslatable:
    """測試值是否可翻譯的判斷。"""

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_value_translatable_lang_true(self, mock_load_config):
        """測試 lang 值可翻譯。"""
        from translation_tool.core.lm_config_rules import is_value_translatable
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text", "name"],
                    "skip_terms": []
                }
            }
        }
        
        assert is_value_translatable("Hello World", is_lang=True) is True

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_value_translatable_cjk(self, mock_load_config):
        """測試含 CJK 的值不可翻譯。"""
        from translation_tool.core.lm_config_rules import is_value_translatable
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text"],
                    "skip_terms": []
                }
            }
        }
        
        assert is_value_translatable("你好", is_lang=True) is False

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_value_translatable_tech_pattern(self, mock_load_config):
        """測試技術模式不可翻譯。"""
        from translation_tool.core.lm_config_rules import is_value_translatable
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text"],
                    "skip_terms": []
                }
            }
        }
        
        # minecraft:xxx 格式
        assert is_value_translatable("minecraft:diamond", is_lang=True) is False

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_value_translatable_empty(self, mock_load_config):
        """測試空值不可翻譯。"""
        from translation_tool.core.lm_config_rules import is_value_translatable
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text"],
                    "skip_terms": []
                }
            }
        }
        
        assert is_value_translatable("", is_lang=True) is False

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_value_translatable_roman_numeral(self, mock_load_config):
        """測試羅馬數字不可翻譯。"""
        from translation_tool.core.lm_config_rules import is_value_translatable
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text"],
                    "skip_terms": []
                }
            }
        }
        
        assert is_value_translatable("III", is_lang=True) is False

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_value_translatable_digit(self, mock_load_config):
        """測試純數字不可翻譯。"""
        from translation_tool.core.lm_config_rules import is_value_translatable
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text"],
                    "skip_terms": []
                }
            }
        }
        
        assert is_value_translatable("123", is_lang=True) is False


class TestIsTranslatableField:
    """測試欄位是否可翻譯的判斷。"""

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_translatable_field_true(self, mock_load_config):
        """測試可翻譯欄位。"""
        from translation_tool.core.lm_config_rules import is_translatable_field
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text", "name", "description"]
                }
            }
        }
        
        assert is_translatable_field("item_text") is True
        assert is_translatable_field("display_name") is True

    @patch('translation_tool.core.lm_config_rules.load_config')
    def test_is_translatable_field_false(self, mock_load_config):
        """測試不可翻譯欄位。"""
        from translation_tool.core.lm_config_rules import is_translatable_field
        
        mock_load_config.return_value = {
            "lm_translator": {
                "translator": {
                    "translatable_keywords": ["text", "name"]
                }
            }
        }
        
        assert is_translatable_field("id") is False
        assert is_translatable_field("damage") is False
