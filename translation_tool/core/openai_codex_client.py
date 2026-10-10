"""Responses API transport for Sign in with ChatGPT plan usage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from translation_tool.core.codex_oauth import get_chatgpt_access_token
from translation_tool.core.lm_api_client import _build_translation_response_schema
from translation_tool.utils.redaction import redact_text

_RESPONSES_URL = "https://api.openai.com/v1/responses"


class ChatGPTAPIError(RuntimeError):
    """A safe provider error raised for failed or incomplete Responses streams."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: int | None = None,
        error_type: str | None = None,
        param: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ):
        self.code = redact_text(str(code or "unknown_error").strip())[:100]
        self.status = status
        self.error_type = redact_text(str(error_type or "").strip())[:100]
        self.param = redact_text(str(param or "").strip())[:100]
        self.request_id = redact_text(str(request_id or "").strip())[:100]
        self.retry_after = retry_after
        safe_message = redact_text(message.strip())[:300]
        details = [value for value in (safe_message,) if value]
        if self.error_type:
            details.append(f"type={self.error_type}")
        if self.param:
            details.append(f"param={self.param}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        detail = f"：{'；'.join(details)}" if details else ""
        http_status = f" HTTP {status}" if status is not None else ""
        super().__init__(f"ChatGPT Responses API {self.code}{http_status}{detail}")


def _response_error(response: requests.Response) -> ChatGPTAPIError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
    return ChatGPTAPIError(
        str(error.get("code") or error.get("type") or f"http_{response.status_code}"),
        str(error.get("message") or ""),
        status=response.status_code,
        error_type=str(error.get("type") or ""),
        param=str(error.get("param") or ""),
        request_id=response.headers.get("x-request-id", ""),
        retry_after=retry_after,
    )


def _parse_retry_after(value: object) -> float | None:
    """Parse Retry-After delta-seconds or HTTP-date into a nonnegative delay."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _stream_request(
    *,
    token: str,
    model_name: str,
    system_prompt: str,
    payload: dict,
    timeout: int,
    reasoning_effort: str | None = None,
) -> requests.Response:
    response_schema = _build_translation_response_schema(payload)
    body = {
        "model": model_name,
        "instructions": system_prompt,
        "input": [
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "minecraft_translation_batch",
                "strict": True,
                "schema": response_schema,
            }
        },
        "store": False,
        "stream": True,
    }
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort}
    response = requests.post(
        _RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json=body,
        timeout=(min(30, max(1, timeout)), max(1, timeout)),
        stream=True,
    )
    return response


def _event_payload(data: str) -> dict[str, Any] | None:
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ChatGPTAPIError("invalid_stream_event") from exc
    if not isinstance(payload, dict):
        raise ChatGPTAPIError("invalid_stream_event")
    return payload


def _find_refusal(response_data: dict[str, Any]) -> str:
    output = response_data.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "refusal":
                refusal = part.get("refusal")
                return refusal if isinstance(refusal, str) else ""
    return ""


def _consume_stream(response: requests.Response, meta_out: dict | None) -> str:
    chunks: list[str] = []
    refusal_chunks: list[str] = []
    completed_response: dict[str, Any] | None = None
    completed = False
    event_data: list[str] = []

    def consume_event() -> None:
        nonlocal completed, completed_response
        payload = _event_payload("\n".join(event_data))
        event_data.clear()
        if payload is None:
            return
        event_type = str(payload.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
        elif event_type == "response.refusal.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                refusal_chunks.append(delta)
        elif event_type == "response.refusal.done":
            refusal = payload.get("refusal")
            if isinstance(refusal, str):
                refusal_chunks[:] = [refusal]
        elif event_type == "error":
            error = payload.get("error", payload)
            if not isinstance(error, dict):
                error = {}
            raise ChatGPTAPIError(
                str(error.get("code") or error.get("type") or "stream_error"),
                str(error.get("message") or ""),
                error_type=str(error.get("type") or ""),
                param=str(error.get("param") or ""),
                request_id=str(payload.get("request_id") or ""),
            )
        elif event_type == "response.failed":
            failed = payload.get("response", {})
            error = failed.get("error", {}) if isinstance(failed, dict) else {}
            if not isinstance(error, dict):
                error = {}
            raise ChatGPTAPIError(
                str(error.get("code") or error.get("type") or "response_failed"),
                str(error.get("message") or ""),
                error_type=str(error.get("type") or ""),
                param=str(error.get("param") or ""),
                request_id=response.headers.get("x-request-id", ""),
            )
        elif event_type == "response.incomplete":
            incomplete = payload.get("response", {})
            details = (
                incomplete.get("incomplete_details", {})
                if isinstance(incomplete, dict)
                else {}
            )
            reason = str(details.get("reason") or "incomplete")
            raise ChatGPTAPIError("incomplete", reason)
        elif event_type == "response.completed":
            response_data = payload.get("response")
            completed_response = (
                response_data if isinstance(response_data, dict) else {}
            )
            completed = True
            refusal = _find_refusal(completed_response) or "".join(refusal_chunks)
            if refusal:
                raise ChatGPTAPIError("refusal", refusal)

    try:
        for raw_line in response.iter_lines(decode_unicode=True):
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line == "":
                consume_event()
                if completed:
                    break
            elif line.startswith("data:"):
                event_data.append(line[5:].lstrip())
        if event_data:
            consume_event()
    except requests.RequestException as exc:
        raise ChatGPTAPIError("stream_interrupted", redact_text(exc)) from exc
    finally:
        response.close()

    if not completed:
        raise ChatGPTAPIError("stream_incomplete")
    if meta_out is not None:
        usage = completed_response.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        output_details = usage.get("output_tokens_details") or {}
        if not isinstance(output_details, dict):
            output_details = {}
        output_tokens = usage.get("output_tokens")
        reasoning_tokens = output_details.get("reasoning_tokens")
        candidate_tokens = output_tokens
        if isinstance(output_tokens, int) and isinstance(reasoning_tokens, int):
            candidate_tokens = max(0, output_tokens - reasoning_tokens)
        meta_out.update(
            {
                "finish_reason": "STOP",
                "prompt_tokens": usage.get("input_tokens"),
                "candidates_tokens": candidate_tokens,
                "thoughts_tokens": reasoning_tokens,
                "total_tokens": usage.get("total_tokens"),
            }
        )
    return "".join(chunks)


def call_chatgpt_responses(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict,
    timeout: int,
    meta_out: dict | None = None,
    reasoning_effort: str | None = None,
    profile_id: str | None = None,
) -> str:
    """Call Responses with the locally stored ChatGPT OAuth token and SSE stream."""
    token_kwargs = {"profile_id": profile_id} if profile_id is not None else {}
    token = get_chatgpt_access_token(**token_kwargs)
    request_kwargs = {
        "token": token,
        "model_name": model_name,
        "system_prompt": system_prompt,
        "payload": payload,
        "timeout": timeout,
    }
    if reasoning_effort is not None:
        request_kwargs["reasoning_effort"] = reasoning_effort
    try:
        response = _stream_request(**request_kwargs)
    except requests.RequestException as exc:
        raise ChatGPTAPIError("network_error", redact_text(exc)) from exc
    if response.status_code == 401:
        response.close()
        token = get_chatgpt_access_token(force_refresh=True, **token_kwargs)
        request_kwargs["token"] = token
        try:
            response = _stream_request(**request_kwargs)
        except requests.RequestException as exc:
            raise ChatGPTAPIError("network_error", redact_text(exc)) from exc
    if not response.ok:
        error = _response_error(response)
        response.close()
        raise error
    return _consume_stream(response, meta_out)
