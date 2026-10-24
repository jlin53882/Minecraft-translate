"""批次事件 schema 契約：canonical 欄位與版本、舊欄位 adapter、舊歷史事件仍可格式化。"""

from __future__ import annotations

import dataclasses
import re

import pytest

from translation_tool.core.batch_events import (
    BATCH_EVENT_SCHEMA_VERSION,
    EVENT_CORE_FIELDS,
    FORMATTED_BATCH_EVENT_KINDS,
    LEGACY_FIELD_ALIASES,
    TIMING_FIELDS,
    BatchEvent,
    BatchTiming,
    batch_event_level,
    canonicalize_event,
    format_batch_event,
    to_legacy_event,
    with_legacy_fields,
)
from translation_tool.core.batch_timing import LEGACY_SECONDS_ALIASES
from translation_tool.core.common_batch_executor import (
    BatchMetrics,
    CommonBatchExecutor,
)
from translation_tool.core.common_batch_executor import (
    format_batch_event as executor_format_batch_event,
)
from translation_tool.translation_db.smart_review_run_state import (
    SmartReviewRunState,
    new_smart_review_metrics,
)

LEGACY_EVENT_KEYS = {
    name for names in LEGACY_FIELD_ALIASES.values() for name in names
} | {name for names in LEGACY_SECONDS_ALIASES.values() for name in names}


# -- schema 欄位與版本 ---------------------------------------------------------------


def test_schema_version_is_a_positive_int_and_stamped_on_events():
    assert (
        isinstance(BATCH_EVENT_SCHEMA_VERSION, int) and BATCH_EVENT_SCHEMA_VERSION >= 2
    )
    event = BatchEvent(
        task_id="t", run_id="r", operation="op", provider="p", model="m", phase="api"
    ).as_dict()
    assert event["schema_version"] == BATCH_EVENT_SCHEMA_VERSION
    assert set(EVENT_CORE_FIELDS) <= set(event)


def test_core_event_has_only_canonical_field_names():
    """core 產生的事件不含任何舊欄位；舊欄位只在輸出邊界補。"""
    executor = CommonBatchExecutor(operation="smart_review")
    handle = executor.begin_batch(
        batch_index=1, batch_total=2, batch_size=3, selected_count=6, processed_count=0
    )
    event = handle.emit("attempt_start", expected_count=3)
    assert LEGACY_EVENT_KEYS.isdisjoint(event)
    assert event["batch_index"] == 1 and event["attempt"] == 0


def test_timing_fields_are_milliseconds_and_match_the_dataclass():
    assert all(name.endswith("_ms") for name in TIMING_FIELDS)
    assert [f.name for f in dataclasses.fields(BatchTiming)] == list(TIMING_FIELDS)
    assert set(TIMING_FIELDS) <= set(LEGACY_SECONDS_ALIASES) | {
        "validation_ms",
        "commit_ms",
    }


def test_legacy_aliases_never_collide_with_canonical_names():
    canonical = (
        set(EVENT_CORE_FIELDS)
        | set(TIMING_FIELDS)
        | set(LEGACY_SECONDS_ALIASES)
        | set(LEGACY_FIELD_ALIASES)
    )
    assert canonical.isdisjoint(LEGACY_EVENT_KEYS)
    legacy_names = [n for names in LEGACY_FIELD_ALIASES.values() for n in names]
    assert len(legacy_names) == len(set(legacy_names))


# -- BatchTiming ---------------------------------------------------------------------


def test_batch_timing_add_rounds_clamps_and_serialises():
    timing = BatchTiming()
    assert timing.add("api_ms", 1499.6) == 1500
    assert timing.add_seconds("api_ms", 0.25) == 250
    assert timing.add("api_ms", -5) == 0
    assert timing.api_ms == 1750
    assert timing.to_event()["api_ms"] == 1750
    assert set(timing.to_event()) == set(TIMING_FIELDS)
    with pytest.raises(AttributeError):
        timing.add("api_seconds", 1)
    other = BatchTiming(api_ms=10, quota_wait_ms=5)
    timing.merge(other)
    assert (timing.api_ms, timing.quota_wait_ms) == (1760, 5)


