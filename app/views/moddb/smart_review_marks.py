"""條目列與詳情顯示最近一次智慧校對批次的處理狀態標籤。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SmartReviewMark,
    SmartReviewStatus,
)
from app.ui import kit

# 狀態 -> (標籤文字, 色調)。待審查的 ready 不標記：使用者本來就會到審查分頁處理。
MARK_STYLES: dict[str, tuple[str, str]] = {
    SmartReviewStatus.APPLIED: ("校對：已套用", "dia"),
    SmartReviewStatus.REJECTED: ("校對：保留原譯文", "neutral"),
    SmartReviewStatus.NO_CHANGE: ("AI：不用改", "neutral"),
    SmartReviewStatus.NEEDS_CONTEXT: ("AI：需補 context", "gold"),
    SmartReviewStatus.INVALID: ("校對：無效", "red"),
    SmartReviewStatus.INVALID_FORMAT: ("校對：格式不符", "red"),
    SmartReviewStatus.STALE: ("校對：已過期", "gold"),
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


ALL_SMART_REVIEW = "__all__"
# 條目篩選「智慧校對」下拉選項：(EntryFilter.smart_review 的值, 顯示名稱)；依最近一次批次判斷。
SMART_REVIEW_FILTER_OPTIONS: tuple[tuple[str, str], ...] = (
    (ALL_SMART_REVIEW, "全部"),
    ("any", "有處理結果"),
    ("applied", "已套用"),
    ("invalid", "無效／格式不符"),
    ("stale", "已過期"),
    ("needs_context", "需補 context"),
    ("no_change", "AI 認為不用改"),
    ("rejected", "已保留原譯文"),
)
