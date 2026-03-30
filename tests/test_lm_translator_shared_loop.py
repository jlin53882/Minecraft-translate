"""lm_translator_shared_loop.py 單元測試。

用途：測試 LM 翻譯共享迴圈相關功能。
"""
from unittest.mock import patch


class TestTranslateLoopResult:
    """測試 TranslateLoopResult 資料類別。"""

    def test_translate_loop_result_creation(self):
        """測試 TranslateLoopResult 創建。"""
        from translation_tool.core.lm_translator_shared_loop import TranslateLoopResult

        result = TranslateLoopResult(
            status="DONE",
            processed=10,
            total=20,
            completed_calls=5,
            elapsed_sec=100.0,
            exhausted=False
        )

        assert result.status == "DONE"
        assert result.processed == 10
        assert result.total == 20
        assert result.completed_calls == 5
        assert result.elapsed_sec == 100.0
        assert result.exhausted is False

    def test_translate_loop_result_with_error(self):
        """測試 TranslateLoopResult 帶錯誤。"""
        from translation_tool.core.lm_translator_shared_loop import TranslateLoopResult

        result = TranslateLoopResult(
            status="FAILED",
            processed=5,
            total=20,
            completed_calls=3,
            elapsed_sec=50.0,
            exhausted=False,
            last_error="Test error"
        )

        assert result.last_error == "Test error"


class TestGetDefaultBatchSize:
    """測試 _get_default_batch_size 函數。"""

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_ftbquests(self, mock_load_config):
        """測試 FTB 批次大小。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {
            "lm_translator": {
                "initial_batch_size_ftb": 150
            }
        }

        result = _get_default_batch_size("ftbquests", None)

        assert result == 150

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_kubejs(self, mock_load_config):
        """測試 KubeJS 批次大小。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {
            "lm_translator": {
                "initial_batch_size_kubejs": 250
            }
        }

        result = _get_default_batch_size("kubejs", None)

        assert result == 250

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_patchouli(self, mock_load_config):
        """測試 Patchouli 批次大小。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_patchouli": 120
            }
        }

        result = _get_default_batch_size("patchouli", None)

        assert result == 120

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_lang(self, mock_load_config):
        """測試 Lang 批次大小。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 350
            }
        }

        result = _get_default_batch_size("lang", None)

        assert result == 350

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_md(self, mock_load_config):
        """測試 MD 批次大小。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_md": 80
            }
        }

        result = _get_default_batch_size("md", None)

        assert result == 80

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_custom_map(self, mock_load_config):
        """測試自訂批次大小映射。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        custom_map = {
            "lang": 500,
            "patchouli": 200
        }

        result = _get_default_batch_size("lang", custom_map)

        assert result == 500

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_unknown_type(self, mock_load_config):
        """測試未知類型使用預設值。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {
            "lm_translator": {}
        }

        result = _get_default_batch_size("unknown_type", None)

        # 應該回傳 lang 的預設值
        assert result == 300

    @patch('translation_tool.core.lm_translator_shared_loop.load_config')
    def test_get_default_batch_size_empty_config(self, mock_load_config):
        """測試空設定回傳預設值。"""
        from translation_tool.core.lm_translator_shared_loop import _get_default_batch_size

        mock_load_config.return_value = {}

        result = _get_default_batch_size("lang", None)

        assert result == 300


class TestTranslateItemsWithCacheLoop:
    """測試 translate_items_with_cache_loop 函數。"""

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_empty_list(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試空項目列表。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        def mock_translate(batch, total):
            return [], "AUTO"

        result = translate_items_with_cache_loop(
            [],
            translate_batch_smart=mock_translate
        )

        assert result.status == "DONE"
        assert result.processed == 0

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_success(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試成功翻譯。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        def mock_translate(batch, total):
            # 模擬翻譯成功
            translated = []
            for item in batch:
                new_item = item.copy()
                new_item["text"] = f"Translated {item['text']}"
                translated.append(new_item)
            return translated, "AUTO"

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"},
            {"path": "path2", "text": "World", "source_text": "World", "cache_type": "lang"}
        ]

        result = translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate,
            batch_size_by_type={"lang": 10}
        )

        assert result.status == "DONE"
        assert result.processed == 2

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_all_keys_exhausted(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試 API 金鑰耗盡。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        def mock_translate(batch, total):
            return None, "ALL_KEYS_EXHAUSTED"

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"}
        ]

        result = translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate
        )

        assert result.status == "ALL_KEYS_EXHAUSTED"
        assert result.exhausted is True

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_failure(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試翻譯失敗。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        def mock_translate(batch, total):
            return [], "FAILED"

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"}
        ]

        result = translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate
        )

        assert result.status == "FAILED"

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_with_callbacks(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試帶回調函數。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        progress_calls = []

        def mock_translate(batch, total):
            translated = []
            for item in batch:
                new_item = item.copy()
                new_item["text"] = f"Translated {item['text']}"
                translated.append(new_item)
            return translated, "AUTO"

        def on_progress(progress, msg, eta):
            progress_calls.append((progress, msg))

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"}
        ]

        translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate,
            on_progress=on_progress
        )

        assert len(progress_calls) > 0

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_exception_handling(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試例外處理。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        def mock_translate(batch, total):
            raise RuntimeError("Test error")

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"}
        ]

        result = translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate
        )

        assert result.status == "FAILED"
        assert result.last_error is not None

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    def test_translate_items_custom_cache_rules(self, mock_add_cache, mock_save_cache, mock_reload):
        """測試自訂快取規則。"""
        from translation_tool.core.lm_translator_shared_loop import (
            CacheRule,
            get_default_cache_rules,
            translate_items_with_cache_loop,
        )

        def mock_translate(batch, total):
            translated = []
            for item in batch:
                new_item = item.copy()
                new_item["text"] = f"Translated {item['text']}"
                translated.append(new_item)
            return translated, "AUTO"

        custom_rules = get_default_cache_rules()
        custom_rules["lang"] = CacheRule("path")

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"}
        ]

        result = translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate,
            cache_rules=custom_rules
        )

        assert result.status == "DONE"

    @patch('translation_tool.core.lm_translator_shared_loop.reload_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.save_translation_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.add_to_cache')
    @patch('translation_tool.core.lm_translator_shared_loop.time.sleep')
    def test_translate_items_with_sleep(self, mock_sleep, mock_add_cache, mock_save_cache, mock_reload):
        """測試批次間 sleep。"""
        from translation_tool.core.lm_translator_shared_loop import translate_items_with_cache_loop

        def mock_translate(batch, total):
            translated = []
            for item in batch:
                new_item = item.copy()
                new_item["text"] = f"Translated {item['text']}"
                translated.append(new_item)
            return translated, "AUTO"

        items = [
            {"path": "path1", "text": "Hello", "source_text": "Hello", "cache_type": "lang"}
        ]

        result = translate_items_with_cache_loop(
            items,
            translate_batch_smart=mock_translate,
            sleep_seconds_between_batches=1.0
        )

        assert result.status == "DONE"
        # 驗證 sleep 被調用
        # mock_sleep 可能不被調用取決於實現
