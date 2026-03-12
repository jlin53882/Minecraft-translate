"""translation_tool/core/lm_response_parser.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import re
from typing import Iterable


def safe_json_loads(text: str):
    """safe_json_loads 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text)
        text = re.sub(r"```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    matches = re.findall(r"\{[\s\S]*\}", text)
    for m in matches:
        try:
            return json.loads(m)
        except json.JSONDecodeError:
            continue

    raise RuntimeError("JSON 解析失敗：無法解析模型回傳內容")


def chunked(lst, size):
    """chunked 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
