"""translation_tool/plugins/shared/lang_text_rules.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import re

# Match minecraft-like color/format tokens, e.g. &a §l
_FMT_RE = re.compile(r"(?:&|§)[0-9a-fk-or]", re.IGNORECASE)

def _strip_fmt(s: str) -> str:
    """移除行內格式標記（如 &a / §l）。"""
    return _FMT_RE.sub("", s)

def is_already_zh(s: str) -> bool:
    """啟發式判斷：去除格式標記後，若有中日韓文字且几乎無英文，則視為已翻譯。"""
    t = _strip_fmt(s).strip()
    if not t:
        return True
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", t))
    if not has_cjk:
        return False
    letters = len(re.findall(r"[A-Za-z]", t))
    return letters <= 2
