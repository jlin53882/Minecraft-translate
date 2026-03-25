"""lm_api_client.py 單元測試。

用途：測試 LM API 用戶端相關功能。
"""
import pytest
from unittest.mock import patch, Mock
import requests


class TestCallGeminiRequests:
    """測試 call_gemini_requests 函數。"""

    @patch('translation_tool.core.lm_api_client.requests.post')
    @patch('translation_tool.core.lm_api_client.load_config')
    def test_successful_call(self, mock_config, mock_post):
        """測試成功呼叫。"""
        from translation_tool.core.lm_api_client import call_gemini_requests
        
        # Mock 配置
        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 60}}}
        
        # Mock 回應
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": '{"translated": "value"}'}]
                }
            }]
        }
        mock_post.return_value = mock_response
        
        result = call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="You are a translator",
            payload={"key": "value"},
            api_key="test_api_key",
            temperature=0.7,
        )
        
        assert result == '{"translated": "value"}'

    @patch('translation_tool.core.lm_api_client.requests.post')
    @patch('translation_tool.core.lm_api_client.load_config')
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
                payload={"key": "value"},
                api_key="test_api_key",
                temperature=0.7,
            )

    @patch('translation_tool.core.lm_api_client.requests.post')
    @patch('translation_tool.core.lm_api_client.load_config')
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
                payload={"key": "value"},
                api_key="test_api_key",
                temperature=0.7,
            )

    @patch('translation_tool.core.lm_api_client.requests.post')
    @patch('translation_tool.core.lm_api_client.load_config')
    def test_custom_timeout(self, mock_config, mock_post):
        """測試自定義超時。"""
        from translation_tool.core.lm_api_client import call_gemini_requests
        
        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 120}}}
        
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": '{"result": "ok"}'}]
                }
            }]
        }
        mock_post.return_value = mock_response
        
        result = call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="test",
            payload={},
            api_key="test_key",
            temperature=0.5,
        )
        
        # 驗證 post 被調用
        mock_post.assert_called_once()
        # 驗證 timeout 參數被傳遞
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get('timeout') == 120


    @patch('translation_tool.core.lm_api_client.requests.post')
    @patch('translation_tool.core.lm_api_client.load_config')
    def test_api_key_not_in_url(self, mock_config, mock_post):
        """測試 API Key 不出現在 URL 中，而是放在 Authorization: Bearer header。"""
        from translation_tool.core.lm_api_client import call_gemini_requests

        # 使用假的 API key（長度 35-45 字，以 AIza 開頭）
        fake_api_key = "AIza" + "a" * 37  # 共 41 字

        mock_config.return_value = {"lm_translator": {"rate_limit": {"timeout": 60}}}

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": '{"result": "ok"}'}]
                }
            }]
        }
        mock_post.return_value = mock_response

        call_gemini_requests(
            model_name="gemini-pro",
            system_prompt="test prompt",
            payload={"key": "value"},
            api_key=fake_api_key,
            temperature=0.7,
        )

        # 驗證 URL 中不包含 API key
        call_args = mock_post.call_args
        called_url = call_args.args[0] if call_args.args else call_args.kwargs.get('url', '')
        assert fake_api_key not in called_url, "API key 不應出現在 URL 中"

        # 驗證 Authorization: Bearer header 存在
        headers = call_args.kwargs.get('headers', {})
        assert 'Authorization' in headers, "Authorization header 必須存在"
        assert headers['Authorization'] == f"Bearer {fake_api_key}", \
            "Authorization header 應為 Bearer {api_key} 格式"

        # 確保 URL 中沒有 key=... 之類的 query string
        assert '?' not in called_url or 'key=' not in called_url, \
            "URL 中不應包含 key query parameter"


class TestModuleImports:
    """測試模組導入。"""

    def test_imports(self):
        """測試必要導入。"""
        from translation_tool.core.lm_api_client import call_gemini_requests
        assert callable(call_gemini_requests)
