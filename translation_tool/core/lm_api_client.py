"""translation_tool/core/lm_api_client.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import random

import requests

from translation_tool.utils.cancellation import interruptible_sleep
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning

# 只重試「連線階段」的暫時性網路錯誤（連線被拒、DNS、連線逾時、連線中斷）。
# HTTP 狀態碼（429/503 等）與讀取逾時由 lm_translator_main 依狀態處理
# （換 key、等待、縮小 batch），不在這一層重試，避免重複等待與浪費配額。
NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_BASE_SEC = 1.0


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
                f"[API] 連線失敗（第 {attempt}/{NETWORK_RETRY_ATTEMPTS} 次）：{e}；"
                f"{wait:.1f}s 後重試"
            )
            interruptible_sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def call_gemini_requests(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict,
    api_key: str,
    temperature: float,
) -> str:
    """以同步 requests 方式呼叫 Gemini generateContent API，並回傳純文字回應。"""
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
            "responseMimeType": "application/json",
        },
    }

    request_timeout = int(
        load_config().get("lm_translator", {}).get("rate_limit", {}).get("timeout", 600)
    )

    response = _post_with_retry(
        url,
        headers=headers,
        json=data,
        timeout=request_timeout,
    )

    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} {response.text}",
            response=response,
        )

    result = response.json()

    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:  # noqa: BLE001
        raise RuntimeError(
            f"Gemini 回傳格式異常: {json.dumps(result, ensure_ascii=False)}"
        )
