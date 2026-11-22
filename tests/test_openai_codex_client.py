"""OpenAI Codex client 請求參數與錯誤處理的針對性測試。"""

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
import requests

from translation_tool.core import openai_codex_client as client
from translation_tool.core.codex_cli_auth import CodexCLICredentials


@pytest.fixture(autouse=True)
def _isolated_fallback_state():
    """備援沿用狀態是程序內共用的；每個測試都從「沒有任務、沒有沿用」開始。"""
    from translation_tool.utils import ui_mirror

    client._sticky_tasks.clear()
    with ui_mirror.task_scope(None):
        yield
    client._sticky_tasks.clear()


class _FakeStreamResponse:
    def __init__(self, events, *, status_code=200, headers=None):
        self.events = events
        self.status_code = status_code
        self.ok = status_code < 400
        self.headers = headers or {}
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for event in self.events:
            yield f"data: {json.dumps(event)}".encode()
            yield b""

    def close(self):
        self.closed = True


def test_stream_request_uses_responses_structured_outputs(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return requests.Response()

    monkeypatch.setattr(client.requests, "post", post)
    payload = {"items": [{"id": "line-1", "value": "Hello"}]}

    client._stream_request(
        token="access-token",
        model_name="gpt-5-codex",
        system_prompt="Translate the items and return JSON.",
        payload=payload,
        timeout=30,
    )

    body = captured["json"]
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer access-token"
    assert body["stream"] is True
    assert body["store"] is False
    assert "max_output_tokens" not in body
    assert "service_tier" not in body
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    schema = body["text"]["format"]["schema"]
    assert schema["required"] == ["items"]
    assert schema["additionalProperties"] is False
    item_schema = schema["properties"]["items"]["items"]
    assert item_schema["required"] == ["id", "value"]
    assert item_schema["additionalProperties"] is False
    assert item_schema["properties"]["id"]["enum"] == ["line-1"]


def test_stream_request_accepts_a_custom_smart_review_schema(monkeypatch):
    """驗證串流請求接受自訂智慧校對結構。"""
    captured = {}
    monkeypatch.setattr(
        client.requests,
        "post",
        lambda _url, **kwargs: captured.update(kwargs) or requests.Response(),
    )
    custom = {
        "type": "object",
        "properties": {"items": {"type": "array"}},
        "required": ["items"],
        "additionalProperties": False,
    }

    client._stream_request(
        token="access-token",
        model_name="gpt-5.6-luna",
        system_prompt="Review.",
        payload={"items": [{"id": "1"}]},
        timeout=30,
        response_schema=custom,
    )

    assert captured["json"]["text"]["format"]["schema"] == custom


def test_stream_request_sends_output_cap_and_fast_service_tier(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        client.requests,
        "post",
        lambda _url, **kwargs: captured.update(kwargs) or requests.Response(),
    )

    client._stream_request(
        token="access-token",
        model_name="gpt-5-codex",
        system_prompt="Translate.",
        payload={"items": [{"id": "line-1", "value": "Hello"}]},
        timeout=30,
        max_output_tokens=1234,
        service_tier="fast",
    )

    assert captured["json"]["max_output_tokens"] == 1234
    assert captured["json"]["service_tier"] == "fast"


def test_stream_request_omits_responses_output_caps_below_api_minimum(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        client.requests,
        "post",
        lambda _url, **kwargs: captured.update(kwargs) or requests.Response(),
    )

    client._stream_request(
        token="access-token",
        model_name="gpt-5-codex",
        system_prompt="Translate.",
        payload={"items": [{"id": "line-1", "value": "Hello"}]},
        timeout=30,
        max_output_tokens=8,
    )

    assert "max_output_tokens" not in captured["json"]


def test_stream_request_sends_configured_reasoning_effort(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        client.requests,
        "post",
        lambda _url, **kwargs: captured.update(kwargs) or requests.Response(),
    )

    client._stream_request(
        token="access-token",
        model_name="gpt-5-codex",
        system_prompt="Translate.",
        payload={"items": [{"id": "line-1", "value": "Hello"}]},
        timeout=30,
        reasoning_effort="high",
    )

    assert captured["json"]["reasoning"] == {"effort": "high"}


def test_stream_request_preserves_conversation_history_for_retry(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        client.requests,
        "post",
        lambda _url, **kwargs: captured.update(kwargs) or requests.Response(),
    )

    client._stream_request(
        token="access-token",
        model_name="gpt-5-codex",
        system_prompt="Translate.",
        payload={"items": [{"id": "0", "value": "修復原文"}]},
        timeout=30,
        conversation_history=[
            {"role": "user", "text": "原始 request"},
            {"role": "model", "text": "第一次 response"},
        ],
    )

    assert captured["json"]["input"] == [
        {"role": "user", "content": "原始 request"},
        {"role": "assistant", "content": "第一次 response"},
        {
            "role": "user",
            "content": '{"items": [{"id": "0", "value": "修復原文"}]}',
        },
    ]


def test_call_retries_once_after_unauthorized_and_keeps_reasoning(monkeypatch):
    """驗證未授權後只重試一次並保留推理設定。"""

    class _UnauthorizedResponse:
        status_code = 401

        def close(self):
            self.closed = True

    class _SuccessResponse:
        """測試用的成功 Responses API 回應物件。"""

        status_code = 200
        ok = True

    tokens = iter(("expired-token", "refreshed-token"))
    token_requests = []

    def get_token(**kwargs):
        token_requests.append(kwargs)
        return next(tokens)

    monkeypatch.setattr(client, "get_chatgpt_access_token", get_token)
    requests_seen = []

    def stream_request(**kwargs):
        requests_seen.append(kwargs)
        return (
            _UnauthorizedResponse() if len(requests_seen) == 1 else _SuccessResponse()
        )

    monkeypatch.setattr(client, "_stream_request", stream_request)
    monkeypatch.setattr(
        client, "_consume_stream", lambda _response, _meta, _guard=None: "done"
    )

    assert (
        client.call_chatgpt_responses(
            model_name="gpt-5-codex",
            system_prompt="Translate.",
            payload={"items": []},
            timeout=30,
            reasoning_effort="medium",
            profile_id="profile-a",
        )
        == "done"
    )
    assert [request["token"] for request in requests_seen] == [
        "expired-token",
        "refreshed-token",
    ]
    assert all(request["reasoning_effort"] == "medium" for request in requests_seen)
    assert all("service_tier" not in request for request in requests_seen)
    assert token_requests == [
        {"profile_id": "profile-a"},
        {"force_refresh": True, "profile_id": "profile-a"},
    ]


def test_call_fast_mode_uses_codex_priority_service_tier(monkeypatch):
    """驗證快速模式使用 Codex 優先服務層級。"""

    class _SuccessResponse:
        """提供測試所需的成功 HTTP response 欄位。"""

        status_code = 200
        ok = True

    requests_seen = []
    monkeypatch.setattr(
        client, "get_chatgpt_access_token", lambda **_kwargs: "access-token"
    )

    def stream_request(**kwargs):
        """記錄測試用的 Responses 請求參數並回傳成功 response。"""
        requests_seen.append(kwargs)
        return _SuccessResponse()

    monkeypatch.setattr(client, "_stream_request", stream_request)
    monkeypatch.setattr(
        client, "_consume_stream", lambda _response, _meta, _guard=None: "done"
    )

    assert (
        client.call_chatgpt_responses(
            model_name="gpt-5.6-luna",
            system_prompt="Translate.",
            payload={"items": []},
            timeout=30,
            fast=True,
        )
        == "done"
    )
    assert len(requests_seen) == 1
    assert requests_seen[0]["service_tier"] == "priority"


def test_consume_stream_returns_structured_output_and_usage():
    response = _FakeStreamResponse(
        [
            {"type": "response.output_text.delta", "delta": '{"items": []}'},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 8,
                        "output_tokens": 5,
                        "output_tokens_details": {"reasoning_tokens": 2},
                        "total_tokens": 13,
                    },
                    "service_tier": "priority",
                },
            },
        ]
    )
    meta = {}

    result = client._consume_stream(response, meta)

    assert result == '{"items": []}'
    assert meta == {
        "finish_reason": "STOP",
        "prompt_tokens": 8,
        "candidates_tokens": 3,
        "thoughts_tokens": 2,
        "total_tokens": 13,
        "service_tier": "priority",
    }
    assert response.closed is True


