"""lm_translator_shared.py 單元測試。

用途：測試 LM 翻譯共享模組的 re-export 介面。
測試重點：確認所有應該被 export 的項目都有正確導出。
"""


class TestSharedModuleExports:
    """測試 lm_translator_shared 模組的導出。"""

    def test_cache_rule_exported(self):
        """測試 CacheRule 導出。"""
        from translation_tool.core.lm_translator_shared import CacheRule
        
        assert CacheRule is not None

    def test_strict_src_types_exported(self):
        """測試 STRICT_SRC_TYPES 導出。"""
        from translation_tool.core.lm_translator_shared import STRICT_SRC_TYPES
        
        assert isinstance(STRICT_SRC_TYPES, (set, frozenset))
        assert "lang" in STRICT_SRC_TYPES

    def test_valid_hit_fn_exported(self):
        """測試 ValidHitFn 導出。"""
        from translation_tool.core.lm_translator_shared import ValidHitFn
        
        # ValidHitFn 是 Callable 類型
        assert ValidHitFn is not None

    def test_is_valid_hit_exported(self):
        """測試 _is_valid_hit 導出。"""
        from translation_tool.core.lm_translator_shared import _is_valid_hit
        
        assert callable(_is_valid_hit)

    def test_fast_split_items_by_cache_exported(self):
        """測試 fast_split_items_by_cache 導出。"""
        from translation_tool.core.lm_translator_shared import fast_split_items_by_cache
        
        assert callable(fast_split_items_by_cache)

    def test_get_default_cache_rules_exported(self):
        """測試 get_default_cache_rules 導出。"""
        from translation_tool.core.lm_translator_shared import get_default_cache_rules
        
        assert callable(get_default_cache_rules)

    def test_touch_set_exported(self):
        """測試 TouchSet 導出。"""
        from translation_tool.core.lm_translator_shared import TouchSet
        
        assert TouchSet is not None

    def test_write_dry_run_preview_exported(self):
        """測試 write_dry_run_preview 導出。"""
        from translation_tool.core.lm_translator_shared import write_dry_run_preview
        
        assert callable(write_dry_run_preview)

    def test_write_cache_hit_preview_exported(self):
        """測試 write_cache_hit_preview 導出。"""
        from translation_tool.core.lm_translator_shared import write_cache_hit_preview
        
        assert callable(write_cache_hit_preview)

    def test_translation_recorder_exported(self):
        """測試 TranslationRecorder 導出。"""
        from translation_tool.core.lm_translator_shared import TranslationRecorder
        
        assert TranslationRecorder is not None

    def test_translate_loop_result_exported(self):
        """測試 TranslateLoopResult 導出。"""
        from translation_tool.core.lm_translator_shared import TranslateLoopResult
        
        assert TranslateLoopResult is not None

    def test_get_default_batch_size_exported(self):
        """測試 _get_default_batch_size 導出。"""
        from translation_tool.core.lm_translator_shared import _get_default_batch_size
        
        assert callable(_get_default_batch_size)

    def test_translate_items_with_cache_loop_exported(self):
        """測試 translate_items_with_cache_loop 導出。"""
        from translation_tool.core.lm_translator_shared import translate_items_with_cache_loop
        
        assert callable(translate_items_with_cache_loop)


class TestSharedModuleAllList:
    """測試 __all__ 列表。"""

    def test_all_list_contains_expected(self):
        """測試 __all__ 包含預期項目。"""
        from translation_tool.core.lm_translator_shared import __all__
        
        expected = [
            "CacheRule",
            "STRICT_SRC_TYPES",
            "ValidHitFn",
            "_is_valid_hit",
            "fast_split_items_by_cache",
            "get_default_cache_rules",
            "TouchSet",
            "write_dry_run_preview",
            "write_cache_hit_preview",
            "TranslationRecorder",
            "TranslateLoopResult",
            "_get_default_batch_size",
            "translate_items_with_cache_loop",
        ]
        
        for item in expected:
            assert item in __all__, f"{item} should be in __all__"

    def test_all_list_count(self):
        """測試 __all__ 數量。"""
        from translation_tool.core.lm_translator_shared import __all__
        
        # 確認有合理數量的導出
        assert len(__all__) >= 10