def test_batch_metrics_is_a_batch_timing_with_counters():
    metrics = BatchMetrics()
    metrics.add_ms("retry_backoff_ms", 30000)
    snapshot = metrics.as_dict()
    assert isinstance(metrics, BatchTiming)
    assert snapshot["retry_backoff_ms"] == 30000
    assert {"batch_count", "request_attempts", "error_batches"} <= set(snapshot)


# -- 舊欄位 adapter -------------------------------------------------------------------


def test_to_legacy_event_adds_old_names_without_overwriting():
    event = to_legacy_event(
        {
            "kind": "retry_wait",
            "batch_index": 2,
            "attempt": 3,
            "attempts": 4,
            "wait_ms": 30000,
            "retry_after_ms": 1500,
            "api_ms": 2500,
            "overload": True,
            "rate_limit": False,
            "suggestion_count": 5,
            "batch_number": 99,
        }
    )
    assert event["smart_review_attempt"] == 3
    assert event["smart_review_attempts"] == 4
    assert event["wait_seconds"] == 30.0
    assert event["retry_after_seconds"] == 1.5
    assert event["api_seconds"] == 2.5 and event["api_elapsed_seconds"] == 2.5
    assert event["provider_overload"] is True
    assert event["quota_or_rate_limit"] is False
    assert event["suggest_revision_count"] == 5
    assert event["batch_number"] == 99  # 既有值不被覆蓋
    assert event["wait_ms"] == 30000  # canonical 欄位保留


def test_to_legacy_event_restores_resume_elapsed_seconds():
    event = to_legacy_event(
        {"kind": "retry_resume", "waited_ms": 30000, "elapsed_ms": 31000}
    )

    assert event["elapsed_seconds"] == 30.0
    assert event["waited_ms"] == 30000
    assert event["elapsed_ms"] == 31000


def test_with_legacy_fields_skips_missing_values():
    assert with_legacy_fields({"attempt": None}) == {"attempt": None}


def test_canonicalize_event_reads_old_history_events():
    event = canonicalize_event(
        {
            "kind": "attempt_success",
            "batch_number": 1,
            "smart_review_attempt": 2,
            "api_elapsed_seconds": 0.2,
            "wait_seconds": 30,
            "retry_after_seconds": None,
            "provider_overload": True,
            "elapsed_seconds": 0.8,
        }
    )
    assert event["batch_index"] == 1 and event["attempt"] == 2
    assert event["api_ms"] == 200 and event["wait_ms"] == 30000
    assert event["overload"] is True and event["elapsed_ms"] == 800
    assert "retry_after_ms" not in event


def test_canonicalize_keeps_existing_canonical_values():
    event = canonicalize_event({"api_ms": 100, "api_seconds": 9, "attempt": 1})
    assert event["api_ms"] == 100 and event["attempt"] == 1


def test_resume_events_keep_waited_time_separate_from_batch_elapsed():
    event = canonicalize_event(
        {"kind": "retry_resume", "elapsed_ms": 5000, "elapsed_seconds": 2.5}
    )
    assert event["elapsed_ms"] == 5000
    assert event["waited_ms"] == 2500


# -- run state：core 事件為 canonical，進度回呼為 canonical + 舊欄位 --------------------------