def test_consume_stream_detects_structured_output_refusal():
    response = _FakeStreamResponse(
        [
            {
                "type": "response.refusal.done",
                "refusal": "This source text cannot be translated.",
            },
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "refusal",
                                    "refusal": "This source text cannot be translated.",
                                }
                            ],
                        }
                    ],
                },
            },
        ]
    )

    with pytest.raises(client.ChatGPTAPIError, match="refusal") as exc_info:
        client._consume_stream(response, None)

    assert "cannot be translated" in str(exc_info.value)
    assert response.closed is True
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    with pytest.raises(RuntimeError, match="拒絕處理"):
        main._handle_batch_error(
            SimpleNamespace(chatgpt_retry_count=0), exc_info.value, 0
        )


def test_response_incomplete_keeps_shrink_batch_action(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    response = _FakeStreamResponse(
        [
            {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            }
        ]
    )
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    assert exc_info.value.code == "incomplete"
    assert (
        main._handle_batch_error(
            SimpleNamespace(chatgpt_retry_count=0), exc_info.value, 0
        )
        is main.BatchAction.SHRINK_BATCH
    )
    assert response.closed is True
    assert waits == []


@pytest.mark.parametrize(
    "event_payload",
    [
        {
            "type": "error",
            "error": {
                "code": "stream_backend_error",
                "status": "not-a-status",
            },
        },
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "stream_backend_error",
                    "status": "999999999999999999999999",
                }
            },
        },
    ],
)
def test_malformed_sse_status_is_treated_as_unknown(event_payload):
    response = _FakeStreamResponse([event_payload])

    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    assert exc_info.value.status is None
    assert response.closed is True


