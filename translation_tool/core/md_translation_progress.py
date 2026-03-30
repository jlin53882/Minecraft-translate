"""translation_tool/core/md_translation_progress.py 模組。

用途：Markdown 翻譯進度的管理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from typing import Any


class _ProgressProxy:
    """把 step 內部 0~1 進度映射到 pipeline 區段。"""

    def __init__(self, parent: Any, base: float, span: float):
        self.parent = parent
        self.base = float(base)
        self.span = float(span)

    def set_progress(self, p: float):
        if not self.parent or not hasattr(self.parent, "set_progress"):
            return
        try:
            p = 0.0 if p is None else float(p)
            p = min(1.0, max(0.0, p))
            self.parent.set_progress(self.base + p * self.span)
        except Exception:
            pass
