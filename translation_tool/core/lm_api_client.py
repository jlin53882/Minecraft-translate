"""translation_tool/core/lm_api_client.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json

import requests

from translation_tool.utils.config_manager import load_config

def call_gemini_requests(
    *,
    model_name: str,
    system_prompt: str,
    payload: dict,
    api_key: str,
    temperature: float,
) -> str:
    """呼叫 Gemini API 進行翻譯。"""
    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model_name}:generateContent"
        f"?key={api_key}"
    )

    headers = {"Content-Type": "application/json"}

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

    response = requests.post(
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
    except Exception:
        raise RuntimeError(
            f"Gemini 回傳格式異常: {json.dumps(result, ensure_ascii=False)}"
        )
