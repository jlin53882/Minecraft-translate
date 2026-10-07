"""使用者輸入的路徑文字整理：去掉前後空白與引號。

Windows 檔案總管「複製為路徑」會帶雙引號（``"C:\\Users\\...\\mods"``），直接當路徑會被
當成相對路徑而找不到。所有路徑輸入欄位與路徑解析都共用這裡的規則。
"""

from __future__ import annotations

import os
from typing import Any

# 直引號、單引號、彎引號、日式引號
QUOTES = "\"'“”‘’「」"


def strip_path_quotes(text: str) -> str:
    """去掉前後的引號（輸入中使用：沒有引號時不動空白，路徑中間與結尾可能還在打字）。

    整段被引號包住（貼上檔案總管「複製為路徑」）時，引號外多餘的空白一併去掉。
    """
    core = text.strip()
    if len(core) >= 2 and core[0] in QUOTES and core[-1] in QUOTES:
        return normalize_path_text(core)
    return text.strip(QUOTES)


def normalize_path_text(value: Any) -> str:
    """去掉前後空白與引號（成對或單邊都處理）；``None`` 回傳空字串。"""
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    text = str(value or "").strip()
    while len(text) >= 2 and text[0] in QUOTES and text[-1] in QUOTES:
        text = text[1:-1].strip()
    return text.strip(QUOTES).strip()
