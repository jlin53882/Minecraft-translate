"""Codex OAuth Responses transport and Structured Outputs tests."""

import json

import pytest
import requests

from translation_tool.core import openai_codex_client as client


class _FakeStreamResponse:
    def __init__(self, events):
        self.events = events
        self.headers = {}
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
    monkeypatch.setattr(
        client, "get_chatgpt_access_token", lambda **kwargs: next(tokens)
    )
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
        )
        == "done"
    )
    assert [request["token"] for request in requests_seen] == [
        "expired-token",
        "refreshed-token",
    ]
    assert all(request["reasoning_effort"] == "medium" for request in requests_seen)


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
