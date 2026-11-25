"""「其他原文」頁籤需要的資料庫常數與型別（View 不直接 import 引擎）。"""

from __future__ import annotations

from translation_tool.translation_db.variant_listing import (
    STATUS_ALL,
    STATUS_TRANSLATED,
    STATUS_UNTRANSLATED,
    VariantListRow,
)

__all__ = [
    "STATUS_ALL",
    "STATUS_TRANSLATED",
    "STATUS_UNTRANSLATED",
    "VariantListRow",
]
