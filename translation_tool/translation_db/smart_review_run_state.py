"""智慧校對單次執行的帳本：計數、metrics、診斷事件與進度回報。

``SmartReviewRunState`` 是一次 ``run_smart_review`` 內唯一會被各階段修改的
共享狀態：已處理／有效／無效數量、metrics、provider token 用量、共用批次
執行器的計時，以及不含 prompt、response、key 或錯誤原文的結構化事件。
請求、驗證修復與批次迴圈各自在 ``smart_review_request``、``smart_review_repair``
與 ``smart_review_run``，只透過本類別回報結果。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import requests

from translation_tool.core.common_batch_executor import (
    CommonBatchExecutor,
    classify_provider_error,
)
from translation_tool.translation_db.smart_review_provider import (
    _smart_review_error_code,
    _smart_review_record_usage,
)

_RETRY_BASELINE_KEYS = (
    "smart_review_attempts",
    "retry_count",
    "transient_retry_count",
    "overload_count",
    "key_rotation_count",
)
_COMMON_METRIC_KEYS = (
    "selection_ms",
    "planning_ms",
    "prompt_build_ms",
    "quota_wait_ms",
    "retry_backoff_ms",
    "api_ms",
    "response_parse_ms",
    "validation_ms",
    "commit_ms",
    "history_write_ms",
    "network_retry_wait_ms",
    "total_ms",
    "batch_count",
    "error_batches",
    "task_id",
    "run_id",
    "operation",
    "provider",
    "model",
)


def new_smart_review_metrics(
    *,
    setup_seconds: float,
    selection_seconds: float,
    run_record_seconds: float,
    input_token_budget: int | None,
) -> dict[str, Any]:
    """建立一次執行的 metrics 初始值。"""
    return {
        "duration_seconds": 0.0,
        "total_duration_seconds": 0.0,
        "setup_seconds": setup_seconds,
        "selection_seconds": selection_seconds,
        "run_record_seconds": run_record_seconds,
        "preparation_seconds": 0.0,
        "rate_limit_wait_seconds": 0.0,
        "retry_wait_seconds": 0.0,
        "network_retry_wait_seconds": 0.0,
        "api_seconds": 0.0,
        "parse_seconds": 0.0,
        "result_write_seconds": 0.0,
        "batch_seconds": 0.0,
        "batch_count": 0,
        "prompt_tokens": None,
        "output_tokens": None,
        "no_change_count": 0,
        "suggestion_count": 0,
        "suggest_revision_count": 0,
        "needs_context_count": 0,
        "request_attempts": 0,
        "smart_review_attempts": 0,
        "network_attempts": 0,
        "network_retry_count": 0,
        "retry_count": 0,
        "transient_retry_count": 0,
        "overload_count": 0,
        "key_rotation_count": 0,
        "shrink_count": 0,
        "input_budget_shrink_count": 0,
        "input_budget_exceeded_count": 0,
        "response_invalid_count": 0,
        "validation_repair_attempts": 0,
        "validation_repair_items": 0,
        "repaired_valid_count": 0,
        "repaired_invalid_count": 0,
        "validation_repair_request_errors": 0,
        "validation_repair_response_invalid_count": 0,
        "validation_repair_api_seconds": 0.0,
        "validation_repair_parse_seconds": 0.0,
        "usage_missing_attempts": 0,
        "usage_missing_output_attempts": 0,
        "permanent_error_count": 0,
        "failure_classes": {},
        "validation_errors": {},
        "input_token_budget": input_token_budget,
    }


@dataclass
class SmartReviewCounts:
    """一次執行的候選處理計數。"""

    processed: int = 0
    valid: int = 0
    invalid: int = 0
    request_errors: int = 0
    no_change: int = 0
    suggestions: int = 0
    needs_context: int = 0


@dataclass
class SmartReviewBatch:
    """一個邏輯批次的編號、起點基準與耗時診斷。"""

    number: int
    total: int
    started: float
    attempts_before: int
    counters_before: dict[str, int]
    retry_before: dict[str, int]
    timing: dict[str, Any]
    validation_errors: dict[str, int] = field(default_factory=dict)


class SmartReviewRunState:
    """保存並更新單次智慧校對執行的計數、metrics 與事件。"""

    def __init__(
        self,
        *,
        db: Any,
        run_id: str,
        executor: CommonBatchExecutor,
        selected_count: int,
        planned_batch_count: int,
        progress: Callable[[dict[str, Any]], None] | None,
        metrics: dict[str, Any],
        started: float,
    ) -> None:
        self.db = db
        self.run_id = run_id
        self.executor = executor
        self.selected_count = selected_count
        self.planned_batch_count = planned_batch_count
        self.progress = progress
        self.metrics = metrics
        self.started = started
        self.counts = SmartReviewCounts()
        self.token_totals: dict[str, int | None] = {
            "prompt_tokens": None,
            "output_tokens": None,
        }
        self.current_batch_handle: Any = None
        self.cancelled = False

    # -- metrics 累計 ---------------------------------------------------------

    def bump(self, key: str, amount: int = 1) -> None:
        """把整數 metrics 欄位加上指定數量。"""
        self.metrics[key] = int(self.metrics[key]) + amount

    def add_elapsed(self, batch: SmartReviewBatch, key: str, seconds: float) -> None:
        """把耗時同時累計到批次診斷與整體 metrics 的同名欄位。"""
        batch.timing[key] = round(float(batch.timing[key]) + seconds, 3)
        self.metrics[key] = round(float(self.metrics[key]) + seconds, 3)

    def record_usage(self, meta: dict[str, Any]) -> None:
        """記錄一次 API 嘗試的 provider usage，包含失敗或截斷回應。"""
        _smart_review_record_usage(self.metrics, meta, self.token_totals)

    def mark_request_failure(
        self,
        ordinals: Sequence[int],
        error: Exception | None = None,
        status: int | None = None,
        *,
        error_code: str | None = None,
        permanent: bool = False,
    ) -> str:
        """保存安全錯誤代碼並更新錯誤分類統計。"""
        code = error_code or _smart_review_error_code(
            error or RuntimeError("request_failed"), status
        )
        self.db.mark_smart_review_request_error(self.run_id, ordinals, error_code=code)
        self.counts.request_errors += 1
        if permanent:
            self.metrics["permanent_error_count"] = (
                int(self.metrics["permanent_error_count"]) + 1
            )
        classes = self.metrics["failure_classes"]
        classes[code] = int(classes.get(code, 0)) + 1
        return code

    # -- 進度與事件 -----------------------------------------------------------

    def report_progress(
        self,
        *,
        phase: str = "batch",
        timing: dict[str, Any] | None = None,
        event: dict[str, Any] | None = None,
    ) -> None:
        """回報目前已處理數量、階段與可診斷的耗時。"""
        if self.progress is None:
            return
        snapshot: dict[str, Any] = {
            "run_id": self.run_id,
            "processed": self.counts.processed,
            "selected": self.selected_count,
            "valid": self.counts.valid,
            "invalid": self.counts.invalid,
            "request_errors": self.counts.request_errors,
            "status": "running",
            "phase": phase,
        }
        if timing is not None:
            snapshot["timing"] = timing
        if event is not None:
            snapshot["event"] = event
        self.progress(snapshot)

    def _count_event_metrics(self, kind: str, fields: dict[str, Any]) -> None:
        """把事件對應的耗時與重試次數累計到共用批次執行器。"""
        handle = self.current_batch_handle
        metrics = self.executor.metrics
        if kind == "attempt_start":
            handle.attempt = int(
                fields.get("smart_review_attempt") or handle.attempt + 1
            )
            metrics.request_attempts += 1
        elif kind == "attempt_success":
            elapsed_value = fields.get("api_elapsed_seconds")
            try:
                elapsed_ms = float(elapsed_value or 0) * 1000
                metrics.add_ms("api_ms", elapsed_ms)
                fields.setdefault("elapsed_ms", round(elapsed_ms))
            except (TypeError, ValueError):
                pass
        elif kind == "network_attempt_start":
            metrics.network_attempts += 1
        elif kind == "network_retry_wait":
            metrics.network_retry_count += 1
            try:
                wait_ms = max(0, float(fields.get("wait_seconds") or 0) * 1000)
                metrics.add_ms("network_retry_wait_ms", wait_ms)
                fields.setdefault("wait_ms", round(wait_ms))
            except (TypeError, ValueError):
                pass
        elif kind == "retry_wait":
            wait_value = fields.get("wait_seconds")
            try:
                wait_ms = max(0, float(wait_value or 0) * 1000)
            except (TypeError, ValueError):
                wait_ms = 0
            handle.retry_count += 1
            metrics.retry_count += 1
            metrics.transient_retry_count += 1
            metrics.add_ms("retry_backoff_ms", wait_ms)
            fields.setdefault("wait_ms", round(wait_ms))
            fields.setdefault("backoff_ms", round(wait_ms))
        elif kind == "rate_limit_wait":
            wait_value = fields.get("wait_seconds")
            try:
                metrics.add_ms("quota_wait_ms", max(0, float(wait_value or 0) * 1000))
            except (TypeError, ValueError):
                pass
        elif kind == "attempt_failure" and fields.get("key_rotated"):
            metrics.key_rotation_count += 1

    def emit_event(self, kind: str, **fields: Any) -> None:
        """回報不含 prompt、response、key 或錯誤原文的結構化事件。"""
        if self.current_batch_handle is not None:
            self._count_event_metrics(kind, fields)
        if self.progress is None:
            return
        if self.current_batch_handle is not None:
            event = self.current_batch_handle.emit(kind, **fields)
        else:
            event = self.executor.emit(kind, **fields)
        self.report_progress(phase="event", event=event)

    def emit_batch_event(
        self,
        batch: SmartReviewBatch,
        kind: str,
        attempt: int,
        **fields: Any,
    ) -> None:
        """送出帶有批次編號與嘗試序號的事件。"""
        self.emit_event(
            kind,
            batch_number=batch.number,
            batch_total=batch.total,
            smart_review_attempt=attempt,
            **fields,
        )

    def emit_attempt_failure(
        self,
        error: Exception,
        *,
        batch_number: int,
        smart_review_attempt: int,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        key_rotated: bool = False,
        error_kind: str | None = None,
    ) -> None:
        """送出可供診斷的錯誤分類；不使用 provider 的完整錯誤內容。"""
        from translation_tool.core.lm_translator_main import _classify_batch_error

        kind = error_kind or _classify_batch_error(error, status)
        if isinstance(error, requests.Timeout):
            kind = "timeout"
        elif isinstance(error, requests.ConnectionError):
            kind = "connection_error"
        classification = classify_provider_error(error, status)
        if status == 503 or kind == "service_unavailable":
            self.metrics["overload_count"] = int(self.metrics["overload_count"]) + 1
            self.executor.metrics.overload_count += 1
        self.executor.metrics.error_batches.append(batch_number)
        code = str(getattr(error, "code", "") or "").lower()
        self.emit_event(
            "attempt_failure",
            batch_number=batch_number,
            batch_total=self.planned_batch_count,
            smart_review_attempt=smart_review_attempt,
            http_status=status,
            error_kind=kind,
            error_class=classification["error_class"],
            provider_error_code=classification["provider_error_code"],
            exception_type=type(error).__name__,
            provider_overload=bool(status == 503 or kind == "service_unavailable"),
            quota_or_rate_limit=bool(status == 429 or "quota" in code),
            timeout=bool(isinstance(error, requests.Timeout) or kind == "timeout"),
            connection=classification["connection"],
            retry_after_seconds=retry_after_seconds,
            retry_after_ms=(
                round(float(retry_after_seconds) * 1000)
                if retry_after_seconds is not None
                else None
            ),
            will_retry=bool(
                key_rotated
                or status in {408, 429, 500, 502, 503, 504}
                or isinstance(error, (requests.Timeout, requests.ConnectionError))
            ),
            key_rotated=key_rotated,
            network_attempts=int(self.metrics.get("network_attempts", 0)),
            network_retry_count=int(self.metrics.get("network_retry_count", 0)),
        )

    # -- 批次生命週期 ---------------------------------------------------------

    def begin_batch(
        self,
        *,
        number: int,
        size: int,
        ordinal_start: int,
        ordinal_end: int,
        provider: str,
        model_name: str,
    ) -> SmartReviewBatch:
        """建立批次診斷基準、向共用執行器登記並送出 ``batch_start``。"""
        counts = self.counts
        total = max(self.planned_batch_count, number)
        batch = SmartReviewBatch(
            number=number,
            total=total,
            started=time.monotonic(),
            attempts_before=int(self.metrics["request_attempts"]),
            counters_before={
                "valid": counts.valid,
                "invalid": counts.invalid,
                "needs_context": counts.needs_context,
                "no_change": counts.no_change,
                "suggestions": counts.suggestions,
            },
            retry_before={key: int(self.metrics[key]) for key in _RETRY_BASELINE_KEYS},
            timing={
                "batch_number": number,
                "batch_total": total,
                "ordinal_start": ordinal_start,
                "ordinal_end": ordinal_end,
                "actual_count": size,
                "preparation_seconds": 0.0,
                "rate_limit_wait_seconds": 0.0,
                "retry_wait_seconds": 0.0,
                "network_retry_wait_seconds": 0.0,
                "api_seconds": 0.0,
                "parse_seconds": 0.0,
                "result_write_seconds": 0.0,
                "validation_repair_attempts": 0,
                "validation_repair_items": 0,
                "repaired_valid_count": 0,
                "repaired_invalid_count": 0,
                "validation_repair_api_seconds": 0.0,
                "validation_repair_parse_seconds": 0.0,
            },
        )
        self.current_batch_handle = self.executor.begin_batch(
            batch_index=number,
            batch_total=total,
            batch_size=size,
            selected_count=self.selected_count,
            processed_count=counts.processed,
            ordinal_start=ordinal_start,
            ordinal_end=ordinal_end,
        )
        self.emit_event(
            "batch_start",
            batch_number=number,
            batch_total=total,
            ordinal_start=ordinal_start,
            ordinal_end=ordinal_end,
            expected_count=size,
            provider=provider,
            model=model_name,
        )
        return batch

    def _finish_batch_timing(self, batch: SmartReviewBatch, status: str) -> dict:
        """結算一個邏輯批次的耗時並回傳不含敏感資料的診斷摘要。"""
        metrics, counts = self.metrics, self.counts
        before, retry_before = batch.counters_before, batch.retry_before
        elapsed = round(time.monotonic() - batch.started, 3)
        finalized = dict(batch.timing)
        finalized["elapsed_seconds"] = elapsed
        finalized["attempts"] = max(
            0, int(metrics["request_attempts"]) - batch.attempts_before
        )
        finalized["smart_review_attempts"] = max(
            0,
            int(metrics["smart_review_attempts"])
            - retry_before.get("smart_review_attempts", 0),
        )
        for key in (
            "retry_count",
            "transient_retry_count",
            "overload_count",
            "key_rotation_count",
        ):
            finalized[key] = max(
                0, int(metrics[key]) - retry_before.get(key, int(metrics[key]))
            )
        finalized.update(
            {
                "valid_count": max(0, counts.valid - before.get("valid", counts.valid)),
                "invalid_count": max(
                    0, counts.invalid - before.get("invalid", counts.invalid)
                ),
                "needs_context_count": max(
                    0,
                    counts.needs_context
                    - before.get("needs_context", counts.needs_context),
                ),
                "no_change_count": max(
                    0, counts.no_change - before.get("no_change", counts.no_change)
                ),
                "suggestion_count": max(
                    0,
                    counts.suggestions - before.get("suggestions", counts.suggestions),
                ),
                "suggest_revision_count": max(
                    0,
                    counts.suggestions - before.get("suggestions", counts.suggestions),
                ),
                "validation_errors": dict(batch.validation_errors),
            }
        )
        finalized["status"] = status
        metrics["batch_count"] = int(metrics["batch_count"]) + 1
        metrics["batch_seconds"] = round(float(metrics["batch_seconds"]) + elapsed, 3)
        return finalized

    def complete_batch(self, batch: SmartReviewBatch, status: str) -> None:
        """結算批次耗時，並同時送出舊版批次進度與可供 UI 即時顯示的完整事件。"""
        timing = self._finish_batch_timing(batch, status)
        event_fields = {
            "batch_number": timing.get("batch_number"),
            "batch_total": timing.get("batch_total"),
            "ordinal_start": timing.get("ordinal_start"),
            "ordinal_end": timing.get("ordinal_end"),
            "actual_count": timing.get("actual_count"),
            "attempts": timing.get("attempts", 0),
            "smart_review_attempts": timing.get("smart_review_attempts", 0),
            "retry_count": timing.get("retry_count", 0),
            "rate_limit_wait_seconds": timing.get("rate_limit_wait_seconds", 0.0),
            "retry_wait_seconds": timing.get("retry_wait_seconds", 0.0),
            "network_retry_wait_seconds": timing.get("network_retry_wait_seconds", 0.0),
            "api_seconds": timing.get("api_seconds", 0.0),
            "parse_seconds": timing.get("parse_seconds", 0.0),
            "result_write_seconds": timing.get("result_write_seconds", 0.0),
            "validation_repair_attempts": timing.get("validation_repair_attempts", 0),
            "validation_repair_items": timing.get("validation_repair_items", 0),
            "repaired_valid_count": timing.get("repaired_valid_count", 0),
            "repaired_invalid_count": timing.get("repaired_invalid_count", 0),
            "validation_repair_api_seconds": timing.get(
                "validation_repair_api_seconds", 0.0
            ),
            "validation_repair_parse_seconds": timing.get(
                "validation_repair_parse_seconds", 0.0
            ),
            "elapsed_seconds": timing.get("elapsed_seconds", 0.0),
            "valid_count": timing.get("valid_count", 0),
            "invalid_count": timing.get("invalid_count", 0),
            "needs_context_count": timing.get("needs_context_count", 0),
            "no_change_count": timing.get("no_change_count", 0),
            "suggestion_count": timing.get("suggestion_count", 0),
            "suggest_revision_count": timing.get("suggest_revision_count", 0),
            "validation_errors": timing.get("validation_errors", {}),
            "request_errors": self.counts.request_errors,
        }
        processed = self.counts.processed
        handle = self.current_batch_handle
        if handle is not None:
            handle.complete(
                status=str(timing.get("status") or "completed"),
                processed_count=processed,
                emit=False,
                **event_fields,
            )
            event = handle.emit(
                "batch_complete", processed_count=processed, **event_fields
            )
        else:
            event = self.executor.emit("batch_complete", **event_fields)
        self.report_progress(phase="batch_complete", timing=timing, event=event)

    # -- 結算 -----------------------------------------------------------------

    def _merge_common_metrics(self) -> None:
        """補上共用執行器的計時與識別欄位，不改變既有 Smart Review 計數。"""
        common = self.executor.metrics_snapshot()
        for key in _COMMON_METRIC_KEYS:
            self.metrics[key] = common[key]
        self.metrics["common_request_attempts"] = common["request_attempts"]
        self.metrics["common_provider_attempts"] = common["provider_attempts"]
        self.metrics["common_retry_count"] = common["retry_count"]
        self.metrics["common_transient_retry_count"] = common["transient_retry_count"]

    def _token_count_label(self) -> str:
        """標示 token 用量是 provider 完整回報、部分回報或未回報。"""
        metrics = self.metrics
        if metrics["request_attempts"] == 0:
            return "not_reported"
        if (
            metrics["usage_missing_attempts"]
            or metrics["usage_missing_output_attempts"]
        ):
            return "provider_reported_partial"
        return "provider_reported"

    def close_run(self, status: str, *, include_outcome_counts: bool) -> None:
        """結束共用執行器、合併 metrics 並保存這次執行的最終統計。"""
        total_seconds = round(time.monotonic() - self.started, 3)
        executor = self.executor
        executor.metrics.total_ms = max(
            executor.metrics.total_ms, round(total_seconds * 1000)
        )
        executor.finish(status=status)
        self._merge_common_metrics()
        counts = self.counts
        final_metrics: dict[str, Any] = {
            "duration_seconds": total_seconds,
            "total_duration_seconds": total_seconds,
            "prompt_tokens": self.token_totals["prompt_tokens"],
            "output_tokens": self.token_totals["output_tokens"],
        }
        if include_outcome_counts:
            final_metrics.update(
                {
                    "no_change_count": counts.no_change,
                    "suggestion_count": counts.suggestions,
                    "suggest_revision_count": counts.suggestions,
                    "needs_context_count": counts.needs_context,
                }
            )
        final_metrics["status"] = status
        final_metrics["token_counts"] = self._token_count_label()
        self.metrics.update(final_metrics)
        self.db.finish_smart_review_run(
            self.run_id,
            processed_count=counts.processed,
            valid_count=counts.valid,
            invalid_count=counts.invalid,
            request_error_count=counts.request_errors,
            metrics=self.metrics,
            status=status,
        )
