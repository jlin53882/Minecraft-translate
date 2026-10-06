"""translation_tool/core/lm_api_client.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import random
from copy import deepcopy

import requests

from translation_tool.utils.cancellation import interruptible_sleep
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning
from translation_tool.utils.redaction import redact_text

# 只重試「連線階段」的暫時性網路錯誤（連線被拒、DNS、連線逾時、連線中斷）。
# HTTP 狀態碼（429/503 等）與讀取逾時由 lm_translator_main 依狀態處理
# （換 key、等待、縮小 batch），不在這一層重試，避免重複等待與浪費配額。
NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_BASE_SEC = 1.0

# 單次回應的輸出 token 上限（generationConfig.maxOutputTokens）。
# 不設的話，模型重複輸出時會燒光整個輸出額度（issue #108）。
# 設定檔 lm_translator.max_output_tokens 可覆寫；設為 0 代表不送這個欄位。
DEFAULT_MAX_OUTPUT_TOKENS = 32768

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
    """Constrain the response array and IDs to the current input batch."""
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
    item_array["minItems"] = len(ids)
    item_array["maxItems"] = len(ids)
    item_array["items"]["properties"]["id"]["enum"] = ids
    return schema


class GeminiResponseFormatError(RuntimeError):
    """HTTP 請求已成功，但 Gemini 的回應缺少預期的 candidates/content/parts/text。

    仍是 ``RuntimeError`` 的子類（既有行為不變）；呼叫端可據此知道「這次沒有被配額拒絕」。
    """


def worst_case_request_sec(timeout: float) -> float:
    """一次 ``call_gemini_requests`` 最壞情況會花多久（秒）：給探測租約這類需要涵蓋整段的呼叫端用。

    連線階段最多 ``NETWORK_RETRY_ATTEMPTS`` 次嘗試，每次都可能套用完整的 ``timeout``，
    嘗試之間還有指數退避（含最大 jitter）。
    """
    backoff = sum(
        NETWORK_RETRY_BASE_SEC * (2 ** (attempt - 1)) + NETWORK_RETRY_BASE_SEC
        for attempt in range(1, NETWORK_RETRY_ATTEMPTS)
    )
    return NETWORK_RETRY_ATTEMPTS * max(timeout, 0.0) + backoff


def _post_with_retry(url: str, **kwargs) -> requests.Response:
    """requests.post，遇到連線階段的暫時性錯誤時指數退避重試（含 jitter）。"""
    for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
        try:
            return requests.post(url, **kwargs)
        except requests.exceptions.SSLError:
            raise  # 憑證 / TLS 問題重試無意義
        except requests.exceptions.ConnectionError as e:
            if attempt == NETWORK_RETRY_ATTEMPTS:
                raise
            wait = NETWORK_RETRY_BASE_SEC * (2 ** (attempt - 1))
            wait += random.uniform(0, NETWORK_RETRY_BASE_SEC)
            log_warning(
                f"[API] 連線失敗（第 {attempt}/{NETWORK_RETRY_ATTEMPTS} 次）：{redact_text(e)}；"
                f"{wait:.1f}s 後重試"
            )
            interruptible_sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


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
) -> str:
    """以同步 requests 方式呼叫 Gemini generateContent API，並回傳純文字回應。

    Args:
        max_output_tokens: 輸出 token 上限；None 時讀設定檔
            ``lm_translator.max_output_tokens``（預設 32768）。
        meta_out: 若提供，會以 ``extract_response_meta`` 的結果更新這個 dict
            （finish_reason、token 用量）。回傳值維持純文字，既有呼叫端不受影響。
    """
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
        "contents": [
            {
                "role": "user",
                "parts": [{"text": json.dumps(payload, ensure_ascii=False)}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "responseFormat": {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": _build_translation_response_schema(payload),
                }
            },
        },
    }

    lm_cfg = load_config().get("lm_translator", {})
    output_cap = _resolve_max_output_tokens(max_output_tokens, lm_cfg)
    if output_cap is not None:
        data["generationConfig"]["maxOutputTokens"] = output_cap

    request_timeout = int(lm_cfg.get("rate_limit", {}).get("timeout", 600))

    response = _post_with_retry(
        url,
        headers=headers,
        json=data,
        timeout=request_timeout,
    )

    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} {redact_text(response.text)[:2000]}",
            response=response,
        )

    result = response.json()

    if meta_out is not None:
        meta_out.update(extract_response_meta(result))

    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:  # noqa: BLE001
        raise GeminiResponseFormatError(
            "Gemini 回傳格式異常: "
            f"{redact_text(json.dumps(result, ensure_ascii=False))[:2000]}"
        )
