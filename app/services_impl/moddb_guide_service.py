"""指南條目的判斷輔助（供模組資料庫的條目校對頁使用）。

指南標記的比對規則只在 ``translation_tool.translation_db.quality`` 的
``guide_structure_issues``（經 ``compare_format_diff(kind=…)`` 與 ``translation_issues`` 使用）。
"""

from __future__ import annotations

from translation_tool.translation_db.schema import KIND_GUIDE


def is_guide_kind(kind: str) -> bool:
    return kind == KIND_GUIDE