def test_malformed_incomplete_details_do_not_crash_stream_parser():
    response = _FakeStreamResponse(
        [
            {
                "type": "response.incomplete",
                "response": {"incomplete_details": ["unexpected"]},
            }
        ]
    )

    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    assert exc_info.value.code == "incomplete"
    assert response.closed is True


@pytest.mark.parametrize(
    ("event", "code"),
    [
        ("response.failed", "subscription_sharing_usage_unavailable"),
        ("response.failed", "subscription_sharing_user_unavailable"),
        ("error", "subscription_sharing_usage_unavailable"),
        ("error", "subscription_sharing_user_unavailable"),
    ],
)
def test_sse_temporary_usage_errors_reach_bounded_batch_retry(monkeypatch, event, code):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    error_data = {
        "code": code,
        "message": "Usage availability is temporarily unavailable.",
    }
    if event == "response.failed":
        event_payload = {
            "type": event,
            "response": {"id": "resp_test", "status": "failed", "error": error_data},
        }
    else:
        event_payload = {
            "type": event,
            "code": code,
            "message": error_data["message"],
            "param": None,
            "sequence_number": 1,
        }
    response = _FakeStreamResponse([event_payload])
    response.headers["x-request-id"] = "req_sse_retry"
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)

    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    error = exc_info.value
    assert error.code == code
    assert error.status is None
    assert error.error_type == ""
    assert error.request_id == "req_sse_retry"
    assert response.closed is True
    runtime = SimpleNamespace(chatgpt_retry_count=0)
    assert main._handle_batch_error(runtime, error, 0) is (
        main.BatchAction.RETRY_SAME_MODEL
    )
    assert runtime.chatgpt_retry_count == 1
    assert waits == [1]


def test_sse_rate_limit_code_is_classified_as_http_429():
    response = _FakeStreamResponse(
        [
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Too many requests",
                    },
                },
            }
        ]
    )

    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    assert exc_info.value.status == 429


@pytest.mark.parametrize(
    "code",
    [
        "subscription_sharing_usage_limit_exceeded",
        "insufficient_quota",
        "credit_balance_exhausted",
    ],
)
def test_sse_permanent_quota_errors_never_retry(monkeypatch, code):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    response = _FakeStreamResponse(
        [
            {
                "type": "response.failed",
                "response": {
                    "id": "resp_quota",
                    "status": "failed",
                    "error": {"code": code, "message": "Quota exhausted."},
                },
            }
        ]
    )
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    meta = {}
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, meta)

    runtime = SimpleNamespace(chatgpt_retry_count=0)
    with pytest.raises(RuntimeError):
        main._handle_batch_error(runtime, exc_info.value, 0)
    assert exc_info.value.status is None
    assert exc_info.value.code == code
    assert runtime.chatgpt_retry_count == 0
    assert waits == []
    assert response.closed is True
    assert meta == {}


def test_unknown_sse_failure_does_not_retry_based_on_message_text(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    response = _FakeStreamResponse(
        [
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "unrecognized_backend_failure",
                        "message": "subscription_sharing_usage_unavailable",
                    },
                },
            }
        ]
    )
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    with pytest.raises(RuntimeError):
        main._handle_batch_error(
            SimpleNamespace(chatgpt_retry_count=0), exc_info.value, 0
        )
    assert response.closed is True
    assert waits == []


def test_sse_error_with_null_code_stays_unknown_and_does_not_retry(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    response = _FakeStreamResponse(
        [
            {
                "type": "error",
                "code": None,
                "message": "The stream failed without a machine-readable code.",
                "param": None,
                "sequence_number": 1,
            }
        ],
        headers={"x-request-id": "req_unknown_error"},
    )
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    assert exc_info.value.code == "stream_error"
    assert exc_info.value.error_type == ""
    assert exc_info.value.request_id == "req_unknown_error"
    with pytest.raises(RuntimeError):
        main._handle_batch_error(
            SimpleNamespace(chatgpt_retry_count=0), exc_info.value, 0
        )
    assert response.closed is True
    assert waits == []


def test_sse_partial_delta_is_discarded_and_response_is_closed(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    response = _FakeStreamResponse(
        [
            {"type": "response.output_text.delta", "delta": '{"items":[{"id":"0",'},
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "subscription_sharing_usage_unavailable",
                        "message": "Usage is temporarily unavailable.",
                    },
                },
            },
        ]
    )
    meta = {}
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, meta)

    assert response.closed is True
    assert meta == {}
    assert (
        main._handle_batch_error(
            SimpleNamespace(chatgpt_retry_count=0), exc_info.value, 0
        )
        is main.BatchAction.RETRY_SAME_MODEL
    )
    assert waits == [1]


