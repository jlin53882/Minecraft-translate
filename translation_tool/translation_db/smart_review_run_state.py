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

from translation_tool.core.batch_events import BatchTiming, to_legacy_event
from translation_tool.core.common_batch_executor import (
    CommonBatchExecutor,
    classify_provider_error,
)
from translation_tool.translation_db.smart_review_provider import (
    _smart_review_error_code,
    _smart_review_record_usage,
)

_RETRY_BASELINE_KEYS = (
    "attempts",
    "retry_count",
    "transient_retry_count",
    "overload_count",
    "key_rotation_count",
)
_COMMON_METRIC_KEYS = (
    "batch_count",
    "error_batches",
    "task_id",
    "run_id",
    "operation",
    "provider",
    "model",
)


def new_smart_review_metrics(*, input_token_budget: int | None) -> dict[str, Any]:
    """建立一次執行的計數型 metrics 初始值；耗時由 ``BatchTiming`` 累計，結算時才併入。"""
    return {
        "prompt_tokens": None,
        "output_tokens": None,
        "no_change_count": 0,
        "suggestion_count": 0,
        "suggest_revision_count": 0,
        "needs_context_count": 0,
        "request_attempts": 0,
        "attempts": 0,
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
    """一個邏輯批次的編號、起點基準與耗時診斷（耗時為 canonical 毫秒）。"""

    number: int
    total: int
    started: float
    counters_before: dict[str, int]
    retry_before: dict[str, int]
    ordinal_start: int
    ordinal_end: int
    actual_count: int
    timing: BatchTiming = field(default_factory=BatchTiming)
    validation_repair_attempts: int = 0
    validation_repair_items: int = 0
    repaired_valid_count: int = 0
    repaired_invalid_count: int = 0
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

    def add_timing(
        self, batch: SmartReviewBatch, field_name: str, seconds: float
    ) -> None:
        """把量測到的耗時（秒）累計到該批次與整體執行的同名 canonical ``*_ms`` 欄位。"""
        elapsed_ms = batch.timing.add_seconds(field_name, seconds)
        self.executor.metrics.add(field_name, elapsed_ms)

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
            self.bump("permanent_error_count")
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
        # 輸出邊界：core 內部只有 canonical 欄位，回呼端才補舊版欄位。
        if timing is not None:
            snapshot["timing"] = to_legacy_event(timing)
        if event is not None:
            snapshot["event"] = to_legacy_event(event)
        self.progress(snapshot)

    def _count_event_metrics(self, kind: str, fields: dict[str, Any]) -> None:
        """把事件對應的重試與嘗試計數累計到共用批次執行器（耗時由 ``add_timing`` 累計）。"""
        handle = self.current_batch_handle
        metrics = self.executor.metrics
        if kind == "attempt_start":
            handle.attempt = int(fields.get("attempt") or handle.attempt + 1)
            metrics.request_attempts += 1
        elif kind == "attempt_success":
            fields.setdefault("elapsed_ms", int(fields.get("api_ms") or 0))
        elif kind == "network_attempt_start":
            metrics.network_attempts += 1
        elif kind == "network_retry_wait":
            metrics.network_retry_count += 1
        elif kind == "retry_wait":
            handle.retry_count += 1
            metrics.retry_count += 1
            metrics.transient_retry_count += 1
            fields.setdefault("backoff_ms", fields.get("wait_ms", 0))
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
            batch_index=batch.number,
            batch_total=batch.total,
            attempt=attempt,
            **fields,
        )

    def emit_attempt_failure(
        self,
        error: Exception,
        *,
        batch_number: int,
        attempt: int,
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
        overload = bool(status == 503 or kind == "service_unavailable")
        if overload:
            self.bump("overload_count")
            self.executor.metrics.overload_count += 1
        self.executor.metrics.error_batches.append(batch_number)
        code = str(getattr(error, "code", "") or "").lower()
        self.emit_event(
            "attempt_failure",
            batch_index=batch_number,
            batch_total=self.planned_batch_count,
            attempt=attempt,
            http_status=status,
            error_kind=kind,
            error_class=classification["error_class"],
            provider_error_code=classification["provider_error_code"],
            exception_type=type(error).__name__,
            overload=overload,
            rate_limit=bool(status == 429 or "quota" in code),
            timeout=bool(isinstance(error, requests.Timeout) or kind == "timeout"),
            connection=classification["connection"],
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
            counters_before={
                "valid": counts.valid,
                "invalid": counts.invalid,
                "needs_context": counts.needs_context,
                "no_change": counts.no_change,
                "suggestions": counts.suggestions,
            },
            retry_before={key: int(self.metrics[key]) for key in _RETRY_BASELINE_KEYS},
            ordinal_start=ordinal_start,
            ordinal_end=ordinal_end,
            actual_count=size,
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
            batch_index=number,
            batch_total=total,
            ordinal_start=ordinal_start,
            ordinal_end=ordinal_end,
            expected_count=size,
            provider=provider,
            model=model_name,
        )
        return batch

    def _finish_batch_timing(self, batch: SmartReviewBatch, status: str) -> dict:
        """結算一個邏輯批次的耗時並回傳不含敏感資料的 canonical 診斷摘要。"""
        metrics, counts = self.metrics, self.counts
        before, retry_before = batch.counters_before, batch.retry_before
        elapsed_ms = batch.timing.add_seconds(
            "total_ms", time.monotonic() - batch.started
        )
        self.executor.metrics.add("batch_ms", elapsed_ms)

        def delta(current: int, key: str) -> int:
            return max(0, current - before.get(key, current))

        suggestions = delta(counts.suggestions, "suggestions")
        summary: dict[str, Any] = {
            "batch_index": batch.number,
            "batch_total": batch.total,
            "ordinal_start": batch.ordinal_start,
            "ordinal_end": batch.ordinal_end,
            "actual_count": batch.actual_count,
            **batch.timing.to_event(),
            "elapsed_ms": elapsed_ms,
            "validation_repair_attempts": batch.validation_repair_attempts,
            "validation_repair_items": batch.validation_repair_items,
            "repaired_valid_count": batch.repaired_valid_count,
            "repaired_invalid_count": batch.repaired_invalid_count,
            "valid_count": delta(counts.valid, "valid"),
            "invalid_count": delta(counts.invalid, "invalid"),
            "needs_context_count": delta(counts.needs_context, "needs_context"),
            "no_change_count": delta(counts.no_change, "no_change"),
            "suggestion_count": suggestions,
            "validation_errors": dict(batch.validation_errors),
            "status": status,
        }
        for key in _RETRY_BASELINE_KEYS:
            summary[key] = max(0, int(metrics[key]) - retry_before.get(key, 0))
        return summary

    def complete_batch(self, batch: SmartReviewBatch, status: str) -> None:
        """結算批次耗時，並送出批次完成事件與 UI 即時顯示用的進度快照。"""
        timing = self._finish_batch_timing(batch, status)
        event_fields = {
            **timing,
            "request_errors": self.counts.request_errors,
        }
        processed = self.counts.processed
        handle = self.current_batch_handle
        if handle is not None:
            handle.complete(processed_count=processed, emit=False, **event_fields)
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
        self.metrics.update(self.executor.metrics.to_event())
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
        total_ms = round((time.monotonic() - self.started) * 1000)
        executor = self.executor
        executor.metrics.total_ms = max(executor.metrics.total_ms, total_ms)
        executor.finish(status=status)
        self._merge_common_metrics()
        counts = self.counts
        self.metrics.update(
            prompt_tokens=self.token_totals["prompt_tokens"],
            output_tokens=self.token_totals["output_tokens"],
            status=status,
            token_counts=self._token_count_label(),
        )
        if include_outcome_counts:
            self.metrics.update(
                no_change_count=counts.no_change,
                suggestion_count=counts.suggestions,
                needs_context_count=counts.needs_context,
            )
        # 輸出邊界：持久化的 metrics 同時保留舊版 ``*_seconds`` 欄位供既有讀者使用。
        self.metrics.update(to_legacy_event(self.metrics))
        self.db.finish_smart_review_run(
            self.run_id,
            processed_count=counts.processed,
            valid_count=counts.valid,
            invalid_count=counts.invalid,
            request_error_count=counts.request_errors,
            metrics=self.metrics,
            status=status,
        )
