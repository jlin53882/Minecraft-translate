"""Codex OAuth Responses transport and Structured Outputs tests."""

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
import requests

from translation_tool.core import openai_codex_client as client


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
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    schema = body["text"]["format"]["schema"]
    assert schema["required"] == ["items"]
    assert schema["additionalProperties"] is False
    item_schema = schema["properties"]["items"]["items"]
    assert item_schema["required"] == ["id", "value"]
    assert item_schema["additionalProperties"] is False
    assert item_schema["properties"]["id"]["enum"] == ["line-1"]


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


def test_call_retries_once_after_unauthorized_and_keeps_reasoning(monkeypatch):
    class _UnauthorizedResponse:
        status_code = 401

        def close(self):
            self.closed = True

    class _SuccessResponse:
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
    monkeypatch.setattr(client, "_consume_stream", lambda _response, _meta: "done")

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
    assert token_requests == [
        {"profile_id": "profile-a"},
        {"force_refresh": True, "profile_id": "profile-a"},
    ]


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
    assert waits == [1]
    assert len(posts) == 3
    assert all(response.closed for response in stream_responses)


def test_http_error_preserves_status_type_param_and_request_id():
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