def test_sse_temporary_failure_retry_is_bounded(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    runtime = SimpleNamespace(chatgpt_retry_count=0)
    responses = []
    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    for _ in range(main.CHATGPT_MAX_RETRIES):
        response = _FakeStreamResponse(
            [
                {
                    "type": "response.failed",
                    "response": {
                        "status": "failed",
                        "error": {
                            "code": "subscription_sharing_user_unavailable",
                            "message": "Usage is temporarily unavailable.",
                        },
                    },
                }
            ]
        )
        responses.append(response)
        with pytest.raises(client.ChatGPTAPIError) as exc_info:
            client._consume_stream(response, None)
        assert main._handle_batch_error(runtime, exc_info.value, 0) is (
            main.BatchAction.RETRY_SAME_MODEL
        )

    final_response = _FakeStreamResponse(
        [
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "subscription_sharing_user_unavailable",
                        "message": "Usage is temporarily unavailable.",
                    },
                },
            }
        ]
    )
    responses.append(final_response)
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(final_response, None)
    with pytest.raises(RuntimeError, match="重試上限"):
        main._handle_batch_error(runtime, exc_info.value, 0)

    assert runtime.chatgpt_retry_count == main.CHATGPT_MAX_RETRIES
    assert len(waits) == main.CHATGPT_MAX_RETRIES
    assert all(response.closed for response in responses)


def test_sse_retry_wait_cancellation_bubbles_out(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main
    from translation_tool.utils.cancellation import TaskCancelled

    response = _FakeStreamResponse(
        [
            {
                "type": "error",
                "code": "subscription_sharing_user_unavailable",
                "message": "Usage is temporarily unavailable.",
                "param": None,
                "sequence_number": 1,
            }
        ]
    )
    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None)

    def cancel_wait(_seconds):
        raise TaskCancelled("cancelled during SSE retry wait")

    monkeypatch.setattr(main, "interruptible_sleep", cancel_wait)
    with pytest.raises(TaskCancelled):
        main._handle_batch_error(
            SimpleNamespace(chatgpt_retry_count=0), exc_info.value, 0
        )
    assert response.closed is True


def test_sse_retry_wait_cancellation_stops_the_batch_before_another_request(
    monkeypatch,
):
    from translation_tool.core import lm_translator_main as main
    from translation_tool.utils.cancellation import TaskCancelled

    response = _FakeStreamResponse(
        [
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "subscription_sharing_usage_unavailable",
                        "message": "Usage is temporarily unavailable.",
                    },
                },
            }
        ]
    )
    request_calls = []
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {
            "lm_translator": {
                "provider": "chatgpt",
                "chatgpt_model": "gpt-sse-cancel-test",
                "chatgpt_model_profile_id": "profile-a",
                "max_input_token_budget": 60000,
                "initial_batch_size_lang": 1,
                "retry_same_as_source": False,
                "rpm_cooldown_sec": 0,
                "request_interval_sec": 0,
            }
        },
    )
    monkeypatch.setattr(
        main, "chatgpt_account_status", lambda: {"active_profile_id": "profile-a"}
    )

    def call_chatgpt(**kwargs):
        request_calls.append(kwargs)
        return client._consume_stream(response, kwargs["meta_out"])

    def cancel_wait(_seconds):
        raise TaskCancelled("cancelled during SSE retry wait")

    monkeypatch.setattr(main, "call_gemini_requests", call_chatgpt)
    monkeypatch.setattr(main, "interruptible_sleep", cancel_wait)
    items = [
        {
            "id": "line-1",
            "text": "Hello",
            "file": "lang/en_us.json",
            "path": "lang/en_us.json",
        }
    ]
    runtime = main._build_batch_runtime(items, 1)
    assert runtime is not None
    round_data = main._prepare_batch(runtime)

    with pytest.raises(TaskCancelled):
        main._attempt_batch(runtime, round_data)

    assert len(request_calls) == 1
    assert response.closed is True
    assert runtime.all_results == []


