"""共用批次執行器、事件安全性與 provider／network retry 分層的針對性測試。"""

from __future__ import annotations

import requests

from translation_tool.core.common_batch_executor import (
    CommonBatchExecutor,
    classify_provider_error,
)
from translation_tool.core.lm_translator_shared_loop import (
    translate_items_with_cache_loop,
)


def test_common_executor_emits_safe_batch_attempt_and_retry_events():
    """驗證此測試案例的預期結果與相容性。"""
    events: list[dict] = []
    executor = CommonBatchExecutor(
        operation="smart_review",
        provider="gemini",
        model="test-model",
        task_id="task-test",
        run_id="run-test",
        event_sink=events.append,
    )
    handle = executor.begin_batch(
        batch_index=1,
        batch_total=2,
        batch_size=2,
        selected_count=4,
        processed_count=0,
    )
    handle.attempt_start(max_attempts=3)
    handle.retry_wait(1250, reason="provider_overload")
    handle.attempt_success(
        elapsed_ms=12,
        http_status=200,
        expected_count=2,
        actual_count=2,
        prompt_tokens=20,
        output_tokens=10,
        prompt="do not log this",
        api_key="AIza-secret",
    )
    handle.complete(status="completed", processed_count=2)
    executor.finish(status="completed")

    kinds = [event["kind"] for event in events]
    assert kinds == [
        "batch_start",
        "attempt_start",
        "retry_wait",
        "attempt_success",
        "batch_complete",
        "run_complete",
    ]
    assert events[0]["task_id"] == "task-test"
    assert events[0]["operation"] == "smart_review"
    assert events[0]["batch_index"] == 1
    assert events[2]["wait_ms"] == 1250
    assert events[3]["elapsed_ms"] == 12
    assert events[3]["prompt_tokens"] == 20
    assert "prompt" not in events[3]
    assert "api_key" not in events[3]
    assert "AIza-secret" not in repr(events)
    assert executor.metrics.request_attempts == 1
    assert executor.metrics.retry_count == 1
    assert executor.metrics.retry_backoff_ms == 1250


def test_common_error_classifier_distinguishes_provider_failure_kinds():
    """驗證此測試案例的預期結果與相容性。"""
    timeout = classify_provider_error(requests.Timeout())
    assert timeout["error_class"] == "timeout"
    assert timeout["timeout"] is True

    overload = classify_provider_error(RuntimeError("secret response body"), 503)
    assert overload["error_class"] == "service_unavailable"
    assert overload["overload"] is True
    assert "secret response body" not in repr(overload)

    rate_limit = classify_provider_error(RuntimeError(), 429)
    assert rate_limit["error_class"] == "rate_limit"
    assert rate_limit["rate_limit"] is True


def test_machine_translation_shared_loop_uses_common_batch_events():
    """驗證此測試案例的預期結果與相容性。"""
    events: list[dict] = []
    item = {"path": "a", "source_text": "A", "text": "A", "cache_type": "lang"}

    result = translate_items_with_cache_loop(
        [item],
        translate_batch_smart=lambda batch, _total: (
            [{**batch[0], "text": "甲"}],
            "AUTO",
        ),
        write_new_cache=False,
        reload_cache=False,
        cache_add=lambda *_args, **_kwargs: True,
        cache_save=lambda *_args, **_kwargs: True,
        event_sink=events.append,
        event_task_id="task-translate",
        event_run_id="run-translate",
        event_batch_total=1,
    )

    assert result.status == "DONE"
    assert [event["kind"] for event in events] == [
        "batch_start",
        "attempt_start",
        "attempt_success",
        "batch_complete",
        "run_complete",
    ]
    assert all(event["operation"] == "translate" for event in events)
    assert result.metrics["total_ms"] >= 0


def test_shared_loop_keeps_provider_and_network_retry_metrics_separate():
    """驗證此測試案例的預期結果與相容性。"""
    events: list[dict] = []
    item = {"path": "a", "source_text": "A", "text": "A", "cache_type": "lang"}

    def translate(batch, _total, *, event_callback):
        """發送測試用的 provider／network 事件並回傳最小翻譯結果。"""
        event_callback(
            {
                "kind": "provider_attempt_start",
                "provider_attempt": 1,
                "provider_model": "model-a",
            }
        )
        event_callback(
            {
                "kind": "network_attempt_start",
                "network_attempt": 1,
                "network_total": 2,
            }
        )
        event_callback(
            {
                "kind": "network_retry_wait",
                "network_attempt": 1,
                "network_total": 2,
                "wait_seconds": 0.25,
            }
        )
        event_callback(
            {
                "kind": "retry_wait",
                "wait_seconds": 0.5,
                "retry_reason": "provider_overload",
            }
        )
        return [{**batch[0], "text": "甲"}], "AUTO"

    result = translate_items_with_cache_loop(
        [item],
        translate_batch_smart=translate,
        write_new_cache=False,
        reload_cache=False,
        cache_add=lambda *_args, **_kwargs: True,
        cache_save=lambda *_args, **_kwargs: True,
        event_sink=events.append,
        event_task_id="task-translate",
        event_run_id="run-translate",
        event_batch_total=1,
    )

    assert result.status == "DONE"
    assert result.metrics["provider_attempts"] == 1
    assert result.metrics["network_attempts"] == 1
    assert result.metrics["network_retry_count"] == 1
    assert result.metrics["network_retry_wait_ms"] == 250
    assert result.metrics["retry_count"] == 1
    assert result.metrics["retry_backoff_ms"] == 500
    assert [event["kind"] for event in events][2:6] == [
        "provider_attempt_start",
        "network_attempt_start",
        "network_retry_wait",
        "retry_wait",
    ]
