"""lm_translator_shared_cache.py 單元測試。

用途：測試 LM 翻譯共享快取相關功能。
"""
from unittest.mock import patch


class TestCacheRule:
    """測試 CacheRule 資料類別。"""

    def test_cache_rule_default(self):
        """測試預設 CacheRule。"""
        from translation_tool.core.lm_translator_shared_cache import CacheRule
        
        rule = CacheRule()
        
        assert rule.key_mode == "path|source_text"

    def test_cache_rule_custom(self):
        """測試自訂 CacheRule。"""
        from translation_tool.core.lm_translator_shared_cache import CacheRule
        
        rule = CacheRule(key_mode="path")
        
        assert rule.key_mode == "path"

    def test_cache_rule_make_key_path_mode(self):
        """測試路徑模式 key 生成。"""
        from translation_tool.core.lm_translator_shared_cache import CacheRule
        
        rule = CacheRule(key_mode="path")
        item = {"path": "test.path", "source_text": "Hello"}
        
        key = rule.make_key(item)
        
        assert key == "test.path"

    def test_cache_rule_make_key_combined_mode(self):
        """測試組合模式 key 生成。"""
        from translation_tool.core.lm_translator_shared_cache import CacheRule
        
        rule = CacheRule(key_mode="path|source_text")
        item = {"path": "test.path", "source_text": "Hello"}
        
        key = rule.make_key(item)
        
        assert key == "test.path|Hello"


class TestGetDefaultCacheRules:
    """測試 get_default_cache_rules 函數。"""

    def test_get_default_cache_rules(self):
        """測試取得預設快取規則。"""
        from translation_tool.core.lm_translator_shared_cache import get_default_cache_rules, CacheRule
        
        rules = get_default_cache_rules()
        
        assert "lang" in rules
        assert "patchouli" in rules
        assert "ftbquests" in rules
        assert "kubejs" in rules
        assert "md" in rules
        
        assert isinstance(rules["lang"], CacheRule)
        assert rules["lang"].key_mode == "path"
        assert rules["patchouli"].key_mode == "path|source_text"


class TestIsValidHit:
    """測試 _is_valid_hit 函數。"""

    def test_is_valid_hit_non_translated(self):
        """測試未翻譯內容。"""
        from translation_tool.core.lm_translator_shared_cache import _is_valid_hit
        
        item = {"cache_type": "lang", "source_text": "Hello"}
        entry = {"dst": "", "src": "Hello"}
        
        assert _is_valid_hit("", entry, item) is False

    def test_is_valid_hit_lang_strict(self):
        """測試 lang 類型嚴格比對。"""
        from translation_tool.core.lm_translator_shared_cache import _is_valid_hit
        
        # 匹配
        item = {"cache_type": "lang", "source_text": "Hello", "source": "Hello"}
        entry = {"dst": "你好", "src": "Hello"}
        
        assert _is_valid_hit("你好", entry, item) is True
        
        # 不匹配
        item = {"cache_type": "lang", "source_text": "Hello"}
        entry = {"dst": "你好", "src": "Different"}
        
        assert _is_valid_hit("你好", entry, item) is False

    def test_is_valid_hit_patchouli_not_strict(self):
        """測試 patchouli 類型非嚴格比對。"""
        from translation_tool.core.lm_translator_shared_cache import _is_valid_hit
        
        item = {"cache_type": "patchouli", "source_text": "Hello"}
        entry = {"dst": "你好"}
        
        assert _is_valid_hit("你好", entry, item) is True

    def test_is_valid_hit_empty_source(self):
        """測試空 source 情況。"""
        from translation_tool.core.lm_translator_shared_cache import _is_valid_hit
        
        item = {"cache_type": "lang", "source_text": ""}
        entry = {"dst": "你好", "src": ""}
        
        # 空的 source_text 應該回傳 False
        assert _is_valid_hit("你好", entry, item) is False


