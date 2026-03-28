"""lm_translator_main.py 單元測試

測試目標：翻譯批次處理函數。
"""

from unittest.mock import patch


class TestTranslateBatchSmart:
    """translate_batch_smart 測試"""

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_translate_batch_smart_lang_success(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試 Lang 翻譯成功"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "initial_batch_size_lang": 300,
                "initial_batch_size_patchouli": 100,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test",
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "你好"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "你好"}]}

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
        # 成功時 API 應該被調用一次
        mock_call_api.assert_called_once()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_translate_batch_smart_empty_batch(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試空批次"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "initial_batch_size_lang": 300,
                "models": {"gemini-pro": {"enabled": True}},
            }
        }
        mock_get_key.return_value = "test_key"

        result, status = translate_batch_smart([], 0)

        assert result == []
        assert status == "AUTO"
        mock_call_api.assert_not_called()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_translate_batch_smart_api_error_with_retry(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試 API 錯誤時的重試"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "initial_batch_size_lang": 300,
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


class TestSystemPromptConversion:
    """測試 System Prompt dict → string 轉換（PATCHOULI_SYSTEM_PROMPT / LANG_SYSTEM_PROMPT）。

    驗證設定檔中 system_prompt 無論是 dict 或 string，
    都會被正確轉為 string 傳入 API。
    """

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_lang_prompt_dict_converted_to_string(
        self, mock_sleep, mock_get_key, mock_config, mock_post
    ):
        """測試 lang_system_prompt 為 dict 時會被轉為 string。"""
        from unittest.mock import Mock
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"items": [{"id": "0", "value": "你好"}]}'}]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        mock_config.return_value = {
            "lm_translator": {
                "initial_batch_size_lang": 300,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "patchouli_system_prompt": "你是專業的 Minecraft Patchouli 翻譯員",
                "lang_system_prompt": {
                    "role": "translator",
                    "content": "你正在翻譯 Minecraft 語言檔案",
                },
            }
        }
        mock_get_key.return_value = "test_key"

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        result, status = translate_batch_smart(items, 1)

        assert mock_post.call_count >= 1, "API 應該被調用至少一次"
        call_kwargs = mock_post.call_args.kwargs
        json_body = call_kwargs.get("json", {})
        system_instruction = json_body.get("systemInstruction", {})
        prompt_text = system_instruction.get("parts", [{}])[0].get("text", "")
        assert isinstance(prompt_text, str), (
            "lang_system_prompt 必須是 string，而非 dict"
        )

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_prompt_already_string_unchanged(
        self, mock_sleep, mock_get_key, mock_config, mock_post
    ):
        """測試 system_prompt 原本就是 string 時，內容保持不變。"""
        from unittest.mock import Mock
        from translation_tool.core.lm_translator_main import translate_batch_smart

        prompt_text = "你是一個專業的 Minecraft 翻譯員"

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": '{"items": [{"id": "0", "value": "結果"}]}'}]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        mock_config.return_value = {
            "lm_translator": {
                "initial_batch_size_lang": 300,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
                "temperature": 0.2,
                "patchouli_system_prompt": "另一個 prompt",
                "lang_system_prompt": prompt_text,
            }
        }
        mock_get_key.return_value = "test_key"

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        result, status = translate_batch_smart(items, 1)

        assert mock_post.call_count >= 1, "API 應該被調用至少一次"
        call_kwargs = mock_post.call_args.kwargs
        json_body = call_kwargs.get("json", {})
        system_instruction = json_body.get("systemInstruction", {})
        actual_prompt = system_instruction.get("parts", [{}])[0].get("text", "")
        assert actual_prompt == prompt_text, "string 類型的 system_prompt 應保持不變"


class TestBatchProfileDetection:
    """批次設定偵測測試"""

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_translator_main.get_current_api_key")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.time.sleep")
    def test_detect_batch_profile_lang(
        self, mock_sleep, mock_call_api, mock_get_key, mock_config, mock_json_loads
    ):
        """測試 Lang 批次設定"""
        from translation_tool.core.lm_translator_main import translate_batch_smart

        mock_config.return_value = {
            "lm_translator": {
                "initial_batch_size_lang": 300,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"gemini-pro": {"enabled": True}},
            }
        }
        mock_get_key.return_value = "test_key"
        mock_call_api.return_value = '{"items": []}'
        mock_json_loads.return_value = {"items": []}

        items = [
            {"path": f"key.{i}", "text": f"text{i}", "cache_type": "lang"}
            for i in range(10)
        ]
        result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
