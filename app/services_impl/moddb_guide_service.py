"""指南條目的判斷輔助（供模組資料庫的條目校對頁使用）。

指南標記的比對規則只在 ``translation_tool.translation_db.quality`` 的
``guide_structure_issues``（經 ``compare_format_diff(kind=…)`` 與 ``translation_issues`` 使用）。
"""

from __future__ import annotations

from translation_tool.translation_db.quality import translation_issues
from translation_tool.translation_db.schema import KIND_GUIDE


def is_guide_kind(kind: str) -> bool:
    return kind == KIND_GUIDE


def guide_mark_issues(source: str, translated: str) -> list[str]:
    """譯文與原文的指南標記差異（繁體中文說明）；沒有差異回傳空串列。"""
    return translation_issues(source, translated, kind=KIND_GUIDE)