def test_sse_retry_then_success_uses_same_batch_and_pinned_profile(monkeypatch):
    from translation_tool.core import lm_translator_main as main

    # 此測試驗證批次間節流等待，明確還原預設間隔（conftest 預設為 0）。
    monkeypatch.setattr(main, "_DEFAULT_BATCH_SLEEP", 4.0)

    active_profile = {"id": "profile-a"}
    token_requests = []
    posts = []
    responses = [
        _FakeStreamResponse(
            [
                {"type": "response.output_text.delta", "delta": "partial output"},
                {
                    "type": "response.failed",
                    "response": {
                        "status": "failed",
                        "error": {
                            "code": "subscription_sharing_usage_unavailable",
                            "message": "Usage is temporarily unavailable.",
                        },
                    },
                },
            ],
            headers={"x-request-id": "req_sse_failed"},
        ),
        _FakeStreamResponse([], status_code=401),
        _FakeStreamResponse(
            [
                {
                    "type": "response.output_text.delta",
                    "delta": '{"items":[{"id":"0","value":"Bonjour"}]}',
                },
                {
                    "type": "response.completed",
                    "response": {"status": "completed", "usage": {}},
                },
            ]
        ),
    ]
    stream_responses = list(responses)
    waits = []
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {
            "lm_translator": {
                "provider": "chatgpt",
                "chatgpt_model": "gpt-sse-retry-test",
                "chatgpt_model_profile_id": "profile-a",
                "max_input_token_budget": 60000,
                "initial_batch_size_lang": 1,
                "retry_same_as_source": False,
                "rpm_cooldown_sec": 0,
                "request_interval_sec": 0,
                "rate_limit": {"timeout": 30},
            }
        },
    )
    monkeypatch.setattr(
        main,
        "chatgpt_account_status",
        lambda: {"active_profile_id": active_profile["id"]},
    )
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(
        client,
        "get_chatgpt_access_token",
        lambda **kwargs: token_requests.append(kwargs) or "access-token",
    )

    def post(_url, **kwargs):
        posts.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(client.requests, "post", post)
    items = [
        {
            "id": "line-1",
            "text": "Hello",
            "file": "lang/en_us.json",
            "path": "lang/en_us.json",
        }
    ]
    runtime = main._build_batch_runtime(items, 1)
    assert runtime is not None
    round_data = main._prepare_batch(runtime)

    first = main._attempt_batch(runtime, round_data)
    assert first.action is main.BatchAction.RETRY_SAME_MODEL
    assert runtime.all_results == []
    assert runtime.chatgpt_retry_count == 1
    active_profile["id"] = "profile-b"

    second = main._attempt_batch(runtime, round_data)

    assert second.action is None
    assert runtime.all_results == [{**items[0], "text": "Bonjour"}]
    assert runtime.completed_calls == 1
    assert runtime.chatgpt_retry_count == 0
    assert token_requests == [
        {"profile_id": "profile-a"},
        {"profile_id": "profile-a"},
        {"force_refresh": True, "profile_id": "profile-a"},
    ]
    assert [call["json"]["input"] for call in posts] == [
        posts[0]["json"]["input"]
    ] * len(posts)
    assert waits[0] == pytest.approx(1)
    assert waits[1] == pytest.approx(4, abs=0.01)
    assert len(waits) == 2
    assert len(posts) == 3
    assert all(response.closed for response in stream_responses)


def test_http_error_preserves_status_type_param_and_request_id():
    """驗證 HTTP 錯誤保留狀態碼、錯誤類型、參數與請求識別碼。"""
    response = requests.Response()
    response.status_code = 403
    response.headers["x-request-id"] = "req_example"
    response._content = json.dumps(
        {
            "error": {
                "type": "permission_error",
                "code": "region_not_supported",
                "param": "model",
                "message": "This region is not supported.",
            }
        }
    ).encode()

    error = client._response_error(response)

    assert error.status == 403
    assert error.code == "region_not_supported"
    assert error.error_type == "permission_error"
    assert error.param == "model"
    assert error.request_id == "req_example"
    assert "HTTP 403" in str(error)
    assert "region_not_supported" in str(error)


def test_http_error_preserves_top_level_detail_from_direct_route():
    """驗證 HTTP 錯誤保留直接路由回傳的頂層詳細資訊。"""
    response = requests.Response()
    response.status_code = 400
    response.headers["x-request-id"] = "req_direct_route"
    response._content = json.dumps(
        {"detail": "Plan usage is not authorized for this request."}
    ).encode()

    error = client._response_error(response)

    assert error.status == 400
    assert error.code == "http_400"
    assert error.request_id == "req_direct_route"
    assert "Plan usage is not authorized for this request." in str(error)


def test_http_error_carries_retry_after_for_batch_backoff():
    response = requests.Response()
    response.status_code = 503
    response.headers["Retry-After"] = "2.5"
    response._content = json.dumps(
        {"error": {"type": "service_unavailable_error", "code": "server_is_overloaded"}}
    ).encode()

    error = client._response_error(response)

    assert error.retry_after == 2.5


@pytest.mark.parametrize(
    ("status", "body", "retry_after", "expected_code", "expected_wait", "retry"),
    [
        (429, b"", None, "http_429", 1, True),
        (429, b"not-json", "2", "http_429", 2, True),
        (429, b"{}", "not-a-date", "http_429", 1, True),
        (
            429,
            b'{"error":{"code":"rate_limit_exceeded","type":"rate_limit_error"}}',
            None,
            "rate_limit_exceeded",
            1,
            True,
        ),
        (
            503,
            b'{"error":{"code":"server_is_overloaded","type":"service_unavailable_error"}}',
            None,
            "server_is_overloaded",
            1,
            True,
        ),
        (
            429,
            b'{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}',
            "1",
            "subscription_sharing_usage_limit_exceeded",
            None,
            False,
        ),
        (
            400,
            b'{"error":{"code":"subscription_sharing_unsupported_capability"}}',
            None,
            "subscription_sharing_unsupported_capability",
            None,
            False,
        ),
        (
            403,
            b'{"error":{"code":"subscription_sharing_user_not_eligible"}}',
            None,
            "subscription_sharing_user_not_eligible",
            None,
            False,
        ),
    ],
)
def test_http_response_errors_follow_real_chatgpt_batch_retry_path(
    monkeypatch, status, body, retry_after, expected_code, expected_wait, retry
):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    response = requests.Response()
    response.status_code = status
    response.headers["x-request-id"] = "req_retry_path"
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    response._content = body

    error = client._response_error(response)
    assert error.status == status
    assert error.request_id == "req_retry_path"
    assert error.code == expected_code
    runtime = SimpleNamespace(chatgpt_retry_count=0)
    if retry:
        assert main._handle_batch_error(runtime, error, 0) is (
            main.BatchAction.RETRY_SAME_MODEL
        )
        assert runtime.chatgpt_retry_count == 1
        assert waits == [expected_wait]
    else:
        with pytest.raises(RuntimeError):
            main._handle_batch_error(runtime, error, 0)
        assert runtime.chatgpt_retry_count == 0
        assert waits == []


