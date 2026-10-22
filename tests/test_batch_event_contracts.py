"""批次事件接收與計時的契約：log 等級、等待量、API 時間與 callback 傳遞。"""

from __future__ import annotations

import logging

import pytest

from translation_tool.core import lm_translator_shared_loop as shared_loop
from translation_tool.core.batch_events import (
    FORMATTED_BATCH_EVENT_KINDS,
    format_batch_event_for_log,
)
from translation_tool.core.common_batch_executor import (
    BatchMetrics,
    CommonBatchExecutor,
)
from translation_tool.utils.cancellation import TaskCancelled

# ---------------------------------------------------------------------------
# M5：事件 -> log 等級與顯示規則
# ---------------------------------------------------------------------------


def _event(kind: str, **fields):
    return {"kind": kind, "operation": "translate", "batch_index": 1, **fields}


@pytest.mark.parametrize(
    ("event", "level"),
    [
        (_event("attempt_failure", http_status=503), "warning"),
        (_event("retry_wait", wait_ms=1000), "warning"),
        (_event("network_failure"), "warning"),
        (_event("network_retry_wait", wait_ms=1000), "warning"),
        (_event("provider_attempt_failure"), "warning"),
        (_event("batch_complete", invalid_count=2), "warning"),
        (_event("batch_complete", invalid_count=0), "info"),
        (_event("attempt_start"), "info"),
        (_event("retry_resume", waited_ms=1000), "info"),
        (_event("rate_limit_wait", wait_ms=1000), "info"),
    ],
)
def test_known_kinds_use_the_shared_level_rules(event, level):
    entry = format_batch_event_for_log(event)
    assert entry is not None
    assert entry[0] == level
    assert entry[1]


@pytest.mark.parametrize("kind", ["cancelled", "provider_event", "something_new"])
def test_unknown_kinds_are_not_shown_to_the_user(kind):
    assert kind not in FORMATTED_BATCH_EVENT_KINDS
    assert format_batch_event_for_log(_event(kind)) is None


def test_translate_and_repair_sinks_share_the_rules(monkeypatch):
    from app.services_impl import moddb_retranslate_service as repair
    from app.services_impl import moddb_translate_service as translate

    for module, sink_fn in (
        (translate, translate._translation_event_sink),
        (repair, repair._repair_event_sink),
    ):
        logged: list[tuple[str, str]] = []
        monkeypatch.setattr(
            module,
            "_log",
            lambda _s, text, level="info", out=logged: out.append((level, text)),
        )
        sink_fn(object(), _event("network_failure"))
        sink_fn(object(), _event("provider_attempt_failure"))
        sink_fn(object(), _event("batch_complete", invalid_count=1))
        sink_fn(object(), _event("cancelled", status="cancelled"))
        sink_fn(object(), _event("attempt_start"))
        assert [level for level, _ in logged] == [
            "warning",
            "warning",
            "warning",
            "info",
        ], module.__name__


# ---------------------------------------------------------------------------
# M7：等待總額只有一個權威計算入口
# ---------------------------------------------------------------------------


def _handle():
    executor = CommonBatchExecutor(operation="translate", event_sink=lambda _e: None)
    handle = executor.begin_batch(
        batch_index=1, batch_total=1, batch_size=1, selected_count=1, processed_count=0
    )
    return executor, handle


def test_total_wait_ms_is_the_sum_of_the_three_wait_buckets():
    metrics = BatchMetrics(
        quota_wait_ms=100, retry_backoff_ms=20, network_retry_wait_ms=3, api_ms=999
    )
    assert metrics.total_wait_ms == 123


def test_request_timing_subtracts_waits_exactly_once(monkeypatch):
    clock = iter([0.0, 10.0])
    monkeypatch.setattr(
        "translation_tool.core.common_batch_executor.time.monotonic",
        lambda: next(clock),
    )
    executor, handle = _handle()
    with handle.timed_request() as request:
        handle.forward_provider_event({"kind": "rate_limit_wait", "wait_ms": 1000})
        handle.forward_provider_event({"kind": "retry_wait", "wait_ms": 2000})
        handle.forward_provider_event({"kind": "network_retry_wait", "wait_ms": 500})
    assert request.request_ms == 10000
    assert request.wait_ms == 3500
    assert request.api_ms == 6500
    # 成功時由 attempt_success 累加 api_ms；等待量已在各自欄位，不會再被算進 api_ms。
    handle.attempt_success(elapsed_ms=request.api_ms)
    assert executor.metrics.api_ms == 6500
    assert executor.metrics.total_wait_ms == 3500


def test_request_timing_is_recorded_when_the_request_fails_or_is_cancelled(
    monkeypatch,
):
    for error in (RuntimeError("boom"), TaskCancelled()):
        clock = iter([0.0, 4.0])
        monkeypatch.setattr(
            "translation_tool.core.common_batch_executor.time.monotonic",
            lambda clock=clock: next(clock),
        )
        executor, handle = _handle()
        with pytest.raises(type(error)), handle.timed_request():
            handle.forward_provider_event({"kind": "rate_limit_wait", "wait_ms": 1500})
            raise error
        assert executor.metrics.api_ms == 2500, type(error).__name__
        assert executor.metrics.quota_wait_ms == 1500