class TestFastSplitItemsByCache:
    """測試 fast_split_items_by_cache 函數。"""

    @patch('translation_tool.core.lm_translator_shared_cache.get_cache_dict_ref')
    def test_fast_split_items_empty(self, mock_get_cache):
        """測試空項目列表。"""
        from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
        
        mock_get_cache.return_value = {}
        
        cached, to_translate = fast_split_items_by_cache([])
        
        assert cached == []
        assert to_translate == []

    @patch('translation_tool.core.lm_translator_shared_cache.get_cache_dict_ref')
    def test_fast_split_items_all_cached(self, mock_get_cache):
        """測試全部命中快取。"""
        from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
        
        # 建立快取命中
        mock_get_cache.return_value = {
            "test.path": {"src": "Hello", "dst": "你好"}
        }
        
        items = [
            {"path": "test.path", "source_text": "Hello", "cache_type": "lang"}
        ]
        
        cached, to_translate = fast_split_items_by_cache(items)
        
        assert len(cached) == 1
        assert cached[0]["text"] == "你好"
        assert to_translate == []

    @patch('translation_tool.core.lm_translator_shared_cache.get_cache_dict_ref')
    def test_fast_split_items_none_cached(self, mock_get_cache):
        """測試無快取命中。"""
        from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
        
        mock_get_cache.return_value = {}
        
        items = [
            {"path": "test.path", "source_text": "Hello", "cache_type": "lang"}
        ]
        
        cached, to_translate = fast_split_items_by_cache(items)
        
        assert cached == []
        assert len(to_translate) == 1

    @patch('translation_tool.core.lm_translator_shared_cache.get_cache_dict_ref')
    def test_fast_split_items_partial_cached(self, mock_get_cache):
        """測試部分快取命中。"""
        from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
        
        def cache_side_effect(cache_type):
            if cache_type == "lang":
                return {
                    "path1": {"src": "Hello", "dst": "你好"}
                }
            return {}
        
        mock_get_cache.side_effect = cache_side_effect
        
        items = [
            {"path": "path1", "source_text": "Hello", "cache_type": "lang"},
            {"path": "path2", "source_text": "World", "cache_type": "lang"}
        ]
        
        cached, to_translate = fast_split_items_by_cache(items)
        
        assert len(cached) == 1
        assert len(to_translate) == 1

    @patch('translation_tool.core.lm_translator_shared_cache.get_cache_dict_ref')
    def test_fast_split_items_custom_rules(self, mock_get_cache):
        """測試自訂快取規則。"""
        from translation_tool.core.lm_translator_shared_cache import (
            fast_split_items_by_cache,
            CacheRule,
            get_default_cache_rules
        )
        
        mock_get_cache.return_value = {}
        
        custom_rules = get_default_cache_rules()
        custom_rules["lang"] = CacheRule("path|source_text")
        
        items = [
            {"path": "test.path", "source_text": "Hello", "cache_type": "lang"}
        ]
        
        cached, to_translate = fast_split_items_by_cache(items, cache_rules=custom_rules)
        
        # 使用自訂規則，沒有快取所以全部需要翻譯
        assert to_translate == items

    @patch('translation_tool.core.lm_translator_shared_cache.get_cache_dict_ref')
    def test_fast_split_items_custom_validator(self, mock_get_cache):
        """測試自訂驗證函數。"""
        from translation_tool.core.lm_translator_shared_cache import fast_split_items_by_cache
        
        mock_get_cache.return_value = {
            "test.path": {"src": "Hello", "dst": "你好"}
        }
        
        # 自訂驗證：永遠不回傳 True（模擬所有快取都無效）
        def never_valid(dst, entry, item):
            return False
        
        items = [
            {"path": "test.path", "source_text": "Hello", "cache_type": "lang"}
        ]
        
        cached, to_translate = fast_split_items_by_cache(
            items,
            is_valid_hit=never_valid
        )
        
        assert cached == []
        assert to_translate == items


class TestStrictSrcTypes:
    """測試 STRICT_SRC_TYPES 常數。"""

    def test_strict_src_types_contains_expected(self):
        """測試 STRICT_SRC_TYPES 包含預期類型。"""
        from translation_tool.core.lm_translator_shared_cache import STRICT_SRC_TYPES
        
        assert "lang" in STRICT_SRC_TYPES
        assert "kubejs" in STRICT_SRC_TYPES
        assert "ftbquests" in STRICT_SRC_TYPES
        assert "md" in STRICT_SRC_TYPES

    def test_strict_src_types_is_set(self):
        """測試 STRICT_SRC_TYPES 是集合類型。"""
        from translation_tool.core.lm_translator_shared_cache import STRICT_SRC_TYPES
        
        assert isinstance(STRICT_SRC_TYPES, (set, frozenset))
