"""條目列智慧校對狀態標籤的顯示測試。"""

from __future__ import annotations

from app.services_impl.moddb_smart_review_service import SmartReviewMark
from app.views.moddb.smart_review_marks import MARK_STYLES, smart_review_mark_chip


def test_chip_shows_label_and_tooltip_for_each_reviewed_status():
    """每個處理結果狀態都有標籤；tooltip 帶批次與驗證原因。"""
    for status in MARK_STYLES:
        assert smart_review_mark_chip(SmartReviewMark("abcdef123456", status))
    chip = smart_review_mark_chip(
        SmartReviewMark("abcdef123456", "invalid", "reason 不可為空")
    )
    assert "abcdef12" in chip.tooltip
    assert "reason 不可為空" in chip.tooltip


def test_chip_is_hidden_for_ready_and_missing_marks():
    """待審查（ready）與沒有標記時不顯示膠囊。"""
    assert smart_review_mark_chip(None) is None
    assert smart_review_mark_chip(SmartReviewMark("abcdef123456", "ready")) is None


def test_filter_options_match_service_groups():
    """下拉選項的值必須與服務層的狀態群組一一對應，避免選了卻被拒絕。"""
    from app.views.moddb.smart_review_marks import (
        ALL_SMART_REVIEW,
        SMART_REVIEW_FILTER_OPTIONS,
    )
    from translation_tool.translation_db.models import SMART_REVIEW_FILTER_GROUPS

    values = {value for value, _label in SMART_REVIEW_FILTER_OPTIONS}
    assert values == {ALL_SMART_REVIEW, *SMART_REVIEW_FILTER_GROUPS}
