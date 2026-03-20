"""
ATK-001 測試：translate_batch_smart 的 dry_run / export_cache_only 參數失效問題

缺口描述：
  translate_batch_smart(batch_items, total, dry_run, export_cache_only)
  內部呼叫 _execute_translation(items, total, dry_run, export_cache_only)
  但 _execute_translation 簽名是：
    _execute_translation(items, batch_size, batch_profile, total, dry_run=False, export_cache_only=False)
  結果：
    - total 被當成 batch_size（位置錯配）
    - dry_run 被當成 batch_profile（位置錯配）
    - export_cache_only 完全丢失（使用預設值 False）
  _execute_translation 代理至 translate_batch_smart_old(items, total)，
  完全忽略 dry_run 與 export_cache_only。

測試目標（Arrange / Act / Assert 三段式）：
  1. dry_run=True 時，call_gemini_requests 不應被呼叫
  2. export_cache_only=True 時，call_gemini_requests 不應被呼叫
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


class TestAtk001DryRunParameter:
    """ATK-001：dry_run / export_cache_only 參數失效"""

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_dry_run_true_should_not_call_api(
        self,
        mock_sleep,
        mock_call_api,
        mock_get_key,
        mock_config,
        mock_json_loads,
    ):
        """
        Arrange：設定 mock，準備帶有有效文字的批次項目
        Act：呼叫 translate_batch_smart(dry_run=True)
        Assert：call_gemini_requests 不應被呼叫（預期失敗，表示 Bug 存在）
        """
        from translation_tool.core.lm_translator_main import translate_batch_smart

        # 模擬有效設定檔（避免 load_config 失敗）
        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "iniital_batch_size_patchouli": 100,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test",
            }
        }
        mock_get_key.return_value = "test_key"
        # 模擬 API 回應（正常翻譯結果）
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}

        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
        ]

        # Act：dry_run=True → 預期不應呼叫 API
        result, status = translate_batch_smart(items, 1, dry_run=True)

        # Assert：call_gemini_requests 從未被呼叫
        # 目前（修復前）：此斷言會失敗，因為 API 仍被呼叫（Bug）
        mock_call_api.assert_not_called()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_export_cache_only_true_should_not_call_api(
        self,
        mock_sleep,
        mock_call_api,
        mock_get_key,
        mock_config,
        mock_json_loads,
    ):
        """
        Arrange：設定 mock，準備帶有有效文字的批次項目
        Act：呼叫 translate_batch_smart(export_cache_only=True)
        Assert：call_gemini_requests 不應被呼叫（預期失敗，表示 Bug 存在）
        """
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "iniital_batch_size_patchouli": 100,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test",
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}

        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
        ]

        # Act：export_cache_only=True → 預期不應呼叫 API
        result, status = translate_batch_smart(items, 1, export_cache_only=True)

        # Assert：call_gemini_requests 從未被呼叫
        # 目前（修復前）：此斷言會失敗，因為 API 仍被呼叫（Bug）
        mock_call_api.assert_not_called()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_both_flags_true_should_not_call_api(
        self,
        mock_sleep,
        mock_call_api,
        mock_get_key,
        mock_config,
        mock_json_loads,
    ):
        """
        Arrange：設定 mock
        Act：dry_run=True 且 export_cache_only=True
        Assert：call_gemini_requests 不應被呼叫
        """
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "iniital_batch_size_patchouli": 100,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test",
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}

        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
        ]

        # Act：兩個 flag 同時為 True
        result, status = translate_batch_smart(
            items, 1, dry_run=True, export_cache_only=True
        )

        # Assert
        mock_call_api.assert_not_called()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_dry_run_false_should_call_api(
        self,
        mock_sleep,
        mock_call_api,
        mock_get_key,
        mock_config,
        mock_json_loads,
    ):
        """
        Arrange：設定 mock
        Act：dry_run=False（正常翻譯）
        Assert：call_gemini_requests 應該被呼叫（正常行為驗證）
        """
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "iniital_batch_size_patchouli": 100,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test",
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "\\u4f60\\u597d"}]}

        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"},
        ]

        # Act：dry_run=False（預設值）
        result, status = translate_batch_smart(items, 1, dry_run=False)

        # Assert：API 應該被呼叫
        mock_call_api.assert_called()