def test_http_date_retry_after_reaches_batch_backoff_without_sleeping(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0)
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = format_datetime(
        datetime.now(UTC) + timedelta(seconds=10), usegmt=True
    )
    response._content = b"{}"

    error = client._response_error(response)
    assert error.retry_after is not None
    assert main._handle_batch_error(
        SimpleNamespace(chatgpt_retry_count=0), error, 0
    ) is (main.BatchAction.RETRY_SAME_MODEL)
    assert 0 < waits[0] <= 10


def test_http_429_retry_after_over_cap_fails_without_sleeping(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.core import lm_translator_main as main

    waits = []
    monkeypatch.setattr(main, "interruptible_sleep", waits.append)
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = str(main.CHATGPT_MAX_RETRY_AFTER_SEC + 1)
    response._content = b""
    error = client._response_error(response)

    with pytest.raises(RuntimeError, match="等待"):
        main._handle_batch_error(SimpleNamespace(chatgpt_retry_count=0), error, 0)
    assert waits == []


def _completed_event():
    return {"type": "response.completed", "response": {"status": "completed"}}


def test_consume_stream_output_guard_aborts_runaway_output():
    # 模型重複輸出：ChatGPT 不送輸出上限，由本地保護中止並交給縮批重試。
    deltas = [{"type": "response.output_text.delta", "delta": "x" * 400}] * 10
    response = _FakeStreamResponse([*deltas, _completed_event()])

    with pytest.raises(client.ChatGPTAPIError) as exc_info:
        client._consume_stream(response, None, output_guard_tokens=100)

    assert exc_info.value.code == "incomplete"
    assert "local_output_guard" in str(exc_info.value)
    assert response.closed is True


def test_consume_stream_output_guard_allows_output_within_margin():
    # 估算是近似值：只超過上限一點（未超過寬限倍數）不中止。
    response = _FakeStreamResponse(
        [
            {"type": "response.output_text.delta", "delta": "x" * 480},
            _completed_event(),
        ]
    )

    assert client._consume_stream(response, None, output_guard_tokens=100) == (
        "x" * 480
    )


@pytest.mark.parametrize("guard", [None, 0, -1, True])
def test_consume_stream_without_output_guard_never_aborts(guard):
    response = _FakeStreamResponse(
        [
            {"type": "response.output_text.delta", "delta": "x" * 100_000},
            _completed_event(),
        ]
    )

    assert len(client._consume_stream(response, None, guard)) == 100_000


def test_call_passes_output_guard_without_sending_output_cap(monkeypatch):
    monkeypatch.setattr(
        client, "get_chatgpt_access_token", lambda **_kwargs: "access-token"
    )
    seen: dict = {}

    class _Ok:
        status_code = 200
        ok = True

    def stream_request(**kwargs):
        seen["request"] = kwargs
        return _Ok()

    def consume(_response, _meta, guard=None):
        seen["guard"] = guard
        return "done"

    monkeypatch.setattr(client, "_stream_request", stream_request)
    monkeypatch.setattr(client, "_consume_stream", consume)

    client.call_chatgpt_responses(
        model_name="gpt-5-codex",
        system_prompt="Translate.",
        payload={"items": []},
        timeout=30,
        output_guard_tokens=4096,
    )

    assert seen["request"]["max_output_tokens"] is None
    assert seen["guard"] == 4096


def test_codex_backend_request_omits_output_cap_and_identifies_the_app(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return requests.Response()

    monkeypatch.setattr(client.requests, "post", post)

    client._stream_request(
        token="codex-token",
        model_name="gpt-5-codex",
        system_prompt="Translate.",
        payload={"items": [{"id": "a", "value": "Hello"}]},
        timeout=30,
        max_output_tokens=100000,
        service_tier="priority",
        codex_account_id="acct-1",
    )

    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert captured["headers"]["Authorization"] == "Bearer codex-token"
    assert captured["headers"]["ChatGPT-Account-ID"] == "acct-1"
    assert captured["headers"]["originator"] == "minecraft-translate"
    assert "codex" not in captured["headers"]["User-Agent"].lower()
    assert "max_output_tokens" not in captured["json"]
    assert "service_tier" not in captured["json"]


def _call_kwargs():
    return {
        "model_name": "gpt-5-codex",
        "system_prompt": "Translate.",
        "payload": {"items": []},
        "timeout": 30,
    }


def _sharing_limit_error():
    return client.ChatGPTAPIError(
        "subscription_sharing_usage_limit_exceeded", "limit", status=200
    )


def test_sharing_limit_falls_back_to_codex_login(monkeypatch):
    def oauth_route(**_kwargs):
        raise _sharing_limit_error()

    seen = {}

    def codex_route(**kwargs):
        seen.update(kwargs)
        return "from-codex"

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )

    assert client.call_chatgpt_responses(**_call_kwargs()) == "from-codex"
    assert seen["model_name"] == "gpt-5-codex"


def test_codex_fallback_keeps_the_local_output_guard(monkeypatch):
    # 備援路線同樣不送輸出上限，本地輸出保護要跟著帶過去。
    def oauth_route(**_kwargs):
        raise _sharing_limit_error()

    seen = {}

    def codex_route(**kwargs):
        seen.update(kwargs)
        return "from-codex"

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )

    client.call_chatgpt_responses(**_call_kwargs(), output_guard_tokens=4096)

    assert seen["output_guard_tokens"] == 4096
    assert "max_output_tokens" not in seen


