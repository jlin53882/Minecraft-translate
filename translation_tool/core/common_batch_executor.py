"""Shared batch execution telemetry and orchestration primitives.

The three translation workflows deliberately keep their own policies for
selection, payloads, provider retry decisions, validation and persistence.
This module owns the parts that must look the same to operators: run/batch
identity, monotonic timings, cancellation checkpoints and safe structured
events.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import requests

from translation_tool.utils.cancellation import TaskCancelled

logger = logging.getLogger(__name__)


_PHASE_FOR_KIND = {
    "run_start": "run",
    "run_complete": "run",
    "batch_start": "batch",
    "batch_complete": "batch_complete",
    "attempt_start": "api",
    "attempt_success": "api",
    "attempt_failure": "api",
    "provider_attempt_start": "api",
    "provider_attempt_success": "api",
    "provider_attempt_failure": "api",
    "network_attempt_start": "network",
    "network_response": "network",
    "network_failure": "network",
    "network_retry_wait": "retry",
    "network_retry_resume": "retry",
    "retry_wait": "retry",
    "retry_resume": "retry",
    "rate_limit_wait": "quota",
    "rate_limit_resume": "quota",
    "key_rotation": "retry",
}

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "payload",
        "prompt",
        "system_prompt",
        "raw",
        "raw_response",
        "response_text",
        "error_message",
        "exception_message",
    }
)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _safe_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Keep event payloads bounded and free of prompt/credential material."""
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if str(key).lower() in _SENSITIVE_FIELD_NAMES:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, Mapping):
            safe[str(key)] = _safe_fields(value)
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            safe[str(key)] = [str(item) for item in value[:20]]
        else:
            safe[str(key)] = str(value)[:160]
    return safe


def _wait_ms(fields: Mapping[str, Any]) -> int:
    value = fields.get("wait_ms", fields.get("elapsed_ms"))
    if value is None:
        try:
            value = float(fields.get("wait_seconds") or 0) * 1000
        except (TypeError, ValueError):
            value = 0
    try:
        return max(0, round(float(value)))
    except (TypeError, ValueError):
        return 0


def _status_from_error(error: BaseException, status: int | None) -> int | None:
    if status is not None:
        return status
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return int(value) if isinstance(value, int) else None


def classify_provider_error(
    error: BaseException, status: int | None = None
) -> dict[str, Any]:
    """Classify an exception without exposing its provider error body."""
    status = _status_from_error(error, status)
    name = type(error).__name__
    lowered = name.lower()
    code = str(getattr(error, "code", "") or "").strip().lower()
    if isinstance(error, requests.Timeout) or "timeout" in lowered:
        error_class = "timeout"
    elif isinstance(error, requests.ConnectionError) or "connection" in lowered:
        error_class = "connection_error"
    else:
        error_class = {
            400: "invalid_request",
            401: "authentication",
            403: "key_forbidden",
            404: "model_missing",
            408: "deadline_exceeded",
            413: "context_too_large",
            422: "invalid_request",
            429: "rate_limit",
            500: "provider_transient",
            502: "provider_transient",
            503: "service_unavailable",
            504: "provider_timeout",
        }.get(status, "unknown")
    quota_or_rate_limit = status == 429 or any(
        word in code for word in ("quota", "rate", "limit", "usage")
    )
    overload = status == 503 or error_class in {
        "service_unavailable",
        "provider_overload",
    } or any(word in code for word in ("overload", "high_demand", "too_many"))
    timeout = error_class in {"timeout", "provider_timeout", "deadline_exceeded"}
    connection = error_class == "connection_error"
    retry_after = getattr(error, "retry_after", None)
    try:
        retry_after_ms = max(0, round(float(retry_after) * 1000))
    except (TypeError, ValueError):
        retry_after_ms = None
    return {
        "error_class": error_class,
        "http_status": status,
        "provider_error_code": code or None,
        "exception_type": name,
        "timeout": timeout,
        "connection": connection,
        "overload": overload,
        "rate_limit": quota_or_rate_limit,
        "retry_after_ms": retry_after_ms,
    }


