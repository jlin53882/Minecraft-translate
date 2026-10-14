"""指南條目的格式提醒（供模組資料庫的條目校對頁使用）。

View 不直接碰引擎；指南標記的比對規則在 ``translation_tool.core.guide_format``。
"""

from __future__ import annotations

from translation_tool.core.guide_format import guide_issues
from translation_tool.translation_db.schema import KIND_GUIDE


def is_guide_kind(kind: str) -> bool:
    return kind == KIND_GUIDE


def guide_mark_issues(source: str, translated: str) -> list[str]:
    """譯文與原文的指南標記差異（繁體中文說明）；沒有差異回傳空串列。"""
    return guide_issues(source, translated)