def test_other_errors_do_not_fall_back(monkeypatch):
    def oauth_route(**_kwargs):
        raise client.ChatGPTAPIError("insufficient_quota", "no", status=429)

    def codex_route(**_kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )

    with pytest.raises(client.ChatGPTAPIError) as caught:
        client.call_chatgpt_responses(**_call_kwargs())
    assert caught.value.code == "insufficient_quota"


def test_unusable_codex_login_surfaces_the_original_limit_error(monkeypatch):
    def oauth_route(**_kwargs):
        raise _sharing_limit_error()

    def codex_route(**_kwargs):
        raise client.CodexCLIAuthError("expired")

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )

    with pytest.raises(client.ChatGPTAPIError) as caught:
        client.call_chatgpt_responses(**_call_kwargs())
    assert caught.value.code == "subscription_sharing_usage_limit_exceeded"


def test_fallback_is_logged(monkeypatch):
    messages = []

    def oauth_route(**_kwargs):
        raise _sharing_limit_error()

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", lambda **_kw: "ok")
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )
    monkeypatch.setattr(client, "log_warning", messages.append)

    client.call_chatgpt_responses(**_call_kwargs())

    assert any("改用本機 Codex CLI 登入" in message for message in messages)


def _codex_route_kwargs(expected_email):
    return {
        "model_name": "gpt-5-codex",
        "system_prompt": "Translate.",
        "payload": {"items": []},
        "timeout": 30,
        "meta_out": None,
        "reasoning_effort": None,
        "conversation_history": None,
        "response_schema": None,
        "expected_email": expected_email,
    }


@pytest.mark.parametrize("expected", ["other@example.com", ""])
def test_codex_login_of_a_different_account_is_never_used(monkeypatch, expected):
    monkeypatch.setattr(
        client,
        "load_codex_cli_credentials",
        lambda: CodexCLICredentials("tok", "acct", "me@example.com"),
    )

    def post(*_args, **_kwargs):
        raise AssertionError("request must not be sent")

    monkeypatch.setattr(client.requests, "post", post)

    with pytest.raises(client.CodexCLIAuthError, match="不同或無法確認"):
        client._call_codex_login_route(**_codex_route_kwargs(expected))


def test_selected_account_email_follows_the_pinned_profile(monkeypatch):
    monkeypatch.setattr(
        client,
        "chatgpt_account_status",
        lambda: {
            "email": "Active@Example.com",
            "accounts": [
                {"profile_id": "p1", "email": "One@Example.com"},
                {"profile_id": "p2", "email": ""},
            ],
        },
    )

    assert client._selected_account_email(None) == "active@example.com"
    assert client._selected_account_email("p1") == "one@example.com"
    assert client._selected_account_email("p2") == ""
    assert client._selected_account_email("missing") == ""


def test_failed_codex_request_keeps_the_original_limit_error(monkeypatch):
    messages = []

    def oauth_route(**_kwargs):
        raise _sharing_limit_error()

    def codex_route(**_kwargs):
        raise client.ChatGPTAPIError("invalid_token", "revoked", status=401)

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )
    monkeypatch.setattr(client, "log_warning", messages.append)

    with pytest.raises(client.ChatGPTAPIError) as caught:
        client.call_chatgpt_responses(**_call_kwargs())

    assert caught.value.code == "subscription_sharing_usage_limit_exceeded"
    assert any("invalid_token" in message for message in messages)


@pytest.fixture
def sticky_env(monkeypatch):
    from translation_tool.utils import ui_mirror

    client._sticky_tasks.clear()
    calls = {"oauth": 0, "codex": 0}
    logs = {"info": [], "warning": []}

    def oauth_route(**_kwargs):
        calls["oauth"] += 1
        raise _sharing_limit_error()

    def codex_route(**_kwargs):
        calls["codex"] += 1
        return "from-codex"

    monkeypatch.setattr(client, "_call_oauth_route", oauth_route)
    monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
    monkeypatch.setattr(
        client, "_selected_account_email", lambda _pid: "me@example.com"
    )
    monkeypatch.setattr(client, "log_info", logs["info"].append)
    monkeypatch.setattr(client, "log_warning", logs["warning"].append)
    yield calls, logs, ui_mirror
    client._sticky_tasks.clear()


