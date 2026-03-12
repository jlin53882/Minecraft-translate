"""translation_tool/core/lm_response_parser.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import re


def safe_json_loads(text: str):
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`strip`, `findall`

    回傳：依函式內 return path。
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
    """處理此 generator 並逐步回報進度（yield update dict）。"""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