# ---------------------------------------------------------------------------
# L2 / L3：等待量不誤用批次 elapsed；非法計時欄位不丟例外
# ---------------------------------------------------------------------------


def test_retry_wait_without_wait_ms_does_not_borrow_batch_elapsed():
    executor, handle = _handle()
    handle.forward_provider_event({"kind": "retry_wait", "elapsed_ms": 777})
    handle.forward_provider_event({"kind": "rate_limit_wait", "elapsed_ms": 777})
    handle.forward_provider_event({"kind": "network_retry_wait", "elapsed_ms": 777})
    assert executor.metrics.retry_backoff_ms == 0
    assert executor.metrics.quota_wait_ms == 0
    assert executor.metrics.network_retry_wait_ms == 0


def test_wait_events_still_read_wait_ms_and_the_legacy_seconds_alias():
    executor, handle = _handle()
    handle.forward_provider_event({"kind": "retry_wait", "wait_ms": 1200})
    handle.forward_provider_event({"kind": "rate_limit_wait", "wait_seconds": 2.5})
    assert executor.metrics.retry_backoff_ms == 1200
    assert executor.metrics.quota_wait_ms == 2500


def test_timing_event_uses_its_own_elapsed_value():
    executor, handle = _handle()
    handle.forward_provider_event(
        {"kind": "timing", "field_name": "prompt_build_ms", "elapsed_ms": 777}
    )
    assert executor.metrics.prompt_build_ms == 777


def test_unknown_timing_field_is_recorded_not_raised_and_not_silent(caplog):
    executor, handle = _handle()
    before = executor.metrics.as_dict()
    with caplog.at_level(logging.WARNING):
        event = handle.forward_provider_event(
            {"kind": "timing", "field_name": "no_such_ms", "elapsed_ms": 5}
        )
    assert event["kind"] == "timing"
    assert executor.metrics.as_dict() == before
    assert any("no_such_ms" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# M8：共用的 invoke_translation_batch
# ---------------------------------------------------------------------------


def test_invoke_forwards_callback_and_supports_keyword_only_total():
    seen = {}

    def fake(batch, *, total=None, event_callback=None):
        seen.update(batch=batch, total=total, cb=event_callback)
        return ["ok"], "AUTO"

    callback = object()
    assert shared_loop.invoke_translation_batch(fake, [1], 7, callback) == (
        ["ok"],
        "AUTO",
    )
    assert seen == {"batch": [1], "total": 7, "cb": callback}


def test_invoke_passes_total_positionally_for_legacy_signatures():
    calls = []

    def positional(batch, total):
        calls.append((batch, total))
        return [], "AUTO"

    shared_loop.invoke_translation_batch(positional, [1], 3, event_callback=None)
    shared_loop.invoke_translation_batch(positional, [2], 4, event_callback=print)
    assert calls == [([1], 3), ([2], 4)]


def test_invoke_warns_when_telemetry_would_be_dropped(caplog):
    def no_callback(batch, total):
        return [], "AUTO"

    with caplog.at_level(logging.WARNING):
        shared_loop.invoke_translation_batch(no_callback, [1], 1, event_callback=print)
    assert any("event_callback" in record.getMessage() for record in caplog.records)


def test_app_services_use_the_shared_invoke(monkeypatch):
    from app.services_impl import moddb_retranslate_service as repair
    from app.services_impl import moddb_translate_service as translate

    for module in (translate, repair):
        assert module.invoke_translation_batch is shared_loop.invoke_translation_batch


# ---------------------------------------------------------------------------
# L5：建立 run record 的耗時不算成「寫入 JSON」
# ---------------------------------------------------------------------------


def test_run_record_time_is_not_counted_as_history_write(monkeypatch):
    from types import SimpleNamespace

    from translation_tool.translation_db import smart_review_run

    ticks = iter([100.0, 100.0, 100.0, 101.5])
    monkeypatch.setattr(
        smart_review_run,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks, 101.5)),
    )

    class _Db:
        def create_smart_review_run(self, *_args, **_kwargs):
            return "run-1"

    session = SimpleNamespace(
        provider="gemini",
        model_name="m",
        cancel_check=None,
        input_token_budget=None,
        configure_limits=lambda: None,
    )
    state = smart_review_run._open_run_state(
        _Db(),
        session,
        [object()],
        batch_size=1,
        limit=1,
        version=None,
        scope={},
        task_id="t",
        progress=None,
        workflow_started=100.0,
        selection_started=100.0,
        selection_seconds=0.0,
    )
    metrics = state.executor.metrics
    assert metrics.run_record_ms == 1500
    assert metrics.history_write_ms == 0
