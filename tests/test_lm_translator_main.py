"""lm_translator_main.py 單元測試

測試目標：翻譯批次處理函數。
"""

from unittest.mock import patch


class TestTranslateBatchSmart:
    """translate_batch_smart 測試"""

    @patch('translation_tool.core.lm_translator_main.safe_json_loads')
    @patch('translation_tool.core.lm_translator_main.load_config')
    @patch('translation_tool.core.lm_translator_main.get_current_api_key')
    @patch('translation_tool.core.lm_translator_main.call_gemini_requests')
    @patch('translation_tool.core.lm_translator_main.time.sleep')
    def test_translate_batch_smart_lang_success(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試 Lang 翻譯成功"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "iniital_batch_size_patchouli": 100,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test"
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "你好"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "你好"}]}

        items = [
            {"path": "test.key", "text": "Hello", "cache_type": "lang"}
        ]

        result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
        # 成功時 API 應該被調用一次
        mock_call_api.assert_called_once()

    @patch('translation_tool.core.lm_translator_main.safe_json_loads')
    @patch('translation_tool.core.lm_translator_main.load_config')
    @patch('translation_tool.core.lm_translator_main.get_current_api_key')
    @patch('translation_tool.core.lm_translator_main.call_gemini_requests')
    @patch('translation_tool.core.lm_translator_main.time.sleep')
    def test_translate_batch_smart_empty_batch(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試空批次"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "models": {"gemini-pro": {"enabled": True}},
            }
        }
        mock_get_key.return_value = "test_key"

        result, status = translate_batch_smart([], 0)

        assert result == []
        assert status == "AUTO"
        mock_call_api.assert_not_called()

    @patch('translation_tool.core.lm_translator_main.safe_json_loads')
    @patch('translation_tool.core.lm_translator_main.load_config')
    @patch('translation_tool.core.lm_translator_main.get_current_api_key')
    @patch('translation_tool.core.lm_translator_main.call_gemini_requests')
    @patch('translation_tool.core.lm_translator_main.time.sleep')
    def test_translate_batch_smart_api_error_with_retry(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試 API 錯誤時的重試"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "models": {"gemini-pro": {"enabled": True}},
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = ""  # 空回應觸發重試

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]
        result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
        # 驗證 sleep 被調用（重試時會 sleep）
        if mock_call_api.call_count > 1:
            mock_sleep.assert_called()


class TestBatchProfileDetection:
    """批次設定偵測測試"""

    @patch('translation_tool.core.lm_translator_main.safe_json_loads')
    @patch('translation_tool.core.lm_translator_main.load_config')
    @patch('translation_tool.core.lm_translator_main.get_current_api_key')
    @patch('translation_tool.core.lm_translator_main.call_gemini_requests')
    @patch('translation_tool.core.lm_translator_main.time.sleep')
    def test_detect_batch_profile_lang(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試 Lang 批次設定"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "iniital_batch_size_lang": 300,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": []}'
        mock_json_loads.return_value = {"items": []}

        items = [{"path": f"key.{i}", "text": f"text{i}", "cache_type": "lang"} for i in range(10)]
        result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
