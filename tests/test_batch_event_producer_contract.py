"""真實 producer 契約：實際執行發出事件的函式，而不是手工 emit。

core 產生的事件只含 canonical 欄位（毫秒 ``*_ms``、``overload``／``rate_limit``），不含任何舊欄位；
舊欄位只在輸出邊界由 ``to_legacy_event`` 補上，舊歷史資料仍可經 ``canonicalize_event`` 讀取。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from tests.test_batch_event_schema import LEGACY_EVENT_KEYS
from translation_tool.core import lm_api_client as client
from translation_tool.core import lm_translator_main as main
from translation_tool.core.batch_events import (
    canonicalize_event,
    format_batch_event,
    to_legacy_event,
)
from translation_tool.core.common_batch_executor import CommonBatchExecutor
from translation_tool.core.lm_key_health import get_model_quota_registry
from translation_tool.core.openai_codex_client import ChatGPTAPIError


def _assert_canonical(events: list[dict]) -> None:
    assert events, "producer 沒有發出任何事件"
    for event in events:
        leaked = LEGACY_EVENT_KEYS & set(event)
        assert not leaked, f"{event['kind']} 事件含舊欄位 {leaked}"


def _handle():
    executor = CommonBatchExecutor(operation="translate", event_sink=lambda _e: None)
    handle = executor.begin_batch(
        batch_index=1, batch_total=1, batch_size=1, selected_count=1, processed_count=0
    )
    return executor, handle


def test_sleep_with_runtime_event_emits_canonical_ms_and_waited_ms():
    events: list[dict] = []
    clock = iter([0.0, 2.0])
    with (
        patch.object(main, "interruptible_sleep"),
        patch.object(main.time, "monotonic", lambda: next(clock)),
    ):
        main._sleep_with_runtime_event(
            SimpleNamespace(), 1.5, reason="x", callback=events.append
        )
    _assert_canonical(events)
    wait, resume = events
    assert wait["kind"] == "retry_wait" and wait["wait_ms"] == 1500
    assert resume["kind"] == "retry_resume"
    assert resume["wait_ms"] == 1500 and resume["waited_ms"] == 2000


def test_forward_provider_event_accepts_producer_events_and_resume_keeps_both_times():
    sink: list[dict] = []
    executor = CommonBatchExecutor(operation="translate", event_sink=sink.append)
    handle = executor.begin_batch(
        batch_index=1, batch_total=1, batch_size=1, selected_count=1, processed_count=0
    )
    handle.started_ns -= 9_000_000_000
    with (
        patch.object(main, "interruptible_sleep"),
        patch.object(main.time, "monotonic", side_effect=[0.0, 2.0]),
    ):
        main._sleep_with_runtime_event(
            SimpleNamespace(),
            1.5,
            reason="x",
            callback=handle.forward_provider_event,
        )
    _assert_canonical(sink)
    resume = next(e for e in sink if e["kind"] == "retry_resume")
    assert resume["waited_ms"] == 2000
    assert resume["elapsed_ms"] >= 9000  # 批次經過時間，意義獨立
    assert executor.metrics.retry_backoff_ms == 1500


def test_wait_for_request_slot_emits_canonical_rate_limit_events():
    registry = get_model_quota_registry()
    registry.clear()
    runtime = SimpleNamespace(provider="gemini", batch_interval_sec=3.0, lm_cfg={})
    events: list[dict] = []
    with patch.object(main, "interruptible_sleep"):
        main._wait_for_request_slot(
            runtime, "m-contract", wait_event_callback=events.append
        )
        main._wait_for_request_slot(
            runtime, "m-contract", wait_event_callback=events.append
        )
    registry.clear()
    _assert_canonical(events)
    kinds = [e["kind"] for e in events]
    assert "rate_limit_wait" in kinds and "rate_limit_resume" in kinds
    wait = next(e for e in events if e["kind"] == "rate_limit_wait")
    assert 0 < wait["wait_ms"] <= 3000
    resume = next(e for e in events if e["kind"] == "rate_limit_resume")
    assert "waited_ms" in resume and "wait_ms" in resume


def test_post_with_retry_emits_canonical_network_events():
    events: list[dict] = []
    ok = Mock(status_code=200)
    with (
        patch.object(
            client.requests, "post", side_effect=[requests.ConnectionError("x"), ok]
        ),
        patch.object(client, "interruptible_sleep"),
    ):
        client._post_with_retry("http://x", network_event_callback=events.append)
    _assert_canonical(events)
    wait = next(e for e in events if e["kind"] == "network_retry_wait")
    resume = next(e for e in events if e["kind"] == "network_retry_resume")
    assert wait["wait_ms"] > 0
    assert resume["wait_ms"] == wait["wait_ms"] and "waited_ms" in resume


@pytest.mark.parametrize(
    ("status", "overload", "rate_limit"),
    [(429, False, True), (503, True, False), (500, False, False)],
)
def test_chatgpt_retry_events_are_canonical(status, overload, rate_limit):
    events: list[dict] = []
    runtime = SimpleNamespace(chatgpt_retry_count=0)
    error = ChatGPTAPIError("transient", status=status, retry_after=2.5)
    with patch.object(main, "interruptible_sleep"):
        main._handle_chatgpt_api_error(
            runtime, error, retry_event_callback=events.append
        )
    _assert_canonical(events)
    wait = next(e for e in events if e["kind"] == "retry_wait")
    assert wait["wait_ms"] == 2500 and wait["retry_after_ms"] == 2500
    assert wait["overload"] is overload and wait["rate_limit"] is rate_limit
    resume = next(e for e in events if e["kind"] == "retry_resume")
    assert resume["wait_ms"] == 2500 and "waited_ms" in resume


def test_repair_request_failure_event_is_canonical():
    from translation_tool.translation_db import smart_review_repair as repair

    emitted: list[dict] = []
    fake = SimpleNamespace(
        state=SimpleNamespace(bump=lambda *_a, **_k: None),
        _emit_failure=lambda **fields: emitted.append(
            {"kind": "validation_repair_failure", **fields}
        ),
    )
    error = ChatGPTAPIError("server_overloaded", status=503)
    repair._ValidationRepair._record_request_error(fake, error)
    _assert_canonical(emitted)
    assert emitted[0]["overload"] is True
    assert emitted[0]["rate_limit"] is False


# -- 舊資料與輸出邊界 -----------------------------------------------------------------


def test_old_history_events_still_canonicalize_and_format():
    old = {
        "kind": "retry_wait",
        "batch_number": 1,
        "batch_total": 2,
        "smart_review_attempt": 1,
        "retry_number": 1,
        "retry_total": 3,
        "wait_seconds": 30,
        "error_kind": "service_unavailable",
        "http_status": 503,
        "retry_after_seconds": 12.5,
        "provider_overload": True,
        "quota_or_rate_limit": False,
    }
    canonical = canonicalize_event(old)
    assert canonical["wait_ms"] == 30000
    assert canonical["retry_after_ms"] == 12500
    assert canonical["overload"] is True and canonical["rate_limit"] is False
    assert canonical["batch_index"] == 1 and canonical["attempt"] == 1
    assert "Retry-After=12.5s" in format_batch_event(old)
    old_resume = canonicalize_event(
        {"kind": "retry_resume", "elapsed_ms": 5000, "elapsed_seconds": 2.5}
    )
    assert old_resume["waited_ms"] == 2500 and old_resume["elapsed_ms"] == 5000


def test_output_adapter_gives_third_parties_the_legacy_names_without_overwriting():
    core = {
        "kind": "retry_wait",
        "wait_ms": 2500,
        "retry_after_ms": 1000,
        "overload": True,
        "rate_limit": False,
        "attempt": 2,
    }
    legacy = to_legacy_event(core)
    assert legacy["wait_seconds"] == 2.5
    assert legacy["retry_after_seconds"] == 1.0
    assert legacy["provider_overload"] is True
    assert legacy["quota_or_rate_limit"] is False
    assert legacy["smart_review_attempt"] == 2
    assert core["wait_ms"] == 2500 and LEGACY_EVENT_KEYS.isdisjoint(core)
    # 別名只補缺值：既有的舊欄位不會被覆蓋。
    assert to_legacy_event({**core, "wait_seconds": 99})["wait_seconds"] == 99
    resume = to_legacy_event(
        {"kind": "retry_resume", "wait_ms": 1000, "waited_ms": 2000}
    )
    assert resume["elapsed_seconds"] == 2.0


def test_persisted_smart_review_metrics_keep_legacy_seconds_for_the_probe_tool():
    from translation_tool.translation_db.smart_review_run_state import (
        new_smart_review_metrics,
    )

    metrics = new_smart_review_metrics(input_token_budget=None)
    metrics.update(api_ms=1500, history_write_ms=250)
    metrics.update(to_legacy_event(metrics))
    assert metrics["api_seconds"] == 1.5
    assert metrics["result_write_seconds"] == 0.25
