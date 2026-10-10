"""lm_api_client.py 單元測試。

用途：測試 LM API 用戶端相關功能。
"""

from unittest.mock import Mock, patch

import pytest
import requests


class TestCallGeminiRequests:
    """測試 call_gemini_requests 函數。"""

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_successful_call(self, mock_config, mock_post):
        """測試成功呼叫。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        # Mock 配置
        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 60}}}

        # Mock 回應
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": '{"translated": "value"}'}]}}
            ]
        }
        mock_post.return_value = mock_response

        result = call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="You are a translator",
            payload={"items": [{"id": "0", "value": "value"}]},
            api_key="test_api_key",
            temperature=0.7,
        )

        assert result == '{"translated": "value"}'

    @patch("translation_tool.core.openai_codex_client.call_chatgpt_responses")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_chatgpt_provider_dispatches_with_reasoning_effort(
        self, mock_config, mock_chatgpt
    ):
        from translation_tool.core.lm_api_client import call_gemini_requests

        # A UI setting change after a batch starts must not redirect its request.
        mock_config.return_value = {
            "lm_translator": {"provider": "gemini", "rate_limit": {"timeout": 5}}
        }
        mock_chatgpt.return_value = '{"items": []}'
        meta = {}
        payload = {"items": [{"id": "line-1", "value": "Hello"}]}

        result = call_gemini_requests(
            model_name="gpt-5-codex",
            system_prompt="Translate.",
            payload=payload,
            api_key="unused-for-oauth",
            temperature=0.2,
            meta_out=meta,
            reasoning_effort="high",
            profile_id="profile-a",
            provider="chatgpt",
            lm_config={"provider": "chatgpt", "rate_limit": {"timeout": 45}},
        )

        assert result == '{"items": []}'
        mock_chatgpt.assert_called_once_with(
            model_name="gpt-5-codex",
            system_prompt="Translate.",
            payload=payload,
            timeout=45,
            meta_out=meta,
            reasoning_effort="high",
            profile_id="profile-a",
        )

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_http_error(self, mock_config, mock_post):
        """測試 HTTP 錯誤。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 60}}}

        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            call_gemini_requests(
                model_name="gemini-pro",
                system_prompt="You are a translator",
                payload={"items": [{"id": "0", "value": "value"}]},
                api_key="test_api_key",
                temperature=0.7,
            )

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_invalid_response_format(self, mock_config, mock_post):
        """測試無效的回應格式。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 60}}}

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {}  # 缺少 candidates
        mock_post.return_value = mock_response

        with pytest.raises(RuntimeError):
            call_gemini_requests(
                model_name="gemini-pro",
                system_prompt="You are a translator",
                payload={"items": [{"id": "0", "value": "value"}]},
                api_key="test_api_key",
                temperature=0.7,
            )

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_custom_timeout(self, mock_config, mock_post):
        """測試自定義超時。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 120}}}

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"result": "ok"}'}]}}]
        }
        mock_post.return_value = mock_response

        call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="test",
            payload={"items": [{"id": "0", "value": "value"}]},
            api_key="test_key",
            temperature=0.5,
        )

        # 驗證 post 被調用
        mock_post.assert_called_once()
        # 驗證 timeout 參數被傳遞
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("timeout") == (30, 120)

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_short_timeout_bounds_connect_and_read_separately(
        self, mock_config, mock_post
    ):
        """短 timeout 設定同時套用於連線與讀取階段。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 5}}}
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"result":"ok"}'}]}}]
        }
        mock_post.return_value = response

        call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="test",
            payload={"items": [{"id": "0", "value": "value"}]},
            api_key="test_key",
            temperature=0.5,
        )

        assert mock_post.call_args.kwargs["timeout"] == (5, 5)

    @patch("translation_tool.core.lm_api_client.requests.post")
    @patch("translation_tool.core.lm_api_client.load_config")
    def test_api_key_not_in_url(self, mock_config, mock_post):
        """測試 API Key 不出現在 URL 中，而是放在 x-goog-api-key header。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        # 使用假的 API key（長度 35-45 字，以 AIza 開頭）
        fake_api_key = "AIza" + "a" * 37  # 共 41 字

        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 60}}}

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"result": "ok"}'}]}}]
        }
        mock_post.return_value = mock_response

        call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="test prompt",
            payload={"items": [{"id": "0", "value": "value"}]},
            api_key=fake_api_key,
            temperature=0.7,
        )

        # 驗證 URL 中不包含 API key
        call_args = mock_post.call_args
        called_url = (
            call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        )
        assert fake_api_key not in called_url, "API key 不應出現在 URL 中"

        # 驗證 x-goog-api-key header 存在（對照 Google 官方 REST 範例）
        headers = call_args.kwargs.get("headers", {})
        assert "x-goog-api-key" in headers, "x-goog-api-key header 必須存在"
        assert headers["x-goog-api-key"] == fake_api_key
        assert "Authorization" not in headers, "不應再使用 Authorization: Bearer"

        # 確保 URL 中沒有 key=... 之類的 query string
        assert "?" not in called_url or "key=" not in called_url, (
            "URL 中不應包含 key query parameter"
        )