@dataclass
class BatchMetrics:
    """Monotonic, additive timing and retry counters for one run."""

    selection_ms: int = 0
    planning_ms: int = 0
    prompt_build_ms: int = 0
    quota_wait_ms: int = 0
    retry_backoff_ms: int = 0
    api_ms: int = 0
    response_parse_ms: int = 0
    validation_ms: int = 0
    commit_ms: int = 0
    history_write_ms: int = 0
    network_retry_wait_ms: int = 0
    total_ms: int = 0
    batch_count: int = 0
    request_attempts: int = 0
    provider_attempts: int = 0
    retry_count: int = 0
    transient_retry_count: int = 0
    overload_count: int = 0
    key_rotation_count: int = 0
    network_attempts: int = 0
    network_retry_count: int = 0
    error_batches: list[int] = field(default_factory=list)

    def add_ms(self, field_name: str, elapsed_ms: float) -> int:
        if not hasattr(self, field_name):
            raise AttributeError(field_name)
        value = max(0, round(float(elapsed_ms)))
        setattr(self, field_name, int(getattr(self, field_name)) + value)
        return value

    def as_dict(self) -> dict[str, Any]:
        result = {
            key: getattr(self, key)
            for key in (
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
                "request_attempts",
                "provider_attempts",
                "retry_count",
                "transient_retry_count",
                "overload_count",
                "key_rotation_count",
                "network_attempts",
                "network_retry_count",
            )
        }
        result["error_batches"] = list(self.error_batches)
        return result


@dataclass(frozen=True)
class BatchEvent:
    """Safe structured event shared by app log, UI log and reports."""

    task_id: str
    run_id: str
    operation: str
    provider: str
    model: str
    phase: str
    batch_index: int = 0
    batch_total: int = 0
    batch_size: int = 0
    selected_count: int = 0
    processed_count: int = 0
    attempt: int = 0
    max_attempts: int = 0
    retry_count: int = 0
    status: str = ""
    elapsed_ms: int = 0
    fields: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        event = {
            "event_version": 1,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "operation": self.operation,
            "provider": self.provider,
            "model": self.model,
            "phase": self.phase,
            "batch_index": self.batch_index,
            "batch_number": self.batch_index,
            "batch_total": self.batch_total,
            "batch_size": self.batch_size,
            "selected_count": self.selected_count,
            "processed_count": self.processed_count,
            "attempt": self.attempt,
            "smart_review_attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "retry_count": self.retry_count,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
        }
        event.update(_safe_fields(self.fields))
        return event


