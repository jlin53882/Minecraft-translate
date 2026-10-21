"""測試 Smart Review 策略診斷腳本的離線分析行為。"""

from __future__ import annotations

from tools.smart_review_strategy_probe import (
    _batch_plan,
    _history_summary,
    _offline_strategy_report,
    _select_sample,
)


def _history() -> dict[str, object]:
    """建立不需要 provider 的最小歷史資料。"""
    candidates = [
        {
            "ordinal": 1,
            "status": "invalid",
            "validation_error": "reason 不可為空",
            "context": [],
        },
        {
            "ordinal": 2,
            "status": "invalid",
            "validation_error": "suggest_revision 的 reason 必須指出可核對的原文或目前譯文差異",
            "context": [{"id": "x"}],
        },
        {
            "ordinal": 3,
            "status": "ready",
            "validation_error": None,
            "context": [{"id": "x"}, {"id": "y"}],
        },
    ]
    return {
        "id": "run-test",
        "provider": "gemini",
        "model": "test-model",
        "status": "completed",
        "selected_count": 3,
        "processed_count": 3,
        "batch_size": 40,
        "metrics": {
            "batch_count": 1,
            "request_attempts": 1,
            "response_invalid_count": 0,
        },
        "candidates": candidates,
    }


def test_history_summary_keeps_invalid_error_distribution() -> None:
    """離線摘要應保留主要錯誤類別與上下文範圍。"""
    summary = _history_summary(_history())

    assert summary["selected_count"] == 3
    assert summary["invalid_count"] == 2
    assert summary["reason_empty_count"] == 1
    assert summary["validation_error_categories"]["reason 為空"] == 1
    assert summary["context_count"] == {"min": 0, "max": 2, "average": 1.0}


def test_offline_report_does_not_treat_relaxed_validation_as_safe() -> None:
    """放寬 reason 只能作診斷上限，不可被報告為可安全套用。"""
    summary = _history_summary(_history())
    report = _offline_strategy_report(summary, [2, 40])

    relaxed = report["relax_reason_validation_diagnostic_only"]
    assert relaxed["potentially_accepted_upper_bound"] == 1
    assert relaxed["safe_to_apply"] is False
    assert (
        report["invalid_only_repair"]["request_plan_by_batch_size"][0][
            "planned_batches"
        ]
        == 1
    )


def test_sample_selection_is_deterministic_and_stratified() -> None:
    """相同歷史與大小應選出可重現且包含不同狀態的樣本。"""
    history = _history()
    candidates = history["candidates"]
    assert isinstance(candidates, list)

    first = _select_sample(candidates, 2, "stratified")
    second = _select_sample(candidates, 2, "stratified")

    assert [item["ordinal"] for item in first] == [item["ordinal"] for item in second]
    assert {item["status"] for item in first} == {"invalid", "ready"}


def test_batch_plan_handles_partial_last_batch() -> None:
    """批次規劃應正確計算最後一批的實際數量。"""
    assert _batch_plan(81, 40) == {
        "batch_size": 40,
        "planned_batches": 3,
        "last_batch_size": 1,
    }
