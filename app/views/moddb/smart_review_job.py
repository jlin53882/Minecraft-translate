"""智慧校對的背景執行任務：關閉視窗後仍可繼續，並提供進度與 log。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    FORMATTED_BATCH_EVENT_KINDS,
    batch_event_level,
    format_batch_event,
    format_timing_duration,
    get_moddb_smart_review_run_breakdown_from_path,
    run_moddb_smart_review_from_path,
    timing_seconds,
)
from app.tasks.operation_registry import (
    CommitPolicy,
    DurabilityPolicy,
    launch_page_operation,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.smart_review_run_status import (
    run_status_color,
    run_status_label,
)
from translation_tool.utils.log_unit import log_warning

_JOB_ATTR = "_smart_review_job"


@dataclass
class SmartReviewRequest:
    """一次智慧校對的完整參數，建立後不再變更。"""

    path: Path
    priority: tuple[int, ...]
    model: str
    version: str
    mod_id: str | None
    kind: str | None
    effective_source: int | None
    query: str
    limit: int
    batch_size: int
    use_recent_history: bool = False
    skip_valid_history: bool = False
    resume_run_id: str | None = None
    resume_selected_count: int | None = None
    resume_processed_count: int | None = None
    resume_pending_count: int | None = None

    def scope_label(self) -> str:
        """回傳給人看的範圍摘要，用於 log 與完成通知。"""
        return (
            f"{self.version} · {self.mod_id or '全部模組'} / "
            f"{self.kind or '全部類型'}"
            + (f" · 搜尋「{self.query}」" if self.query else "")
        )


@dataclass
class SmartReviewJobState:
    """任務目前的進度快照；背景執行緒寫入、UI 輪詢讀取。"""

    phase: str = "idle"  # idle / running / done / failed
    request: SmartReviewRequest | None = None
    run_id: str | None = None
    selected: int = 0
    processed: int = 0
    valid: int = 0
    invalid: int = 0
    request_errors: int = 0
    actual_ai: int = 0
    reused: int = 0
    pending_review: int = 0
    excluded: int = 0
    invalidated_by_manual_edit: int = 0
    stop_reason: str | None = None
    status: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    result: Any = None
    error: str | None = None
    breakdown: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    resume_baseline_processed: int = 0
    resume_pending_total: int = 0
    resume_started_at: float = 0.0

    @property
    def fraction(self) -> float:
        """已處理比例（0～1）。"""
        return min(1.0, self.processed / self.selected) if self.selected else 0.0

    def elapsed(self, now: float | None = None) -> float:
        """已執行秒數；結束後固定為總耗時。"""
        end = self.finished_at or (now if now is not None else time.monotonic())
        return max(0.0, end - self.started_at) if self.started_at else 0.0

    def eta_seconds(self, now: float | None = None) -> float | None:
        """依目前速度估算剩餘秒數；資料不足時回傳 None。"""
        if self.phase != "running" or self.processed <= 0 or self.selected <= 0:
            return None
        if self.request is not None and self.request.resume_run_id:
            processed_this_resume = max(
                0, self.processed - self.resume_baseline_processed
            )
            remaining = max(
                0,
                self.resume_pending_total - processed_this_resume,
            )
            if processed_this_resume <= 0 or self.resume_pending_total <= 0:
                return None
            eta_started_at = self.resume_started_at or self.started_at
            end = self.finished_at or (now if now is not None else time.monotonic())
            elapsed = max(0.0, end - eta_started_at) if eta_started_at else 0.0
            return elapsed / processed_this_resume * remaining
        remaining = max(0, self.selected - self.processed)
        return self.elapsed(now) / self.processed * remaining

    @property
    def resume_processed(self) -> int:
        """回傳本次續跑新增處理的數量，不包含歷史批次已處理數量。"""
        if self.request is None or not self.request.resume_run_id:
            return self.processed
        return max(0, self.processed - self.resume_baseline_processed)


def format_duration(seconds: float | None) -> str:
    """把秒數格式化為 ``mm:ss`` 或 ``h:mm:ss``；未知時顯示 ``--:--``。"""
    if seconds is None:
        return "--:--"
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _format_timing(seconds: float | None) -> str:
    """把診斷用的耗時格式化成毫秒、秒或分秒，避免短階段都顯示成 00:00。"""
    return format_timing_duration(seconds)


class SmartReviewJob:
    """頁面層級的智慧校對任務；視窗關閉不會中斷，重新開啟時可接續顯示。"""

    def __init__(self, page: ft.Page) -> None:
        """建立空閒狀態的任務。"""
        self.page = page
        self.state = SmartReviewJobState()
        self.session: TaskSession | None = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._finish_listeners: list[Callable[[SmartReviewJobState], None]] = []
        self._parent_callback: Callable[..., None] | None = None

    @property
    def running(self) -> bool:
        """目前是否有進行中的校對。"""
        return self.state.phase == "running"

    def add_finish_listener(
        self, callback: Callable[[SmartReviewJobState], None]
    ) -> None:
        """登記任務結束時要在 UI 執行緒呼叫的回呼。"""
        with self._lock:
            if callback not in self._finish_listeners:
                self._finish_listeners.append(callback)

    def remove_finish_listener(
        self, callback: Callable[[SmartReviewJobState], None]
    ) -> None:
        """取消登記結束回呼。"""
        with self._lock:
            if callback in self._finish_listeners:
                self._finish_listeners.remove(callback)

    def start(
        self,
        request: SmartReviewRequest,
        *,
        parent_callback: Callable[..., None] | None = None,
    ) -> str | None:
        """啟動背景校對；成功回傳 None，失敗回傳給使用者看的原因。"""
        with self._lock:
            if self.state.phase == "running":
                return "智慧校對正在執行中。"
            self._cancel_event.clear()
            self._parent_callback = parent_callback
            self.state = SmartReviewJobState(
                phase="running",
                request=request,
                status="準備中",
                started_at=time.monotonic(),
                resume_baseline_processed=(
                    max(0, int(request.resume_processed_count or 0))
                    if request.resume_run_id
                    else 0
                ),
                resume_pending_total=(
                    max(0, int(request.resume_pending_count or 0))
                    if request.resume_run_id
                    else 0
                ),
            )
        session = tag_session(
            TaskSession(), "Mod 資料庫智慧校對", "moddb", page=self.page
        )
        self.session = session
        try:
            launched = launch_page_operation(
                self.page,
                lambda: self._work(request, session),
                name="Mod 資料庫智慧校對",
                owner="moddb-smart-review",
                task_session=session,
                commit=CommitPolicy.PARTIAL_ALLOWED,
                durability=DurabilityPolicy.USER_ACTION,
            )
        except (AttributeError, RuntimeError) as exc:
            self._fail_to_launch()
            return f"智慧校對無法啟動：{exc}"
        if not launched:
            self._fail_to_launch()
            return "應用程式正在關閉，未啟動智慧校對。"
        return None

    def _fail_to_launch(self) -> None:
        """啟動失敗時把狀態還原為空閒。"""
        with self._lock:
            self.state = SmartReviewJobState()
        self.session = None

    def cancel(self) -> None:
        """要求停止後續批次；已送出的 API 回應返回後才會停。"""
        if not self.running:
            return
        self._cancel_event.set()
        self.state.cancel_requested = True
        session = self.session
        if session is not None:
            session.request_cancel()
            session.add_log("已要求停止：目前 API 回應返回後不再送下一批。", "warning")

    def _should_cancel(self) -> bool:
        """供核心在批次之間檢查是否要停止。"""
        session = self.session
        return self._cancel_event.is_set() or bool(
            session is not None and session.cancel_requested
        )

    def _work(self, request: SmartReviewRequest, session: TaskSession) -> None:
        """在背景執行校對，並在成功或失敗後通知 UI。"""
        state = self.state
        session.start()
        limit_label = "不限" if request.limit == 0 else f"{request.limit:,}"
        if request.resume_run_id:
            selected_label = request.resume_selected_count or state.selected
            processed_label = request.resume_processed_count or 0
            pending_label = request.resume_pending_count or 0
            session.add_log(
                f"繼續未完成智慧校對：批次 {request.resume_run_id}；"
                f"{request.scope_label()}；模型 {request.model}；"
                f"原批次 {selected_label:,} 筆、"
                f"既有處理 {processed_label:,} 筆、"
                f"本次待處理 {pending_label:,} 筆、"
                f"上限 {limit_label} 筆、每批 {request.batch_size} 筆。",
                "system",
            )
        else:
            session.add_log(
                f"開始智慧校對：{request.scope_label()}；模型 {request.model}；"
                f"上限 {limit_label} 筆、每批 {request.batch_size} 筆。",
                "system",
            )
        try:
            result = run_moddb_smart_review_from_path(
                request.path,
                request.priority,
                model_name=request.model,
                version=request.version,
                mod_id=request.mod_id,
                kind=request.kind,
                effective_source=request.effective_source,
                review_status="unreviewed",
                query=request.query,
                limit=request.limit,
                batch_size=request.batch_size,
                progress=self._progress_handler(session),
                cancel_check=self._should_cancel,
                task_id=session.task_id,
                use_recent_history=request.use_recent_history,
                skip_valid_history=request.skip_valid_history,
                resume_run_id=request.resume_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - job reports bounded failure
            state.error = str(exc) or type(exc).__name__
            state.phase = "failed"
            state.finished_at = time.monotonic()
            session.add_log(f"智慧校對失敗：{state.error}", "error")
            session.set_error()
            session.finish()
            self._notify_finished()
            return
        self._record_result(request, result, session)
        session.finish()
        self._notify_finished()

    def _progress_handler(
        self, session: TaskSession
    ) -> Callable[[dict[str, Any]], None]:
        """建立核心進度回呼：更新狀態，並把每批的增量寫成一行 log。"""
        state = self.state
        last = {
            "processed": (
                state.resume_baseline_processed
                if state.request is not None and state.request.resume_run_id
                else 0
            ),
            "valid": 0,
            "invalid": 0,
            "errors": 0,
        }

        def progress(snapshot: dict[str, Any]) -> None:
            """把核心回報的進度快照寫入狀態並轉成逐批 log。"""
            if snapshot.get("run_id") and not state.run_id:
                state.run_id = str(snapshot["run_id"])
                session.add_log(f"已建立批次 {state.run_id}", "info")
            self._apply_progress_snapshot(state, snapshot)
            if snapshot.get("stop_reason"):
                state.stop_reason = str(snapshot["stop_reason"])
            state.status = str(snapshot.get("status", "running"))
            phase = str(snapshot.get("phase") or "")
            if (
                phase == "selection_complete"
                and state.request is not None
                and state.request.resume_run_id
            ):
                state.resume_started_at = time.monotonic()
            session.set_progress(state.fraction)
            timing = snapshot.get("timing")
            event = snapshot.get("event")
            if isinstance(event, dict):
                self._log_event(session, event)
            if phase == "selection_complete" and isinstance(timing, dict):
                self._log_selection_progress(session, state, timing)
            self._log_progress_delta(session, state, last, phase, timing, event)

        return progress

    @staticmethod
    def _apply_progress_snapshot(
        state: SmartReviewJobState, snapshot: dict[str, Any]
    ) -> None:
        """把核心帳本快照投影到 UI job state。"""
        state.selected = int(snapshot.get("selected", state.selected) or 0)
        state.processed = int(snapshot.get("processed", 0) or 0)
        state.valid = int(snapshot.get("valid", 0) or 0)
        state.invalid = int(snapshot.get("invalid", 0) or 0)
        state.request_errors = int(snapshot.get("request_errors", 0) or 0)
        state.resume_baseline_processed = int(
            snapshot.get("resume_baseline_processed", state.resume_baseline_processed)
            or 0
        )
        state.resume_pending_total = int(
            snapshot.get("resume_pending_total", state.resume_pending_total) or 0
        )
        metrics = snapshot.get("metrics")
        if not isinstance(metrics, dict):
            return
        state.metrics = dict(metrics)
        state.actual_ai = int(metrics.get("actual_ai_count", state.actual_ai) or 0)
        state.reused = int(metrics.get("reused_count", state.reused) or 0)
        state.pending_review = int(
            metrics.get("pending_review_count", state.pending_review) or 0
        )
        state.excluded = int(metrics.get("excluded_count", state.excluded) or 0)
        state.invalidated_by_manual_edit = int(
            metrics.get(
                "invalidated_by_manual_edit_count",
                state.invalidated_by_manual_edit,
            )
            or 0
        )

    @staticmethod
    def _log_selection_progress(
        session: TaskSession, state: SmartReviewJobState, timing: dict[str, Any]
    ) -> None:
        """記錄候選選取與建立批次的耗時。"""
        if state.request is not None and state.request.resume_run_id:
            session.add_log(
                f"續跑準備完成：原批次 {state.selected:,} 筆；"
                f"既有處理 {state.resume_baseline_processed:,} 筆；"
                f"本次待處理 {state.resume_pending_total:,} 筆；"
                f"搜尋 {_format_timing(timing_seconds(timing, 'selection_ms'))}、"
                f"建立批次 {_format_timing(timing_seconds(timing, 'run_record_ms'))}。",
                "info",
            )
            return
        session.add_log(
            f"範圍搜尋完成：選取 {state.selected:,} 筆；"
            f"精確規劃送 AI {int(state.metrics.get('planned_ai_count') or 0):,} 筆；"
            f"歷史重用 {int(state.metrics.get('reused_count') or 0):,} 筆；"
            f"搜尋 {_format_timing(timing_seconds(timing, 'selection_ms'))}、"
            f"建立批次 {_format_timing(timing_seconds(timing, 'run_record_ms'))}。",
            "info",
        )

    @staticmethod
    def _log_progress_delta(
        session: TaskSession,
        state: SmartReviewJobState,
        last: dict[str, int],
        phase: str,
        timing: dict[str, Any] | None,
        event: object,
    ) -> None:
        """記錄一次有處理進度的批次摘要。"""
        delta = state.processed - last["processed"]
        if delta <= 0:
            return
        new_errors = state.request_errors - last["errors"]
        level = "warning" if new_errors else "info"
        if phase == "batch_complete" and isinstance(timing, dict) and not event:
            session.add_log(
                f"已處理 {state.processed:,} / {state.selected:,} 筆；"
                f"第 {timing.get('batch_index', timing.get('batch_number', '?'))} 批 "
                f"（{timing.get('ordinal_start', '?')}～"
                f"{timing.get('ordinal_end', '?')}）完成："
                f"準備 {_format_timing(timing_seconds(timing, 'prompt_build_ms'))}、"
                f"等待配額 {_format_timing(timing_seconds(timing, 'quota_wait_ms'))}、"
                f"重試等待 {_format_timing(timing_seconds(timing, 'retry_backoff_ms'))}、"
                f"API {_format_timing(timing_seconds(timing, 'api_ms'))}、"
                f"解析 {_format_timing(timing_seconds(timing, 'response_parse_ms'))}、"
                f"寫入 JSON {_format_timing(timing_seconds(timing, 'history_write_ms'))}、"
                f"本批 {_format_timing(timing_seconds(timing, 'elapsed_ms'))}；"
                f"請求 {timing.get('attempts', 0)} 次。",
                level,
            )
        else:
            session.add_log(
                f"已處理 {state.processed:,} / {state.selected:,} 筆"
                f"（本批 +{delta}）：有效 +{state.valid - last['valid']}、"
                f"無效 +{state.invalid - last['invalid']}、"
                f"請求錯誤 +{new_errors}；預估剩餘 "
                f"{format_duration(state.eta_seconds())}",
                level,
            )
        last.update(
            processed=state.processed,
            valid=state.valid,
            invalid=state.invalid,
            errors=state.request_errors,
        )

    def _log_event(self, session: TaskSession, event: dict[str, Any]) -> None:
        """把核心的安全事件交給共用 formatter 轉成 TaskSession 日誌。"""
        if str(event.get("kind") or "") not in FORMATTED_BATCH_EVENT_KINDS:
            return
        session.add_log(
            format_batch_event(event, default_operation="smart_review"),
            batch_event_level(event),
        )

    def _record_result(
        self, request: SmartReviewRequest, result: Any, session: TaskSession
    ) -> None:
        """把核心回傳的最終結果寫入狀態、log 與任務摘要。"""
        state = self.state
        state.result = result
        state.run_id = result.run_id
        state.processed = result.processed_count
        state.selected = result.selected_count
        state.valid = result.valid_count
        state.invalid = result.invalid_count
        state.request_errors = result.request_error_count
        metrics = result.metrics if isinstance(result.metrics, dict) else {}
        state.metrics = dict(metrics)
        state.actual_ai = int(metrics.get("actual_ai_count", 0) or 0)
        state.reused = int(metrics.get("reused_count", 0) or 0)
        state.pending_review = int(metrics.get("pending_review_count", 0) or 0)
        state.excluded = int(metrics.get("excluded_count", 0) or 0)
        state.invalidated_by_manual_edit = int(
            metrics.get("invalidated_by_manual_edit_count", 0) or 0
        )
        state.stop_reason = getattr(result, "stop_reason", None)
        state.status = result.status
        state.finished_at = time.monotonic()
        self._load_breakdown(request, result.run_id, session)
        label = run_status_label(result.status)[0]
        session.add_log(
            f"智慧校對{label}：處理 {result.processed_count:,} 筆，有效 "
            f"{result.valid_count:,}、建議 {result.suggestion_count:,}、"
            f"不變 {result.no_change_count:,}、需補充上下文 "
            f"{result.needs_context_count:,}、無效 {result.invalid_count:,}；"
            f"耗時 {format_duration(state.elapsed())}",
            "warning" if result.status in {"failed", "partial"} else "system",
        )
        self._log_timing_breakdown(result, session)
        session.set_progress(1.0 if result.status == "completed" else state.fraction)
        session.set_summary(
            {
                "run_id": result.run_id,
                "status": result.status,
                "processed": result.processed_count,
                "valid": result.valid_count,
                "invalid": result.invalid_count,
                "actual_ai": state.actual_ai,
                "reused": state.reused,
                "pending_review": state.pending_review,
                "stop_reason": state.stop_reason,
            }
        )
        if result.status == "failed":
            session.set_error()
        state.phase = "done"

    def _log_timing_breakdown(self, result: Any, session: TaskSession) -> None:
        """把搜尋、API、retry 與 JSON 寫入的總耗時寫入執行紀錄。"""
        metrics = getattr(result, "metrics", None)
        if (
            not isinstance(metrics, dict)
            or timing_seconds(metrics, "selection_ms") is None
        ):
            return

        def seconds(canonical_ms: str) -> float:
            """取得計時欄位的秒數（先讀 ``*_ms``，再讀舊版秒數欄位）；沒有數值時為 0。"""
            return timing_seconds(metrics, canonical_ms) or 0.0

        total = seconds("total_ms")
        session.add_log(
            f"耗時拆解：搜尋 {_format_timing(seconds('selection_ms'))}、"
            f"規劃 {_format_timing(seconds('planning_ms'))}、"
            f"提示詞 {_format_timing(seconds('prompt_build_ms'))}、"
            f"API {_format_timing(seconds('api_ms'))}、"
            f"等待配額 {_format_timing(seconds('quota_wait_ms'))}、"
            f"重試等待 {_format_timing(seconds('retry_backoff_ms'))}、"
            f"網路重試等待 {_format_timing(seconds('network_retry_wait_ms'))}、"
            f"解析 {_format_timing(seconds('response_parse_ms'))}、"
            f"驗證 {_format_timing(seconds('validation_ms'))}、"
            f"寫入 JSON {_format_timing(seconds('history_write_ms'))}、"
            f"驗證修復 API {_format_timing(timing_seconds(metrics, 'validation_repair_api_ms'))}、"
            f"總計 {_format_timing(total)}；"
            f"共 {metrics.get('batch_count', 0)} 批、"
            f"校對請求 {metrics.get('request_attempts', metrics.get('attempts', 0))} 次、"
            f"網路請求 {metrics.get('network_attempts', 0)} 次、"
            f"重試 {metrics.get('retry_count', 0)} 次、"
            f"網路重試 {metrics.get('network_retry_count', 0)} 次、"
            f"服務過載 {metrics.get('overload_count', 0)} 次、"
            f"API 金鑰輪替 {metrics.get('key_rotation_count', 0)} 次。",
            "system",
        )

    def _load_breakdown(
        self, request: SmartReviewRequest, run_id: str, session: TaskSession
    ) -> None:
        """讀取無效原因統計並寫進 log，讓使用者知道為什麼有些候選不能用。"""
        try:
            breakdown = get_moddb_smart_review_run_breakdown_from_path(
                request.path, request.priority, run_id
            )
        except Exception as exc:  # noqa: BLE001 - statistics are optional
            session.add_log(f"無法讀取無效原因統計：{exc}", "warning")
            return
        self.state.breakdown = breakdown
        for message, count in breakdown.get("errors", []):
            session.add_log(f"無效原因：{message} × {count:,}", "warning")

    def _notify_finished(self) -> None:
        """在 UI 執行緒通知結束：先通知視窗，再通知上層與頁面。"""
        snapshot = self.state
        with self._lock:
            listeners = list(self._finish_listeners)

        def deliver() -> None:
            """依序呼叫登記的回呼，單一回呼失敗不影響其他回呼。"""
            for callback in listeners:
                try:
                    callback(snapshot)
                except Exception as exc:  # noqa: BLE001 - one listener must not block others
                    log_warning(f"智慧校對結束通知回呼失敗：{exc!r}")
            if not listeners:
                self._announce(snapshot)
            parent = self._parent_callback
            if parent is not None and snapshot.result is not None:
                try:
                    parent(snapshot.result)
                except TypeError:
                    parent()

        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):

            async def runner() -> None:
                """在 UI 執行緒執行結束通知。"""
                deliver()

            run_task(runner)
        else:
            deliver()

    def _announce(self, state: SmartReviewJobState) -> None:
        """視窗已關閉時，用提示訊息告知校對結果與去哪裡審查。"""
        if state.phase == "failed":
            show_snack(self.page, f"智慧校對失敗：{state.error}", C.RED)
            return
        label = run_status_label(state.status)[0]
        show_snack(
            self.page,
            f"智慧校對{label}：處理 {state.processed:,} 筆，有效 {state.valid:,}、"
            f"無效 {state.invalid:,}。到條目校對按「智慧校對」即可審查。",
            run_status_color(state.status),
        )


def job_for_page(page: ft.Page) -> SmartReviewJob:
    """取得頁面層級的任務物件；不存在時建立並掛在頁面上。"""
    job = getattr(page, _JOB_ATTR, None)
    if isinstance(job, SmartReviewJob):
        return job
    job = SmartReviewJob(page)
    try:
        setattr(page, _JOB_ATTR, job)
    except (AttributeError, TypeError):
        pass
    return job