def test_run_state_emits_canonical_events_and_adapts_only_at_progress_boundary():
    sink, progress = [], []
    executor = CommonBatchExecutor(operation="smart_review", event_sink=sink.append)
    state = SmartReviewRunState(
        db=None,
        run_id="run-1",
        executor=executor,
        selected_count=2,
        planned_batch_count=1,
        progress=progress.append,
        metrics=new_smart_review_metrics(input_token_budget=None),
        started=0.0,
    )
    batch = state.begin_batch(
        number=1,
        size=2,
        ordinal_start=1,
        ordinal_end=2,
        provider="gemini",
        model_name="m",
    )
    state.bump("attempts")
    state.emit_batch_event(batch, "attempt_start", 1, expected_count=2)
    state.add_timing_seconds(batch, "api_ms", 1.5)
    state.emit_batch_event(batch, "attempt_success", 1, api_ms=1500, http_status=200)
    state.complete_batch(batch, "completed")

    assert {"attempt_start", "attempt_success", "batch_complete"} <= {
        e["kind"] for e in sink
    }
    assert all(LEGACY_EVENT_KEYS.isdisjoint(event) for event in sink)
    assert all(event["schema_version"] == BATCH_EVENT_SCHEMA_VERSION for event in sink)

    callback_events = [
        snapshot["event"] for snapshot in progress if "event" in snapshot
    ]
    start = next(e for e in callback_events if e["kind"] == "attempt_start")
    assert start["attempt"] == 1 and start["smart_review_attempt"] == 1
    assert start["batch_index"] == 1 and start["batch_number"] == 1
    done = next(e for e in callback_events if e["kind"] == "batch_complete")
    assert done["api_ms"] == 1500 and done["api_seconds"] == 1.5
    assert done["attempts"] == 1 and done["smart_review_attempts"] == 1
    timing = next(s["timing"] for s in progress if s["phase"] == "batch_complete")
    assert timing["api_ms"] == 1500 and timing["api_seconds"] == 1.5
    assert executor.metrics.api_ms == 1500  # 批次與整體共用同一個累加入口


def test_run_state_progress_carries_resume_baseline_and_pending_total():
    """續跑進度需把歷史基準與本次待處理總數傳到 UI。"""
    progress = []
    state = SmartReviewRunState(
        db=None,
        run_id="run-resume",
        executor=CommonBatchExecutor(operation="smart_review"),
        selected_count=100,
        planned_batch_count=1,
        progress=progress.append,
        metrics=new_smart_review_metrics(input_token_budget=None),
        started=0.0,
        resume_baseline_processed=40,
        resume_pending_total=60,
    )

    state.report_progress(phase="selection_complete")

    assert progress[-1]["resume_baseline_processed"] == 40
    assert progress[-1]["resume_pending_total"] == 60


# -- 唯一的 formatter ------------------------------------------------------------------

OLD_HISTORY_EVENTS = [
    {
        "kind": "retry_wait",
        "batch_number": 1,
        "batch_total": 2,
        "smart_review_attempt": 1,
        "retry_number": 1,
        "retry_total": 3,
        "wait_seconds": 30,
        "error_kind": "service_unavailable",
        "http_status": 503,
        "retry_after_seconds": None,
    },
    {
        "kind": "attempt_success",
        "batch_number": 1,
        "batch_total": 2,
        "smart_review_attempt": 2,
        "api_elapsed_seconds": 0.2,
        "http_status": 200,
        "expected_count": 1,
        "actual_count": 1,
    },
    {
        "kind": "attempt_failure",
        "batch_number": 1,
        "batch_total": 2,
        "smart_review_attempt": 1,
        "http_status": 503,
        "error_kind": "service_unavailable",
        "provider_overload": True,
        "quota_or_rate_limit": False,
        "retry_after_seconds": 12,
        "key_rotated": False,
    },
    {
        "kind": "batch_complete",
        "batch_number": 1,
        "batch_total": 2,
        "ordinal_start": 1,
        "ordinal_end": 2,
        "actual_count": 2,
        "smart_review_attempts": 1,
        "retry_count": 1,
        "rate_limit_wait_seconds": 0.1,
        "retry_wait_seconds": 30,
        "api_seconds": 0.6,
        "parse_seconds": 0.02,
        "result_write_seconds": 0.01,
        "elapsed_seconds": 31.0,
        "valid_count": 2,
        "invalid_count": 0,
    },
]


@pytest.mark.parametrize("old", OLD_HISTORY_EVENTS, ids=lambda e: e["kind"])
def test_old_history_events_format_identically_to_their_canonical_form(old):
    canonical = canonicalize_event(old)
    assert format_batch_event(old, default_operation="smart_review") == (
        format_batch_event(canonical, default_operation="smart_review")
    )
    text = format_batch_event(old, default_operation="smart_review")
    assert text.startswith("[智慧校對][批次 1/2]")


