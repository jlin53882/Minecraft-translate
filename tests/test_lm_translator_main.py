"""lm_translator_main.py 單元測試

測試目標：翻譯批次處理函數。
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest


def test_batch_profile_and_response_helpers_preserve_translation_contract():
    from translation_tool.core.lm_translator_main import (
        _detect_batch_profile,
        _is_truncated_response,
        _normalize_translations,
    )

    assert _detect_batch_profile([{"cache_type": "ftbquests"}]) == "ftb"
    assert _detect_batch_profile([{"file": "mod/lang/en_us.json"}]) == "lang"
    assert _detect_batch_profile([{"file": "mod/patchouli/book.json"}]) == "patch"

    assert _normalize_translations(
        {"items": [{"id": "a", "value": "A"}, {"id": 1, "value": "B"}]}
    ) == {"a": "A", "1": "B"}
    assert _normalize_translations([{"text": "A"}, {"id": "x", "value": "B"}]) == {
        "0": "A",
        "x": "B",
    }
    assert _is_truncated_response('{"items": []}') is False
    assert _is_truncated_response('{"items": [') is True


def test_legacy_batch_name_is_only_a_compatibility_alias():
    from translation_tool.core import lm_translator_main as main

    assert main.translate_batch_smart_old is main._translate_batch_smart_impl


def test_batch_entrypoint_is_a_small_compatibility_facade():
    from translation_tool.core import lm_translator_main as main

    assert len(inspect.getsource(main._translate_batch_smart_impl).splitlines()) < 100
    assert main._translate_batch_smart_impl is not main._run_batch_state_machine


def test_batch_state_machine_has_separate_state_action_and_response_layers():
    from translation_tool.core import lm_translator_main as main

    function_lengths = {
        name: len(inspect.getsource(getattr(main, name)).splitlines())
        for name in (
            "_build_batch_runtime",
            "_prepare_batch",
            "_handle_batch_error",
            "_merge_batch_response",
            "_attempt_batch",
            "_run_batch_state_machine",
        )
    }
    assert function_lengths["_run_batch_state_machine"] < 100
    assert function_lengths["_attempt_batch"] < 120
    assert function_lengths["_merge_batch_response"] < 60
    assert function_lengths["_handle_batch_error"] < 160

    state_machine_source = inspect.getsource(main._run_batch_state_machine)
    assert "_prepare_batch" in state_machine_source
    assert "_attempt_batch" in state_machine_source
    assert "call_gemini_requests" not in state_machine_source
    assert "safe_json_loads" not in state_machine_source
    assert "decide_batch_action" in inspect.getsource(main._handle_batch_error)


def test_batch_error_classifier_covers_retry_actions_without_api_calls():
    import requests

    from translation_tool.core.lm_translator_main import _classify_batch_error

    assert _classify_batch_error(requests.Timeout()) == "timeout"
    assert _classify_batch_error(RuntimeError("x"), 400) == "invalid_argument"
    assert _classify_batch_error(RuntimeError("x"), 403) == "key_forbidden"
    assert _classify_batch_error(RuntimeError("x"), 404) == "model_missing"
    assert _classify_batch_error(RuntimeError("x"), 429) == "rate_limited"
    assert _classify_batch_error(RuntimeError("x"), 500) == "server_error"
    assert _classify_batch_error(RuntimeError("x"), 503) == "service_unavailable"
    assert _classify_batch_error(RuntimeError("x"), 504) == "deadline_exceeded"
    assert _classify_batch_error(RuntimeError("x"), 418) == "unknown"

    response = requests.Response()
    response.status_code = 429
    error = requests.HTTPError(response=response)
    assert _classify_batch_error(error) == "rate_limited"


class TestTranslateBatchSmart:
    """translate_batch_smart 測試"""

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_config_rules._get_all_keys")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.interruptible_sleep")
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
        mock_get_key.return_value = ["test_key"]
        mock_call_api.return_value = '{"items": [{"id": "0", "value": "你好"}]}'
        mock_json_loads.return_value = {"items": [{"id": "0", "value": "你好"}]}

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        _result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
        # 成功時 API 應該被調用一次
        mock_call_api.assert_called_once()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_config_rules._get_all_keys")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.interruptible_sleep")
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
        mock_get_key.return_value = ["test_key"]

        result, status = translate_batch_smart([], 0)

        assert result == []
        assert status == "AUTO"
        mock_call_api.assert_not_called()

    def test_translate_batch_smart_requires_key_before_execution(self, monkeypatch):
        from translation_tool.core import lm_config_rules, lm_translator_main

        monkeypatch.setattr(lm_config_rules, "_get_all_keys", list)
        execute_translation = MagicMock()
        monkeypatch.setattr(
            lm_translator_main, "_execute_translation", execute_translation
        )
        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        with pytest.raises(RuntimeError, match="沒有找到任何 API Key"):
            lm_translator_main.translate_batch_smart(items, total=1)

        execute_translation.assert_not_called()

    def test_translate_batch_smart_dry_run_does_not_require_key(self, monkeypatch):
        from translation_tool.core import lm_config_rules, lm_translator_main

        monkeypatch.setattr(lm_config_rules, "_get_all_keys", list)
        execute_translation = MagicMock(return_value=([], "AUTO"))
        monkeypatch.setattr(
            lm_translator_main, "_execute_translation", execute_translation
        )
        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        assert lm_translator_main.translate_batch_smart(
            items, total=1, dry_run=True
        ) == ([], "AUTO")
        execute_translation.assert_called_once()
        assert execute_translation.call_args.args[1:] == (1, True)

    def test_translate_batch_smart_empty_batch_does_not_require_key(self, monkeypatch):
        from translation_tool.core import lm_config_rules, lm_translator_main

        monkeypatch.setattr(lm_config_rules, "_get_all_keys", list)
        execute_translation = MagicMock()
        monkeypatch.setattr(
            lm_translator_main, "_execute_translation", execute_translation
        )

        assert lm_translator_main.translate_batch_smart([]) == ([], "AUTO")
        execute_translation.assert_not_called()

    @patch("translation_tool.core.lm_translator_main.safe_json_loads")
    @patch("translation_tool.core.lm_translator_main.load_config")
    @patch("translation_tool.core.lm_config_rules._get_all_keys")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.interruptible_sleep")
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
        mock_get_key.return_value = ["test_key"]
        mock_call_api.return_value = ""  # 空回應觸發重試

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]
        _result, status = translate_batch_smart(items, 1)

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
    @patch("translation_tool.core.lm_config_rules._get_all_keys")
    @patch("translation_tool.core.lm_translator_main.interruptible_sleep")
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
        mock_get_key.return_value = ["test_key"]

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        _result, _status = translate_batch_smart(items, 1)

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
    @patch("translation_tool.core.lm_config_rules._get_all_keys")
    @patch("translation_tool.core.lm_translator_main.interruptible_sleep")
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
        mock_get_key.return_value = ["test_key"]

        items = [{"path": "test.key", "text": "Hello", "cache_type": "lang"}]

        _result, _status = translate_batch_smart(items, 1)

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
    @patch("translation_tool.core.lm_config_rules._get_all_keys")
    @patch("translation_tool.core.lm_translator_main.call_gemini_requests")
    @patch("translation_tool.core.lm_translator_main.interruptible_sleep")
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
        mock_get_key.return_value = ["test_key"]
        mock_call_api.return_value = '{"items": []}'
        mock_json_loads.return_value = {"items": []}

        items = [
            {"path": f"key.{i}", "text": f"text{i}", "cache_type": "lang"}
            for i in range(10)
        ]
        _result, status = translate_batch_smart(items, 1)

        assert status in ["AUTO", "PARTIAL", "FAILED"]
