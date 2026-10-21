"""條目列與詳情顯示最近一次智慧校對批次的處理狀態標籤。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_smart_review_service import SmartReviewMark
from app.ui import kit

# 狀態 -> (標籤文字, 色調)。待審查的 ready 不標記：使用者本來就會到審查分頁處理。
MARK_STYLES: dict[str, tuple[str, str]] = {
    "applied": ("校對：已套用", "dia"),
    "rejected": ("校對：保留原譯文", "neutral"),
    "no_change": ("AI：不用改", "neutral"),
    "needs_context": ("AI：需補 context", "gold"),
    "invalid": ("校對：無效", "red"),
    "invalid_format": ("校對：格式不符", "red"),
    "stale": ("校對：已過期", "gold"),
}


def smart_review_mark_chip(mark: SmartReviewMark | None) -> ft.Control | None:
    """回傳智慧校對狀態膠囊；沒有標記或狀態不需顯示時回傳 None。"""
    style = MARK_STYLES.get(mark.status) if mark is not None else None
    if mark is None or style is None:
        return None
    label, tone = style
    tooltip = f"最近一次智慧校對批次 {mark.run_id[:8]}：{label.split('：', 1)[-1]}"
    if mark.validation_error:
        tooltip += f"（{mark.validation_error}）"
    return kit.chip(label, tone, tooltip=tooltip)
