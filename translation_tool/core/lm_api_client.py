"""translation_tool/core/lm_api_client.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from copy import deepcopy

import requests

from translation_tool.utils.cancellation import interruptible_sleep
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.config_schema import get_schema_default
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.redaction import redact_text

# 只重試「連線階段」的暫時性網路錯誤（連線被拒、DNS、連線逾時、連線中斷）。
# HTTP 狀態碼（429/503 等）與讀取逾時由 lm_translator_main 依狀態處理
# （換 key、等待、縮小 batch），不在這一層重試，避免重複等待與浪費配額。
NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_BASE_SEC = 1.0
NETWORK_CONNECT_TIMEOUT_SEC = 30

# 單次回應的輸出 token 上限（generationConfig.maxOutputTokens）。
# 不設的話，模型重複輸出時會燒光整個輸出額度（issue #108）。
# 設定檔 lm_translator.max_output_tokens 可覆寫；設為 0 代表不送這個欄位。
DEFAULT_MAX_OUTPUT_TOKENS = get_schema_default("lm_translator.max_output_tokens")

# Gemini responseFormat.text.schema uses a JSON Schema subset.
TRANSLATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["id", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _build_translation_response_schema(payload: dict) -> dict:
    """建立限制在目前輸入批次內、供翻譯回應使用的結構。"""
    if not isinstance(payload, dict):
        raise ValueError("Gemini translation payload must be an object")  # noqa: TRY004
    if "items" not in payload:
        raise ValueError("Gemini translation payload must contain items")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Gemini translation payload items must be a list")  # noqa: TRY004
    if not items:
        raise ValueError("Gemini translation payload must contain at least one item")

    ids = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(  # noqa: TRY004
                "Gemini 翻譯 payload 的每個 item 都必須是 object"
            )
        if not isinstance(item.get("id"), str):
            raise ValueError(  # noqa: TRY004
                "Gemini 翻譯 payload 的每個 item 都必須有字串 id"
            )
        if not isinstance(item.get("value"), str):
            raise ValueError(  # noqa: TRY004
                "Gemini 翻譯 payload 的每個 item 都必須有字串 value"
            )
        ids.append(item["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Gemini 翻譯 payload 的 item id 不可重複")

    schema = deepcopy(TRANSLATION_RESPONSE_SCHEMA)
    item_array = schema["properties"]["items"]
    # 刻意不設 minItems / maxItems：依批次筆數動態設定陣列長度會讓 Gemini
    # （實測 gemini-3.1-flash-lite，150 筆）回 400 INVALID_ARGUMENT。
    # 筆數是否完整改由回覆後的程式端驗證（lm_translator_main._merge_batch_response：
    # ID 必須屬於本批、不可重複、不可缺漏），不完整的回覆不會被合併或寫入。
    item_array["items"]["properties"]["id"]["enum"] = ids
    return schema


class GeminiResponseFormatError(RuntimeError):
    """HTTP 請求已成功，但 Gemini 的回應缺少預期的 candidates/content/parts/text。

    仍是 ``RuntimeError`` 的子類（既有行為不變）；呼叫端可據此知道「這次沒有被配額拒絕」。
    """


def _request_timeout(lm_cfg: dict) -> int:
    """API 請求逾時秒數：設定缺漏時用 schema 的 ``rate_limit.timeout`` 預設值。"""
    rate_limit = lm_cfg.get("rate_limit", {})
    return int(
        rate_limit.get("timeout", get_schema_default("lm_translator.rate_limit.timeout"))
    )


def worst_case_request_sec(timeout: float) -> float:
    """一次 ``call_gemini_requests`` 最壞情況會花多久（秒）：給探測租約這類需要涵蓋整段的呼叫端用。

    連線階段最多 ``NETWORK_RETRY_ATTEMPTS`` 次嘗試，每次都可能套用完整的 ``timeout``，
    嘗試之間還有指數退避（含最大 jitter）。

    注意：``requests`` 的 timeout 不是整段 wall-clock 上限（慢速傳輸、多位址連線都可能超過），所以
    這只是實務上保守的預算，不是嚴格上限。探測租約因此另外靠「心跳續租」
    （``ModelQuotaRegistry.hold_probe_lease``）涵蓋請求還活著的整段時間；這個預算只是持有者異常
    結束時租約的保底長度。
    """
    backoff = sum(
        NETWORK_RETRY_BASE_SEC * (2 ** (attempt - 1)) + NETWORK_RETRY_BASE_SEC
        for attempt in range(1, NETWORK_RETRY_ATTEMPTS)
    )
    return NETWORK_RETRY_ATTEMPTS * max(timeout, 0.0) + backoff


def _post_with_retry(
    url: str,
    *,
    network_event_callback: Callable[[dict[str, object]], None] | None = None,
    **kwargs,
) -> requests.Response:
    """requests.post，遇到連線階段的暫時性錯誤時指數退避重試（含 jitter）。"""
    for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
        _emit_network_event(
            network_event_callback,
            {
                "kind": "network_attempt_start",
                "network_attempt": attempt,
                "network_total": NETWORK_RETRY_ATTEMPTS,
            },
        )
        try:
            response = requests.post(url, **kwargs)
        except requests.exceptions.SSLError:
            _emit_network_event(
                network_event_callback,
                {
                    "kind": "network_failure",
                    "network_attempt": attempt,
                    "network_total": NETWORK_RETRY_ATTEMPTS,
                    "error_kind": "tls_error",
                    "exception_type": "SSLError",
                    "retryable": False,
                },
            )
            raise  # 憑證 / TLS 問題重試無意義
        except requests.exceptions.ConnectionError as e:
            _emit_network_event(
                network_event_callback,
                {
                    "kind": "network_failure",
                    "network_attempt": attempt,
                    "network_total": NETWORK_RETRY_ATTEMPTS,
                    "error_kind": "connection_error",
                    "exception_type": type(e).__name__,
                    "retryable": attempt < NETWORK_RETRY_ATTEMPTS,
                },
            )
            if attempt == NETWORK_RETRY_ATTEMPTS:
                raise
            wait = NETWORK_RETRY_BASE_SEC * (2 ** (attempt - 1))
            wait += random.uniform(0, NETWORK_RETRY_BASE_SEC)
            _emit_network_event(
                network_event_callback,
                {
                    "kind": "network_retry_wait",
                    "network_attempt": attempt,
                    "network_total": NETWORK_RETRY_ATTEMPTS,
                    "wait_seconds": wait,
                    "error_kind": "connection_error",
                    "exception_type": type(e).__name__,
                },
            )
            log_warning(
                f"[API] 連線失敗（第 {attempt}/{NETWORK_RETRY_ATTEMPTS} 次）：{redact_text(e)}；"
                f"{wait:.1f}s 後重試"
            )
            wait_started = time.monotonic()
            interruptible_sleep(wait)
            _emit_network_event(
                network_event_callback,
                {
                    "kind": "network_retry_resume",
                    "network_attempt": attempt,
                    "network_total": NETWORK_RETRY_ATTEMPTS,
                    "wait_seconds": wait,
                    "elapsed_seconds": time.monotonic() - wait_started,
                },
            )
        else:
            status = getattr(response, "status_code", None)
            if not isinstance(status, int):
                try:
                    status = int(status)
                except (TypeError, ValueError):
                    status = None
            _emit_network_event(
                network_event_callback,
                {
                    "kind": "network_response",
                    "network_attempt": attempt,
                    "network_total": NETWORK_RETRY_ATTEMPTS,
                    "http_status": status,
                },
            )
            return response
    raise AssertionError("unreachable")  # pragma: no cover


def _emit_network_event(
    callback: Callable[[dict[str, object]], None] | None,
    event: dict[str, object],
) -> None:
    """送出不含 payload、response body 或 credentials 的網路診斷事件。"""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:  # noqa: BLE001 - telemetry must never break an API call
        log_warning("[API] network telemetry callback failed")


def _resolve_max_output_tokens(explicit: int | None, lm_cfg: dict) -> int | None:
    """決定要送出的 maxOutputTokens：明確參數 > 設定檔 > 預設值；<= 0 代表不送。"""
    value = explicit if explicit is not None else lm_cfg.get("max_output_tokens")
    if value is None:
        value = DEFAULT_MAX_OUTPUT_TOKENS
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_OUTPUT_TOKENS
    return value if value > 0 else None


def _build_gemini_contents(
    payload: dict, conversation_history: list[dict] | None
) -> list[dict]:
    """建立目前使用者訊息，必要時接續用戶端對話歷史。"""
    contents: list[dict] = []
    for turn in conversation_history or []:
        if not isinstance(turn, dict):
            raise TypeError("Gemini conversation history turns must be objects")
        role = turn.get("role")
        text = turn.get("text")
        if role not in {"user", "model"} or not isinstance(text, str):
            raise ValueError("Gemini conversation history turns must contain role/text")
        contents.append({"role": role, "parts": [{"text": text}]})
    contents.append(
        {
            "role": "user",
            "parts": [{"text": json.dumps(payload, ensure_ascii=False)}],
        }
    )
    return contents


def extract_response_meta(result: dict) -> dict:
    """從 generateContent 回應取出截斷診斷所需的欄位。

    - finish_reason：``candidates[0].finishReason``（STOP / MAX_TOKENS / SAFETY ...）
    - prompt_tokens / candidates_tokens / thoughts_tokens / total_tokens：
      ``usageMetadata`` 對應欄位。思考 token（thoughtsTokenCount）也會計入輸出額度，
      但不一定包含在 candidatesTokenCount 內，所以分開保留。
    缺少的欄位為 None（呼叫端不可假設一定存在）。
    """
    candidates = result.get("candidates") or [{}]
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    usage = result.get("usageMetadata") or {}
    return {
        "finish_reason": first.get("finishReason"),
        "prompt_tokens": usage.get("promptTokenCount"),
        "candidates_tokens": usage.get("candidatesTokenCount"),
        "thoughts_tokens": usage.get("thoughtsTokenCount"),
        "total_tokens": usage.get("totalTokenCount"),
    }


def call_gemini_requests(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict,
    api_key: str,
    temperature: float,
    max_output_tokens: int | None = None,
    meta_out: dict | None = None,
    reasoning_effort: str | None = None,
    profile_id: str | None = None,
    conversation_history: list[dict] | None = None,
    provider: str | None = None,
    lm_config: dict | None = None,
    fast: bool = False,
    response_schema: dict | None = None,
    network_event_callback: Callable[[dict[str, object]], None] | None = None,
) -> str:
    """依 provider 與固定的 lm 設定送出翻譯請求，並回傳模型文字結果。"""
    # Batch translation passes its start-of-task snapshot so a settings change
    # cannot route an in-flight Gemini model to the ChatGPT Responses endpoint.
    lm_cfg = (
        lm_config
        if isinstance(lm_config, dict)
        else load_config().get("lm_translator", {})
    )
    provider = provider if provider is not None else lm_cfg.get("provider", "gemini")
    if provider == "chatgpt":
        from translation_tool.core.openai_codex_client import call_chatgpt_responses

        chatgpt_kwargs = {
            "model_name": model_name,
            "system_prompt": system_prompt,
            "payload": payload,
            "timeout": _request_timeout(lm_cfg),
            "meta_out": meta_out,
            "reasoning_effort": reasoning_effort,
        }
        if max_output_tokens is not None:
            chatgpt_kwargs["max_output_tokens"] = max_output_tokens
        if fast:
            chatgpt_kwargs["fast"] = True
        if conversation_history:
            chatgpt_kwargs["conversation_history"] = conversation_history
        if profile_id is not None:
            chatgpt_kwargs["profile_id"] = profile_id
        if response_schema is not None:
            chatgpt_kwargs["response_schema"] = response_schema
        _emit_network_event(
            network_event_callback,
            {
                "kind": "network_attempt_start",
                "network_attempt": 1,
                "network_total": 1,
                "network_scope": "chatgpt_provider_call",
            },
        )
        try:
            result = call_chatgpt_responses(**chatgpt_kwargs)
        except Exception as exc:
            _emit_network_event(
                network_event_callback,
                {
                    "kind": "network_failure",
                    "network_attempt": 1,
                    "network_total": 1,
                    "network_scope": "chatgpt_provider_call",
                    "error_kind": "provider_error",
                    "exception_type": type(exc).__name__,
                },
            )
            raise
        _emit_network_event(
            network_event_callback,
            {
                "kind": "network_response",
                "network_attempt": 1,
                "network_total": 1,
                "network_scope": "chatgpt_provider_call",
                "http_status": (meta_out or {}).get("http_status", 200),
            },
        )
        return result
    if provider != "gemini":
        raise RuntimeError(f"不支援的翻譯服務供應商：{provider}")

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model_name}:generateContent"
    )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    data = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": _build_gemini_contents(payload, conversation_history),
        "generationConfig": {
            "temperature": temperature,
            "responseFormat": {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": response_schema
                    if response_schema is not None
                    else _build_translation_response_schema(payload),
                }
            },
        },
    }

    output_cap = _resolve_max_output_tokens(max_output_tokens, lm_cfg)
    if output_cap is not None:
        data["generationConfig"]["maxOutputTokens"] = output_cap

    request_timeout = _request_timeout(lm_cfg)
    connect_timeout = min(request_timeout, NETWORK_CONNECT_TIMEOUT_SEC)

    request_kwargs = {
        "headers": headers,
        "json": data,
        "timeout": (connect_timeout, request_timeout),
    }
    if network_event_callback is not None:
        request_kwargs["network_event_callback"] = network_event_callback
    response = _post_with_retry(url, **request_kwargs)

    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} {redact_text(response.text)[:2000]}",
            response=response,
        )

    # HTTP 已成功：之後任何解析問題（不是 JSON、最外層型別不對、缺欄位）都是「回應格式異常」，
    # 與配額無關（呼叫端據此知道這次沒有被配額拒絕）。
    try:
        result = response.json()
    except Exception as exc:
        raise GeminiResponseFormatError(
            f"Gemini 回傳不是有效的 JSON: {redact_text(exc)}"
        ) from exc

    if meta_out is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            meta_out["http_status"] = status
        try:
            meta_out.update(extract_response_meta(result))
        except Exception as exc:
            raise GeminiResponseFormatError(
                "Gemini 回傳格式異常: "
                f"{redact_text(json.dumps(result, ensure_ascii=False, default=str))[:2000]}"
            ) from exc

    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:  # noqa: BLE001
        raise GeminiResponseFormatError(
            "Gemini 回傳格式異常: "
            f"{redact_text(json.dumps(result, ensure_ascii=False, default=str))[:2000]}"
        )