@dataclass
class BatchHandle:
    executor: CommonBatchExecutor
    batch_index: int
    batch_total: int
    batch_size: int
    selected_count: int
    processed_before: int
    started_ns: int = field(default_factory=time.monotonic_ns)
    attempt: int = 0
    retry_count: int = 0
    _finished: bool = False

    @property
    def elapsed_ms(self) -> int:
        return max(0, round((time.monotonic_ns() - self.started_ns) / 1_000_000))

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        base = {
            "batch_index": self.batch_index,
            "batch_total": self.batch_total,
            "batch_size": self.batch_size,
            "selected_count": self.selected_count,
            "processed_count": self.processed_before,
            "attempt": self.attempt,
            "retry_count": self.retry_count,
            "elapsed_ms": self.elapsed_ms,
        }
        base.update(fields)
        return self.executor.emit(kind, **base)

    def attempt_start(self, *, max_attempts: int = 0, **fields: Any) -> dict[str, Any]:
        self.attempt += 1
        self.executor.metrics.request_attempts += 1
        return self.emit("attempt_start", max_attempts=max_attempts, **fields)

    def attempt_success(self, *, elapsed_ms: float = 0, **fields: Any) -> dict[str, Any]:
        self.executor.metrics.add_ms("api_ms", elapsed_ms)
        return self.emit("attempt_success", elapsed_ms=round(float(elapsed_ms)), **fields)

    def attempt_failure(
        self,
        error: BaseException,
        *,
        status: int | None = None,
        will_retry: bool = False,
        backoff_ms: float = 0,
        reason: str | None = None,
        key_rotated: bool = False,
        **fields: Any,
    ) -> dict[str, Any]:
        classification = classify_provider_error(error, status)
        if classification["overload"]:
            self.executor.metrics.overload_count += 1
        if key_rotated:
            self.executor.metrics.key_rotation_count += 1
        self.executor.metrics.error_batches.append(self.batch_index)
        return self.emit(
            "attempt_failure",
            **classification,
            will_retry=will_retry,
            backoff_ms=round(float(backoff_ms)),
            retry_reason=reason,
            key_rotated=key_rotated,
            **fields,
        )

    def forward_provider_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Forward safe provider/network telemetry under the current batch."""
        fields = dict(event)
        kind = str(fields.pop("kind", "provider_event") or "provider_event")
        if kind == "network_attempt_start":
            self.executor.metrics.network_attempts += 1
        elif kind == "provider_attempt_start":
            self.executor.metrics.provider_attempts += 1
        elif kind == "network_retry_wait":
            self.executor.metrics.network_retry_count += 1
            wait_ms = _wait_ms(fields)
            self.executor.metrics.add_ms("network_retry_wait_ms", wait_ms)
            fields.setdefault("wait_ms", wait_ms)
        elif kind == "retry_wait":
            wait_ms = _wait_ms(fields)
            self.retry_count += 1
            self.executor.metrics.retry_count += 1
            self.executor.metrics.transient_retry_count += 1
            self.executor.metrics.add_ms("retry_backoff_ms", wait_ms)
            fields.setdefault("retry_number", self.retry_count)
            fields.setdefault("wait_ms", wait_ms)
            fields.setdefault("backoff_ms", wait_ms)
        elif kind == "rate_limit_wait":
            wait_ms = _wait_ms(fields)
            self.executor.metrics.add_ms("quota_wait_ms", wait_ms)
            fields.setdefault("wait_ms", wait_ms)
        elif kind == "timing":
            field_name = str(fields.get("field_name") or "")
            if field_name:
                self.executor.metrics.add_ms(field_name, _wait_ms(fields))
        elif kind == "key_rotation":
            self.executor.metrics.key_rotation_count += 1
        return self.emit(kind, **fields)

    def retry_wait(
        self,
        wait_ms: float,
        *,
        transient: bool = True,
        reason: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        wait_ms = max(0, round(float(wait_ms)))
        self.retry_count += 1
        self.executor.metrics.retry_count += 1
        if transient:
            self.executor.metrics.transient_retry_count += 1
        self.executor.metrics.add_ms("retry_backoff_ms", wait_ms)
        return self.emit(
            "retry_wait",
            wait_ms=wait_ms,
            wait_seconds=wait_ms / 1000,
            retry_number=self.retry_count,
            retry_reason=reason,
            **fields,
        )

    def add_timing(self, field_name: str, elapsed_ms: float) -> None:
        self.executor.metrics.add_ms(field_name, elapsed_ms)

    def complete(
        self,
        *,
        status: str,
        processed_count: int,
        emit: bool = True,
        **fields: Any,
    ) -> dict[str, Any]:
        if self._finished:
            return self.emit("batch_complete", status=status, **fields)
        self._finished = True
        self.executor.metrics.batch_count += 1
        self.executor.metrics.total_ms += self.elapsed_ms
        self.executor.metrics.error_batches = list(
            dict.fromkeys(self.executor.metrics.error_batches)
        )
        if not emit:
            return {}
        return self.emit(
            "batch_complete",
            processed_count=processed_count,
            status=status,
            **fields,
        )


@runtime_checkable
class BatchPolicy(Protocol):
    """Business-specific hooks used by a common batch executor."""

    def candidate_selector(self, source: Any) -> Sequence[Any]: ...

    def payload_builder(self, batch: Sequence[Any], context: BatchHandle) -> Any: ...

    def provider_request(self, payload: Any, context: BatchHandle) -> Any: ...

    def response_parser(self, response: Any, context: BatchHandle) -> Any: ...

    def result_validator(self, results: Any, batch: Sequence[Any], context: BatchHandle) -> Any: ...

    def result_committer(self, results: Any, batch: Sequence[Any], context: BatchHandle) -> int: ...

    def history_writer(self, results: Any, context: BatchHandle) -> None: ...

    def on_batch_complete(self, event: Mapping[str, Any]) -> None: ...


class CommonBatchExecutor:
    """Shared lifecycle for legacy and policy-based batch workflows."""

    def __init__(
        self,
        *,
        operation: str,
        provider: str = "",
        model: str = "",
        task_id: str | None = None,
        run_id: str | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        log_unhandled_events: bool = False,
    ) -> None:
        self.operation = operation
        self.provider = provider
        self.model = model
        self.task_id = task_id or _new_id("task")
        self.run_id = run_id or _new_id("run")
        self.event_sink = event_sink
        self.cancel_check = cancel_check
        self.log_unhandled_events = log_unhandled_events
        self.metrics = BatchMetrics()
        self.started_ns = time.monotonic_ns()

    def is_cancelled(self) -> bool:
        return bool(self.cancel_check is not None and self.cancel_check())

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise TaskCancelled()

    def add_timing(self, field_name: str, elapsed_ms: float) -> None:
        self.metrics.add_ms(field_name, elapsed_ms)

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = BatchEvent(
            task_id=self.task_id,
            run_id=self.run_id,
            operation=self.operation,
            provider=self.provider,
            model=self.model,
            phase=_PHASE_FOR_KIND.get(kind, kind),
            batch_index=int(fields.pop("batch_index", 0) or 0),
            batch_total=int(fields.pop("batch_total", 0) or 0),
            batch_size=int(fields.pop("batch_size", 0) or 0),
            selected_count=int(fields.pop("selected_count", 0) or 0),
            processed_count=int(fields.pop("processed_count", 0) or 0),
            attempt=int(fields.pop("attempt", 0) or 0),
            max_attempts=int(fields.pop("max_attempts", 0) or 0),
            retry_count=int(fields.pop("retry_count", 0) or 0),
            status=str(fields.pop("status", "") or ""),
            elapsed_ms=max(0, round(float(fields.pop("elapsed_ms", 0) or 0))),
            fields={"kind": kind, **fields},
        ).as_dict()
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                logger.debug("batch event sink failed", exc_info=True)
        elif self.log_unhandled_events:
            logger.info("[batch-event] %s", event)
        return event

    def begin_batch(
        self,
        *,
        batch_index: int,
        batch_total: int,
        batch_size: int,
        selected_count: int,
        processed_count: int,
        **fields: Any,
    ) -> BatchHandle:
        handle = BatchHandle(
            executor=self,
            batch_index=batch_index,
            batch_total=batch_total,
            batch_size=batch_size,
            selected_count=selected_count,
            processed_before=processed_count,
        )
        handle.emit("batch_start", **fields)
        return handle

    def execute(
        self,
        source: Any,
        policy: BatchPolicy,
        *,
        batch_size: int = 1,
    ) -> list[Any]:
        """Run a fully hook-based policy without owning its business rules.

        Existing translators use the lower-level ``begin_batch`` seam because
        they have dynamic token-budget planning and legacy cache/CAS behavior.
        New policies can use this method directly; every business operation is
        still delegated to the policy hooks below.
        """
        selection_started = time.monotonic_ns()
        selected = list(policy.candidate_selector(source))
        self.add_timing("selection_ms", (time.monotonic_ns() - selection_started) / 1_000_000)
        size = max(1, int(batch_size))
        planning_started = time.monotonic_ns()
        planner = getattr(policy, "plan_batches", None)
        planned = list(planner(selected, size)) if callable(planner) else [
            selected[index : index + size] for index in range(0, len(selected), size)
        ]
        self.add_timing("planning_ms", (time.monotonic_ns() - planning_started) / 1_000_000)
        results: list[Any] = []
        for index, batch in enumerate(planned, start=1):
            self.check_cancelled()
            handle = self.begin_batch(
                batch_index=index,
                batch_total=len(planned),
                batch_size=len(batch),
                selected_count=len(selected),
                processed_count=len(results),
            )
            with self.phase("prompt_build_ms"):
                payload = policy.payload_builder(batch, handle)
            handle.attempt_start(max_attempts=0, expected_count=len(batch))
            request_started = time.monotonic_ns()
            try:
                response = policy.provider_request(payload, handle)
            except TaskCancelled:
                handle.complete(status="cancelled", processed_count=len(results))
                raise
            except Exception as exc:
                handle.attempt_failure(exc, will_retry=False)
                handle.complete(status="failed", processed_count=len(results))
                raise
            try:
                handle.attempt_success(
                    elapsed_ms=(time.monotonic_ns() - request_started) / 1_000_000,
                    http_status=200,
                )
                parse_started = time.monotonic_ns()
                parsed = policy.response_parser(response, handle)
                self.add_timing(
                    "response_parse_ms",
                    (time.monotonic_ns() - parse_started) / 1_000_000,
                )
                validation_started = time.monotonic_ns()
                validated = policy.result_validator(parsed, batch, handle)
                self.add_timing(
                    "validation_ms",
                    (time.monotonic_ns() - validation_started) / 1_000_000,
                )
                commit_started = time.monotonic_ns()
                committed = policy.result_committer(validated, batch, handle)
                self.add_timing(
                    "commit_ms", (time.monotonic_ns() - commit_started) / 1_000_000
                )
                history_started = time.monotonic_ns()
                policy.history_writer(validated, handle)
                self.add_timing(
                    "history_write_ms",
                    (time.monotonic_ns() - history_started) / 1_000_000,
                )
            except TaskCancelled:
                handle.complete(status="cancelled", processed_count=len(results))
                raise
            except Exception:
                handle.complete(status="failed", processed_count=len(results))
                raise
            results.append(validated)
            event = handle.complete(
                status="completed",
                processed_count=len(results),
                committed_count=int(committed or 0),
            )
            policy.on_batch_complete(event)
        self.finish(status="completed")
        return results

    @contextmanager
    def phase(self, field_name: str):
        started_ns = time.monotonic_ns()
        try:
            yield
        finally:
            self.add_timing(field_name, (time.monotonic_ns() - started_ns) / 1_000_000)

    def finish(self, *, status: str) -> dict[str, Any]:
        self.metrics.total_ms = max(
            self.metrics.total_ms,
            round((time.monotonic_ns() - self.started_ns) / 1_000_000),
        )
        return self.emit("run_complete", status=status, elapsed_ms=self.metrics.total_ms)

    def metrics_snapshot(self) -> dict[str, Any]:
        snapshot = self.metrics.as_dict()
        snapshot.update(
            {
                "task_id": self.task_id,
                "run_id": self.run_id,
                "operation": self.operation,
                "provider": self.provider,
                "model": self.model,
            }
        )
        return snapshot


def format_batch_event(event: Mapping[str, Any]) -> str:
    """Chinese, bounded rendering used by non-Smart-Review TaskSession sinks."""
    kind = str(event.get("kind") or "")
    prefix = (
        f"[{event.get('operation') or 'batch'}]"
        f"[批次 {event.get('batch_index', '?')}/{event.get('batch_total', '?')}]"
    )
    if kind == "batch_start":
        return (
            f"{prefix} 開始：{event.get('batch_size', 0)} 筆；"
            f"provider={event.get('provider') or '-'}，model={event.get('model') or '-'}。"
        )
    if kind == "attempt_start":
        return (
            f"{prefix}[第 {event.get('attempt', '?')} 次嘗試] API 請求開始；"
            f"預期 {event.get('batch_size', 0)} 筆。"
        )
    if kind == "provider_attempt_start":
        return (
            f"{prefix}[Provider 第 {event.get('provider_attempt', '?')} 次嘗試] "
            f"請求開始；模型={event.get('provider_model') or event.get('model') or '-'}，"
            f"預期 {event.get('expected_count', event.get('batch_size', 0))} 筆。"
        )
    if kind == "provider_attempt_success":
        return (
            f"{prefix}[Provider 第 {event.get('provider_attempt', '?')} 次嘗試] "
            f"API 成功：狀態碼={event.get('http_status', '?')}，"
            f"finish_reason={event.get('finish_reason') or '-'}，"
            f"預期/回傳={event.get('expected_count', '?')}/{event.get('actual_count', '?')}，"
            f"prompt/output tokens={event.get('prompt_tokens', '-')}/"
            f"{event.get('output_tokens', '-')}。"
        )
    if kind == "provider_attempt_failure":
        return (
            f"{prefix}[Provider 第 {event.get('provider_attempt', '?')} 次嘗試] "
            f"API 失敗：狀態碼={event.get('http_status', '?')}，"
            f"錯誤分類={event.get('error_class', 'unknown')}，"
            f"逾時={event.get('timeout', False)}，過載={event.get('overload', False)}，"
            f"速率限制={event.get('rate_limit', False)}。"
        )
    if kind == "network_attempt_start":
        return (
            f"{prefix}[網路第 {event.get('network_attempt', '?')}/"
            f"{event.get('network_total', '?')} 次] 請求開始。"
        )
    if kind == "network_response":
        return (
            f"{prefix}[網路第 {event.get('network_attempt', '?')}/"
            f"{event.get('network_total', '?')} 次] 回應："
            f"狀態碼={event.get('http_status', '?')}。"
        )
    if kind == "network_failure":
        return (
            f"{prefix}[網路第 {event.get('network_attempt', '?')}/"
            f"{event.get('network_total', '?')} 次] 失敗："
            f"錯誤分類={event.get('error_kind', event.get('error_class', 'unknown'))}，"
            f"是否重試={event.get('retryable', False)}。"
        )
    if kind == "attempt_success":
        return (
            f"{prefix}[第 {event.get('attempt', '?')} 次嘗試] API 成功："
            f"耗時 {event.get('elapsed_ms', 0)} ms，"
            f"狀態碼={event.get('http_status', '?')}，"
            f"預期/回傳={event.get('expected_count', '?')}/{event.get('actual_count', '?')}。"
        )
    if kind == "attempt_failure":
        return (
            f"{prefix}[第 {event.get('attempt', '?')} 次嘗試] API 失敗："
            f"狀態碼={event.get('http_status', '?')}，"
            f"錯誤分類={event.get('error_class', event.get('error_kind', 'unknown'))}，"
            f"逾時={event.get('timeout', False)}，過載={event.get('overload', False)}，"
            f"速率限制={event.get('rate_limit', False)}，"
            f"Retry-After={event.get('retry_after_ms', '?')} ms，"
            f"是否重試={event.get('will_retry', False)}。"
        )
    if kind == "retry_wait":
        return (
            f"{prefix}[第 {event.get('attempt', '?')} 次嘗試] 重試等待："
            f"第 {event.get('retry_number', '?')} 次，"
            f"等待 {event.get('wait_ms', 0)} ms；"
            f"原因={event.get('retry_reason') or 'transient'}。"
        )
    if kind == "network_retry_wait":
        return (
            f"{prefix}[網路第 {event.get('network_attempt', '?')} 次] "
            f"網路重試等待 {event.get('wait_ms', '?')} ms。"
        )
    if kind == "rate_limit_wait":
        return f"{prefix} 配額等待：{event.get('wait_ms', '?')} ms。"
    if kind == "rate_limit_resume":
        return f"{prefix} 配額等待結束，繼續請求。"
    if kind in {"retry_resume", "network_retry_resume"}:
        return f"{prefix} 重試等待結束，繼續請求。"
    if kind == "timing":
        return (
            f"{prefix} 計時：{event.get('field_name') or '-'} "
            f"{event.get('elapsed_ms', 0)} ms。"
        )
    if kind == "key_rotation":
        return f"{prefix} 重試策略切換 API Key：原因={event.get('reason') or '-'}。"
    if kind == "batch_complete":
        return (
            f"{prefix} 完成：已處理 {event.get('processed_count', 0)} 筆，"
            f"API {event.get('api_ms', event.get('elapsed_ms', 0))} ms，"
            f"解析 {event.get('response_parse_ms', 0)} ms，"
            f"驗證 {event.get('validation_ms', 0)} ms，"
            f"寫入 {event.get('commit_ms', 0)} ms，"
            f"重試 {event.get('retry_count', 0)} 次，狀態={event.get('status') or '-'}。"
        )
    if kind == "run_complete":
        return f"[{event.get('operation') or 'batch'}] 任務完成：狀態={event.get('status') or '-'}。"
    return f"{prefix} {kind}。"


__all__ = [
    "BatchEvent",
    "BatchHandle",
    "BatchMetrics",
    "BatchPolicy",
    "CommonBatchExecutor",
    "classify_provider_error",
    "format_batch_event",
]