class TestModuleImports:
    """測試模組導入。"""

    def test_imports(self):
        """測試必要導入。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        assert callable(call_gemini_requests)


def test_worst_case_request_sec_covers_every_connection_attempt_and_backoff():
    from translation_tool.core import lm_api_client as client

    timeout = 100.0
    worst = client.worst_case_request_sec(timeout)

    # 每次連線嘗試都可能套用完整逾時，再加上嘗試之間的指數退避（含最大 jitter）
    assert worst >= client.NETWORK_RETRY_ATTEMPTS * timeout
    backoff = worst - client.NETWORK_RETRY_ATTEMPTS * timeout
    expected = sum(
        client.NETWORK_RETRY_BASE_SEC * (2 ** (n - 1)) + client.NETWORK_RETRY_BASE_SEC
        for n in range(1, client.NETWORK_RETRY_ATTEMPTS)
    )
    assert backoff == expected
    assert client.worst_case_request_sec(-5) == expected  # 負的逾時不產生負的租約


def test_malformed_gemini_envelope_raises_a_dedicated_runtime_error_subclass():
    """HTTP 已成功但回應格式異常：例外是 RuntimeError 的子類（既有行為不變），呼叫端可據此分辨。"""
    from unittest.mock import Mock, patch

    import pytest

    from translation_tool.core import lm_api_client as client

    response = Mock(ok=True)
    response.json.return_value = {"promptFeedback": {"blockReason": "SAFETY"}}

    with (
        patch.object(
            client,
            "load_config",
            return_value={"lm_translator": {"provider": "gemini"}},
        ),
        patch.object(client, "_post_with_retry", return_value=response),
        pytest.raises(client.GeminiResponseFormatError) as info,
    ):
        client.call_gemini_requests(
            model_name="m",
            system_prompt="s",
            payload={"items": [{"id": "0", "value": "x"}]},
            api_key="k",
            temperature=0.2,
        )

    assert isinstance(info.value, RuntimeError)


def test_http_200_with_an_invalid_json_body_raises_the_format_error():
    """HTTP 200 但 body 不是有效 JSON：一樣是「HTTP 已成功、回應格式異常」。"""
    from unittest.mock import Mock, patch

    import pytest

    from translation_tool.core import lm_api_client as client

    response = Mock(ok=True)
    response.json.side_effect = ValueError("Expecting value")

    with (
        patch.object(
            client,
            "load_config",
            return_value={"lm_translator": {"provider": "gemini"}},
        ),
        patch.object(client, "_post_with_retry", return_value=response),
        pytest.raises(client.GeminiResponseFormatError),
    ):
        client.call_gemini_requests(
            model_name="m",
            system_prompt="s",
            payload={"items": [{"id": "0", "value": "x"}]},
            api_key="k",
            temperature=0.2,
            meta_out={},
        )


def test_http_200_with_an_unexpected_json_type_raises_the_format_error():
    """HTTP 200 且 JSON 合法、但最外層不是 object：extract_response_meta 也不能漏出別種例外。"""
    from unittest.mock import Mock, patch

    import pytest

    from translation_tool.core import lm_api_client as client

    response = Mock(ok=True)
    response.json.return_value = ["not", "an", "object"]

    with (
        patch.object(
            client,
            "load_config",
            return_value={"lm_translator": {"provider": "gemini"}},
        ),
        patch.object(client, "_post_with_retry", return_value=response),
        pytest.raises(client.GeminiResponseFormatError),
    ):
        client.call_gemini_requests(
            model_name="m",
            system_prompt="s",
            payload={"items": [{"id": "0", "value": "x"}]},
            api_key="k",
            temperature=0.2,
            meta_out={},
        )
