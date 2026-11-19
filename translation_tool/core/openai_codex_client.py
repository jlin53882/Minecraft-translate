"""供 ChatGPT 登入方案使用的 Responses API 傳輸與錯誤轉換。"""

from __future__ import annotations

import json
from typing import Any

import requests

from translation_tool.core.codex_cli_auth import (
    CodexCLIAuthError,
    load_codex_cli_credentials,
)
from translation_tool.core.codex_oauth import (
    chatgpt_account_status,
    get_chatgpt_access_token,
)
from translation_tool.core.lm_api_client import _build_translation_response_schema
from translation_tool.core.lm_batch_budget import estimate_text_tokens
from translation_tool.core.retry_after import parse_retry_after_header_value
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.redaction import redact_text

_RESPONSES_URL = "https://api.openai.com/v1/responses"
_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
# 第三方程式必須如實表明身分，不偽裝成 Codex CLI。
_CODEX_ORIGINATOR = "minecraft-translate"
_CODEX_USER_AGENT = "minecraft-translate"
# 這些錯誤代表 OAuth 路線的第三方分享額度受限，才改走 Codex 登入備援。
_SHARING_LIMIT_CODES = frozenset({"subscription_sharing_usage_limit_exceeded"})


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
    """從 OAuth Responses 錯誤回應擷取可供 UI 顯示的詳細資訊。"""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    # ChatGPT's OAuth direct route may reject a request before the Responses
    # API envelope is created.  That route returns FastAPI-style top-level
    # fields such as {"detail": "..."}, rather than {"error": {...}}.
    top_level = payload if isinstance(payload, dict) else {}
    raw_message = (
        error.get("message")
        or top_level.get("detail")
        or top_level.get("message")
        or ""
    )
    if not isinstance(raw_message, str):
        try:
            raw_message = json.dumps(raw_message, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raw_message = str(raw_message)
    raw_code = error.get("code") or top_level.get("code")
    raw_type = error.get("type") or top_level.get("type")
    raw_param = error.get("param") or top_level.get("param")
    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
    return ChatGPTAPIError(
        str(raw_code or raw_type or f"http_{response.status_code}"),
        raw_message,
        status=response.status_code,
        error_type=str(raw_type or ""),
        param=str(raw_param or ""),
        request_id=response.headers.get("x-request-id", ""),
        retry_after=retry_after,
    )


def _parse_retry_after(value: object) -> float | None:
    """把 Retry-After 秒數或 HTTP 日期解析成有限的非負延遲；無效回傳 None。"""
    return parse_retry_after_header_value(value)


def _safe_status(value: object, fallback: int | None = None) -> int | None:
    """Parse a provider status without allowing malformed payloads to escape."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value if 100 <= value <= 599 else fallback
    if isinstance(value, str) and len(value) <= 3 and value.isdigit():
        parsed = int(value)
        return parsed if 100 <= parsed <= 599 else fallback
    return fallback


def _stream_request(
    *,
    token: str,
    model_name: str,
    system_prompt: str,
    payload: dict,
    timeout: int,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
    conversation_history: list[dict] | None = None,
    response_schema: dict | None = None,
    codex_account_id: str | None = None,
) -> requests.Response:
    """建立並讀取 ChatGPT Responses 串流請求。

    ``codex_account_id`` 不是 None 時改走 Codex 後端（沿用本機 Codex CLI 登入）：
    該後端不接受輸出上限與 service_tier，所以不送；並依 Codex 後端要求帶上身分標頭。
    """
    use_codex_backend = codex_account_id is not None
    response_schema = response_schema or _build_translation_response_schema(payload)
    input_history = []
    for turn in conversation_history or []:
        role = turn.get("role")
        if role == "model":
            role = "assistant"
        text = turn.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str):
            raise ValueError(
                "ChatGPT conversation history turns must contain role/text"
            )
        input_history.append({"role": role, "content": text})
    input_history.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        }
    )
    body = {
        "model": model_name,
        "instructions": system_prompt,
        "input": input_history,
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
    # Responses API requires at least 16 output tokens. Zero/invalid values
    # retain the provider default; never send a request the API will reject.
    if (
        not use_codex_backend
        and isinstance(max_output_tokens, int)
        and max_output_tokens >= 16
    ):
        body["max_output_tokens"] = max_output_tokens
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort}
    if service_tier and not use_codex_backend:
        body["service_tier"] = service_tier
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if use_codex_backend:
        headers["originator"] = _CODEX_ORIGINATOR
        headers["User-Agent"] = _CODEX_USER_AGENT
        if codex_account_id:
            headers["ChatGPT-Account-ID"] = codex_account_id
    response = requests.post(
        _CODEX_RESPONSES_URL if use_codex_backend else _RESPONSES_URL,
        headers=headers,
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


# 本地輸出保護的寬限倍數：估算是近似值，只有明顯超出（例如模型重複輸出）才中止。
OUTPUT_GUARD_MARGIN = 1.5


def _output_guard_limit(output_guard_tokens: int | None) -> float | None:
    """回傳本地輸出保護的 token 上限；未設定或不是正整數時不保護。"""
    if (
        isinstance(output_guard_tokens, bool)
        or not isinstance(output_guard_tokens, int)
        or output_guard_tokens <= 0
    ):
        return None
    return output_guard_tokens * OUTPUT_GUARD_MARGIN


def _consume_stream(
    response: requests.Response,
    meta_out: dict | None,
    output_guard_tokens: int | None = None,
) -> str:
    """讀取 Responses 串流；輸出估算超過本地保護上限時中止並視為未完整完成。

    ChatGPT 請求不送 ``max_output_tokens``，模型重複輸出時沒有 API 端的上限擋住，
    所以由這裡以本地估算中止，拋出 ``incomplete`` 交給既有的縮批重試。
    """
    guard_limit = _output_guard_limit(output_guard_tokens)
    estimated_output = 0.0
    chunks: list[str] = []
    refusal_chunks: list[str] = []
    completed_response: dict[str, Any] | None = None
    completed = False
    event_data: list[str] = []

    def consume_event() -> None:
        nonlocal completed, completed_response, estimated_output
        payload = _event_payload("\n".join(event_data))
        event_data.clear()
        if payload is None:
            return
        event_type = str(payload.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
                if guard_limit is not None:
                    estimated_output += estimate_text_tokens(delta)
                    if estimated_output > guard_limit:
                        raise ChatGPTAPIError(
                            "incomplete",
                            f"local_output_guard：輸出估計超過 {output_guard_tokens} tokens"
                            f" 的 {OUTPUT_GUARD_MARGIN:g} 倍",
                        )
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
            nested_error = isinstance(error, dict) and error is not payload
            if not isinstance(error, dict):
                error = {}
            error_type = (
                error.get("type") if nested_error else payload.get("error_type")
            )
            raise ChatGPTAPIError(
                str(error.get("code") or error_type or "stream_error"),
                str(error.get("message") or ""),
                status=_safe_status(error.get("status")),
                error_type=str(error_type or ""),
                param=str(error.get("param") or ""),
                request_id=str(
                    payload.get("request_id")
                    or response.headers.get("x-request-id", "")
                ),
                retry_after=_parse_retry_after(
                    error.get("retry_after") or response.headers.get("Retry-After")
                ),
            )
        elif event_type == "response.failed":
            failed = payload.get("response", {})
            error = failed.get("error", {}) if isinstance(failed, dict) else {}
            if not isinstance(error, dict):
                error = {}
            rate_limited_status = _safe_status(error.get("status"))
            if rate_limited_status is None and "rate" in (
                f"{error.get('type') or ''} {error.get('code') or ''}".lower()
            ):
                rate_limited_status = 429
            raise ChatGPTAPIError(
                str(error.get("code") or error.get("type") or "response_failed"),
                str(error.get("message") or ""),
                status=rate_limited_status,
                error_type=str(error.get("type") or ""),
                param=str(error.get("param") or ""),
                request_id=response.headers.get("x-request-id", ""),
                retry_after=_parse_retry_after(
                    error.get("retry_after") or response.headers.get("Retry-After")
                ),
            )
        elif event_type == "response.incomplete":
            incomplete = payload.get("response", {})
            details = (
                incomplete.get("incomplete_details", {})
                if isinstance(incomplete, dict)
                else {}
            )
            if not isinstance(details, dict):
                details = {}
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
        response_tier = completed_response.get("service_tier")
        if isinstance(response_tier, str) and response_tier:
            meta_out["service_tier"] = response_tier
    return "".join(chunks)


def _call_oauth_route(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict,
    timeout: int,
    max_output_tokens: int | None = None,
    meta_out: dict | None = None,
    reasoning_effort: str | None = None,
    fast: bool = False,
    profile_id: str | None = None,
    conversation_history: list[dict] | None = None,
    response_schema: dict | None = None,
    output_guard_tokens: int | None = None,
) -> str:
    """使用本程式自己登入的 ChatGPT OAuth token 呼叫 Responses 串流。

    ``max_output_tokens`` 會送給 API；``output_guard_tokens`` 不送，只在本地估算輸出、
    明顯超過時中止串流（見 ``_consume_stream``）。
    """
    token_kwargs = {"profile_id": profile_id} if profile_id is not None else {}
    token = get_chatgpt_access_token(**token_kwargs)
    request_kwargs = {
        "token": token,
        "model_name": model_name,
        "system_prompt": system_prompt,
        "payload": payload,
        "timeout": timeout,
        "max_output_tokens": max_output_tokens,
    }
    if conversation_history:
        request_kwargs["conversation_history"] = conversation_history
    if reasoning_effort is not None:
        request_kwargs["reasoning_effort"] = reasoning_effort
    # Codex maps its provider-level fast flag to the Responses API's
    # service_tier=priority request value. The public ChatGPT OAuth route
    # rejects the literal service_tier=fast value, but accepts priority.
    if fast:
        request_kwargs["service_tier"] = "priority"
    if response_schema is not None:
        request_kwargs["response_schema"] = response_schema
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
    return _consume_stream(response, meta_out, output_guard_tokens)


def _call_codex_login_route(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict,
    timeout: int,
    meta_out: dict | None,
    reasoning_effort: str | None,
    conversation_history: list[dict] | None,
    response_schema: dict | None,
    output_guard_tokens: int | None = None,
    expected_email: str = "",
) -> str:
    """用本機 Codex CLI 的登入打 Codex 後端；token 過期時不自行更新。

    ``expected_email`` 是本程式所選 ChatGPT 帳號的 email；Codex CLI 登入的帳號
    必須與它相同，否則不送出，避免把翻譯內容用到別的帳號與額度。
    """
    credentials = load_codex_cli_credentials()
    if not expected_email or credentials.email != expected_email:
        raise CodexCLIAuthError(
            "Codex CLI 登入的帳號與目前選用的 ChatGPT 帳號不同或無法確認，"
            "為避免使用到別的帳號，不改用 Codex 登入。"
        )
    request_kwargs = {
        "token": credentials.access_token,
        "model_name": model_name,
        "system_prompt": system_prompt,
        "payload": payload,
        "timeout": timeout,
        "codex_account_id": credentials.account_id,
    }
    if conversation_history:
        request_kwargs["conversation_history"] = conversation_history
    if reasoning_effort is not None:
        request_kwargs["reasoning_effort"] = reasoning_effort
    if response_schema is not None:
        request_kwargs["response_schema"] = response_schema
    try:
        response = _stream_request(**request_kwargs)
    except requests.RequestException as exc:
        raise ChatGPTAPIError("network_error", redact_text(exc)) from exc
    if not response.ok:
        error = _response_error(response)
        response.close()
        raise error
    return _consume_stream(response, meta_out, output_guard_tokens)


def _selected_account_email(profile_id: str | None) -> str:
    """回傳本程式所選 ChatGPT 帳號（指定 profile 或目前啟用者）的 email；查不到回空字串。"""
    status = chatgpt_account_status()
    if profile_id is None:
        return str(status.get("email") or "").strip().casefold()
    for account in status.get("accounts", []):
        if account.get("profile_id") == profile_id:
            return str(account.get("email") or "").strip().casefold()
    return ""


def call_chatgpt_responses(**kwargs: Any) -> str:
    """先走自己的 OAuth 登入；第三方分享額度用盡時改用本機 Codex CLI 登入。"""
    try:
        return _call_oauth_route(**kwargs)
    except ChatGPTAPIError as exc:
        if exc.code not in _SHARING_LIMIT_CODES:
            raise
        original = exc
    log_warning("[ChatGPT] 分享額度已達上限，嘗試改用本機 Codex CLI 登入重試。")
    try:
        return _call_codex_login_route(
            model_name=kwargs["model_name"],
            system_prompt=kwargs["system_prompt"],
            payload=kwargs["payload"],
            timeout=kwargs["timeout"],
            meta_out=kwargs.get("meta_out"),
            reasoning_effort=kwargs.get("reasoning_effort"),
            conversation_history=kwargs.get("conversation_history"),
            response_schema=kwargs.get("response_schema"),
            output_guard_tokens=kwargs.get("output_guard_tokens"),
            expected_email=_selected_account_email(kwargs.get("profile_id")),
        )
    except (CodexCLIAuthError, ChatGPTAPIError) as fallback_error:
        # 備援失敗（含 Codex 登入被撤銷的 401）只記在日誌；使用者看到的仍是原本的
        # 分享額度錯誤，避免把 Codex 登入的失敗誤導成「請重新登入本程式的帳號」。
        log_warning(
            f"[ChatGPT] 分享額度已達上限，且無法改用 Codex 登入：{fallback_error}"
        )
        raise original from fallback_error