def test_successful_fallback_is_logged_and_kept_for_the_task(sticky_env):
    calls, logs, ui_mirror = sticky_env

    with ui_mirror.task_scope("task-1"):
        for _ in range(3):
            assert client.call_chatgpt_responses(**_call_kwargs()) == "from-codex"

    assert calls == {"oauth": 1, "codex": 3}
    assert sum("已改用本機 Codex CLI 登入完成請求" in m for m in logs["info"]) == 1


def test_a_new_task_checks_the_oauth_route_again(sticky_env):
    calls, _logs, ui_mirror = sticky_env

    with ui_mirror.task_scope("task-1"):
        client.call_chatgpt_responses(**_call_kwargs())
        client.call_chatgpt_responses(**_call_kwargs())
    with ui_mirror.task_scope("task-2"):
        client.call_chatgpt_responses(**_call_kwargs())

    assert calls["oauth"] == 2


def test_without_a_task_every_call_checks_the_oauth_route(sticky_env):
    calls, _logs, _ui_mirror = sticky_env

    client.call_chatgpt_responses(**_call_kwargs())
    client.call_chatgpt_responses(**_call_kwargs())

    assert calls["oauth"] == 2
    assert not client._sticky_tasks


def test_sticky_codex_login_failure_returns_to_the_normal_route(
    sticky_env, monkeypatch
):
    calls, logs, ui_mirror = sticky_env

    with ui_mirror.task_scope("task-1"):
        client.call_chatgpt_responses(**_call_kwargs())
        assert client._sticky_tasks

        def codex_route(**_kwargs):
            calls["codex"] += 1
            raise client.ChatGPTAPIError("invalid_token", "revoked", status=401)

        monkeypatch.setattr(client, "_call_codex_login_route", codex_route)
        monkeypatch.setattr(
            client,
            "_call_oauth_route",
            lambda **_kw: "from-oauth",
        )

        assert client.call_chatgpt_responses(**_call_kwargs()) == "from-oauth"

    assert not client._sticky_tasks
    assert any("本任務改回一般登入" in m for m in logs["warning"])


def test_sticky_codex_transient_error_propagates_and_stays_sticky(
    sticky_env, monkeypatch
):
    _calls, _logs, ui_mirror = sticky_env

    with ui_mirror.task_scope("task-1"):
        client.call_chatgpt_responses(**_call_kwargs())

        def codex_route(**_kwargs):
            raise client.ChatGPTAPIError("server_error", "boom", status=503)

        monkeypatch.setattr(client, "_call_codex_login_route", codex_route)

        with pytest.raises(client.ChatGPTAPIError) as caught:
            client.call_chatgpt_responses(**_call_kwargs())

        assert caught.value.code == "server_error"
        assert client._sticky_tasks


def test_consume_stream_cancellable_aborts_stalled_real_stream_on_cancel():
    """送出標頭後就不再送資料的真實連線：取消要立即拋出取消，且讀取執行緒會結束。

    用真實 socket 驗證：讀取中的執行緒持有串流 reader 的鎖，直接 close() 會被它卡住，
    所以這裡不能用假的 response。
    """
    import socket
    import threading
    import time

    from translation_tool.utils.cancellation import TaskCancelled, cancel_scope

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    accepted = []

    def serve():
        conn, _ = server.accept()
        accepted.append(conn)  # 保留連線，避免對方關閉
        conn.recv(65536)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )

    threading.Thread(target=serve, daemon=True).start()
    try:
        with requests.Session() as session:
            session.trust_env = False  # 不經過環境的 proxy
            response = session.post(
                f"http://127.0.0.1:{server.getsockname()[1]}/",
                stream=True,
                timeout=(5, 60),
            )
            cancel = threading.Event()
            threading.Timer(0.1, cancel.set).start()
            started = time.monotonic()
            with cancel_scope(cancel.is_set), pytest.raises(TaskCancelled):
                client._consume_stream_cancellable(response, None, None)
            assert time.monotonic() - started < 3

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(
                t.name == "cancellable-blocking" for t in threading.enumerate()
            ):
                time.sleep(0.02)
            assert not any(
                t.name == "cancellable-blocking" for t in threading.enumerate()
            )
    finally:
        server.close()
        for conn in accepted:
            conn.close()


def test_abort_stream_does_not_block_when_socket_unavailable():
    """拿不到底層 socket 時仍不卡住呼叫端，close 在背景執行緒進行。"""
    import threading
    import time

    closed = threading.Event()
    release = threading.Event()

    class _Response:
        def close(self):
            release.wait(5)  # 模擬 close 被讀取中的執行緒卡住
            closed.set()

    started = time.monotonic()
    client._abort_stream(_Response())
    assert time.monotonic() - started < 1
    release.set()
    assert closed.wait(2)
