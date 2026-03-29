"""translation_tool/core/lm_response_parser.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
import re


def safe_json_loads(text: str):
    """將模型回傳的文字嘗試解析為 JSON，支援去除 Markdown code fence 並從雜訊文字中截取第一個合法 JSON 區塊。"""
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text)
        text = re.sub(r"```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # ✅ Issue #12 修復：使用 brace-counting parser 取代 non-greedy regex
    # 正確處理巢狀 JSON 與多個相鄰 JSON 區塊
    blocks = _extract_json_blocks(text)
    for block in blocks:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    raise RuntimeError("JSON 解析失敗：無法解析模型回傳內容")


def _extract_json_blocks(text: str):
    """使用 brace-counting 找出文字中所有完整的 JSON 區塊。

    演算法：從第一個 '{' 開始，計算深度（{ 和 [ +1，} 和 ] -1）。
    當深度回到 0 時，該區塊為一個完整的 JSON。
    遇到非 { 或 [ 時不影響（depth 不變）。
    """
    blocks = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            start = i
            depth = 0
            j = i
            while j < n:
                c = text[j]
                if c == "{" or c == "[":
                    depth += 1
                elif c == "}" or c == "]":
                    depth -= 1
                    if depth == 0:
                        blocks.append(text[start : j + 1])
                        i = j + 1
                        break
                j += 1
            else:
                # 未找到匹配的結尾，結束
                break
        else:
            i += 1
    return blocks


def chunked(lst, size):
    """將序列 lst 依指定大小 size 分塊，yield 每個 chunk（最後一塊可能較短）。"""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
