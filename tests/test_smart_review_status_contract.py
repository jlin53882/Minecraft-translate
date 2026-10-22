"""智慧校對各層級狀態與標籤的契約：全集覆蓋、同語意同標籤、不同語意不硬併。"""

from __future__ import annotations

from app.services_impl.moddb_source_service import (
    REVIEW_BADGE_LABELS,
    REVIEW_STATUS_LABELS,
)
from app.views.moddb.smart_review_batch_outcome import (
    BatchOutcome,
    BatchOutcomeGroup,
    classify_outcome,
)
from app.views.moddb.smart_review_marks import (
    MARK_STYLES,
    SMART_REVIEW_FILTER_OPTIONS,
)
from app.views.moddb.smart_review_reviewer_state import ALL_FILTER, FILTERS
from app.views.moddb.smart_review_run_status import (
    RUN_STATUS_STYLES,
    SmartReviewRunStatus,
    run_status_color,
    run_status_label,
)
from translation_tool.translation_db.smart_review_status import SmartReviewStatus

# 條目列不顯示標記的候選狀態：ready 由審查分頁處理，其餘是批次內部過渡狀態。
_UNMARKED = {
    SmartReviewStatus.PENDING,
    SmartReviewStatus.READY,
    SmartReviewStatus.REQUEST_FAILED,
    SmartReviewStatus.CANCELLED,
}
# 審查頁篩選器不列出的候選狀態：尚未產生結果或沒有可審查內容的過渡狀態。
_NOT_FILTERABLE = {
    SmartReviewStatus.PENDING,
    SmartReviewStatus.REQUEST_FAILED,
    SmartReviewStatus.CANCELLED,
}


def test_candidate_status_mark_styles_cover_every_member():
    """每個候選狀態不是有標記樣式，就是明確列在不標記清單。"""
    assert set(MARK_STYLES) | _UNMARKED == set(SmartReviewStatus)
    assert not set(MARK_STYLES) & _UNMARKED


def test_candidate_status_filters_cover_every_member():
    """審查頁篩選器涵蓋所有可審查候選狀態，並保留「全部」。"""
    filtered = {key for key, _label in FILTERS}
    assert ALL_FILTER in filtered
    assert filtered - {ALL_FILTER} | _NOT_FILTERABLE == set(SmartReviewStatus)


def test_entry_filter_options_only_use_known_mark_statuses():
    """條目篩選的智慧校對選項（除 any／全部外）都是真實候選狀態。"""
    values = {value for value, _label in SMART_REVIEW_FILTER_OPTIONS}
    known = {str(status) for status in SmartReviewStatus}
    assert values - {"__all__", "any"} <= known


def test_run_status_styles_cover_every_member_and_core_run_statuses():
    """批次層級狀態集中一份標籤，且涵蓋資料層允許的 run 結束狀態。"""
    assert set(RUN_STATUS_STYLES) == set(SmartReviewRunStatus)
    for status in SmartReviewRunStatus:
        label, tone = run_status_label(status)
        assert label
        assert tone in {"em", "gold", "red", "neutral"}
        assert run_status_color(status)


def test_cancelled_run_is_gold_everywhere_and_unknown_falls_back():
    """已取消在執行頁、歷史頁、提示訊息使用同一色調；未知狀態以原字串顯示。"""
    assert run_status_label("cancelled") == ("已取消", "gold")
    assert run_status_label("cancelled", active=False) == ("已取消", "gold")
    assert run_status_color("cancelled") == run_status_color("partial")
    assert run_status_label("weird") == ("weird", "neutral")
    assert run_status_label("") == ("未知", "neutral")
    assert run_status_label("running", active=False) == ("可能已中斷", "red")


def test_run_status_literals_match_core_finish_statuses():
    """資料層 finish_run 允許的字面值都在批次層級列舉內。"""
    import inspect

    from translation_tool.translation_db import smart_review_runs

    source = inspect.getsource(smart_review_runs)
    for status in ("completed", "partial", "failed", "interrupted"):
        assert f'"{status}"' in source
        assert status in {item.value for item in SmartReviewRunStatus}
    assert SmartReviewStatus.CANCELLED.value in {
        item.value for item in SmartReviewRunStatus
    }


def test_batch_outcomes_classify_into_exactly_one_group():
    """六種主要 apply outcome 與其餘已知字面值都有明確群組。"""
    expected = {
        "applied": BatchOutcomeGroup.APPLIED,
        "rejected": BatchOutcomeGroup.REJECTED,
        "error": BatchOutcomeGroup.ERROR,
        "not_ready": BatchOutcomeGroup.SKIPPED,
        "missing": BatchOutcomeGroup.SKIPPED,
        "manual_reviewed": BatchOutcomeGroup.SKIPPED,
    }
    for status, group in expected.items():
        assert classify_outcome(status) is group
    for outcome in BatchOutcome:
        assert isinstance(classify_outcome(outcome), BatchOutcomeGroup)
    assert classify_outcome("never_seen") is BatchOutcomeGroup.SKIPPED


def test_entry_review_status_labels_are_a_separate_semantic_layer():
    """條目生效審核狀態（未審核／人工已審核）與候選、批次狀態語意不同，不共用字面值。"""
    assert set(REVIEW_STATUS_LABELS) == {"unreviewed", "reviewed", "legacy_unknown"}
    # 歷史狀態與人工已審核使用者看到的是同一件事，標籤一致；未審核不另外顯示成徽章。
    assert REVIEW_STATUS_LABELS["legacy_unknown"] == REVIEW_STATUS_LABELS["reviewed"]
    assert set(REVIEW_BADGE_LABELS) == {"reviewed", "legacy_unknown"}
    # 候選狀態的「已套用／待審查」是另一層，不得意外混入條目審核標籤。
    assert not set(REVIEW_STATUS_LABELS) & {str(s) for s in SmartReviewStatus}
    assert not set(REVIEW_STATUS_LABELS) & {s.value for s in SmartReviewRunStatus}


def test_run_level_views_share_one_status_source():
    """執行頁、歷史頁與背景任務都引用共用的批次狀態標籤，不各自維護字典。"""
    from app.views.moddb import (
        smart_review_history,
        smart_review_job,
        smart_review_run_panel,
    )

    for module in (smart_review_history, smart_review_job, smart_review_run_panel):
        assert module.run_status_label is run_status_label
        assert not hasattr(module, "_STATUS")
        assert not hasattr(module, "_STATUS_LABEL")
        assert not hasattr(module, "_RESULT_LABEL")