def test_old_history_event_text_matches_the_user_visible_log():
    retry = format_batch_event(OLD_HISTORY_EVENTS[0], default_operation="smart_review")
    assert "[智慧校對][批次 1/2][第 1 次嘗試]" in retry
    assert "第 1/3 次重試" in retry and "等待 30s" in retry
    done = format_batch_event(OLD_HISTORY_EVENTS[3], default_operation="smart_review")
    assert "API 600ms" in done and "重試等待 30s" in done and "本批 31s" in done


def test_formatter_never_prints_raw_milliseconds():
    events = [
        {"kind": "retry_wait", "attempt": 1, "wait_ms": 30000},
        {"kind": "attempt_success", "attempt": 1, "api_ms": 12345, "elapsed_ms": 99999},
        {"kind": "attempt_failure", "attempt": 1, "retry_after_ms": 45000},
        {"kind": "rate_limit_wait", "attempt": 1, "wait_ms": 8000},
        {"kind": "network_retry_wait", "attempt": 1, "wait_ms": 2000},
        {"kind": "timing", "field_name": "api_ms", "elapsed_ms": 70000},
    ]
    for event in events:
        text = format_batch_event(event)
        assert not re.search(r"\d{4,}", text), text


def test_one_formatter_is_shared_with_the_executor_module():
    assert executor_format_batch_event is format_batch_event


def test_unknown_kinds_fall_back_and_levels_follow_severity():
    assert "weird_kind" in format_batch_event({"kind": "weird_kind"})
    assert "weird_kind" not in FORMATTED_BATCH_EVENT_KINDS
    assert batch_event_level({"kind": "retry_wait"}) == "warning"
    assert batch_event_level({"kind": "attempt_start"}) == "info"
    assert (
        batch_event_level({"kind": "batch_complete", "invalid_count": 1}) == "warning"
    )
    assert batch_event_level({"kind": "batch_complete", "invalid_count": 0}) == "info"
    assert (
        batch_event_level(
            {"kind": "validation_repair_complete", "repaired_invalid_count": 2}
        )
        == "warning"
    )


def test_initial_metrics_contain_only_canonical_fields():
    """初始 metrics 不得預放舊欄位別名，否則輸出邊界的別名（只補缺值）永遠不會更新。"""
    from translation_tool.core.batch_events import LEGACY_FIELD_ALIASES
    from translation_tool.translation_db.smart_review_run_state import (
        new_smart_review_metrics,
    )

    legacy_names = {
        name for names in LEGACY_FIELD_ALIASES.values() for name in names
    } | {name for names in LEGACY_SECONDS_ALIASES.values() for name in names}
    assert (
        set(new_smart_review_metrics(input_token_budget=None)) & legacy_names == set()
    )


def test_persisted_metrics_legacy_aliases_follow_canonical_values():
    """持久化 metrics 的舊別名必須等於 canonical 值（含建議筆數大於 0 的情況）。"""
    from translation_tool.core.batch_events import to_legacy_event
    from translation_tool.translation_db.smart_review_run_state import (
        new_smart_review_metrics,
    )

    metrics = new_smart_review_metrics(input_token_budget=None)
    metrics.update(suggestion_count=7, attempts=4)
    metrics.update(to_legacy_event(metrics))
    assert metrics["suggest_revision_count"] == 7
    assert metrics["smart_review_attempts"] == 4


def test_provider_attempt_failure_uses_yes_no_flags():
    """provider 失敗訊息的旗標與其他 formatter 一致，用「是／否」而不是 Python 的 True／False。"""
    text = format_batch_event(
        {
            "kind": "provider_attempt_failure",
            "operation": "translate",
            "timeout": False,
            "overload": True,
            "rate_limit": False,
        }
    )
    assert "逾時=否" in text and "過載=是" in text and "速率限制=否" in text
    assert "True" not in text and "False" not in text


def test_smart_review_run_state_timing_method_name_states_the_unit():
    """SmartReviewRunState 以秒計；方法名必須帶單位，避免與毫秒的 add_timing 混用。"""
    from translation_tool.translation_db.smart_review_run_state import (
        SmartReviewRunState,
    )

    assert hasattr(SmartReviewRunState, "add_timing_seconds")
    assert not hasattr(SmartReviewRunState, "add_timing")
