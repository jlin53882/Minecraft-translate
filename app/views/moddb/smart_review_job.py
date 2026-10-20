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
    get_moddb_smart_review_run_breakdown_from_path,
    run_moddb_smart_review_from_path,
)
from app.tasks.operation_registry import (
    CommitPolicy,
    DurabilityPolicy,
    launch_page_operation,
)
from app.tasks.task_session import TaskSession, tag_session
from app.ui.design import C
from app.ui.snack import show_snack
from translation_tool.utils.log_unit import log_warning

_JOB_ATTR = "_smart_review_job"
_STATUS_LABEL = {
    "completed": "完成",
    "partial": "部分完成",
    "cancelled": "已取消",
    "failed": "失敗",
}


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
    status: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    result: Any = None
    error: str | None = None
    breakdown: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False

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
        remaining = max(0, self.selected - self.processed)
        return self.elapsed(now) / self.processed * remaining


def format_duration(seconds: float | None) -> str:
    """把秒數格式化為 ``mm:ss`` 或 ``h:mm:ss``；未知時顯示 ``--:--``。"""
    if seconds is None:
        return "--:--"
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _format_timing(seconds: float | None) -> str:
    """把診斷用的耗時格式化成毫秒或秒，避免短階段都顯示成 00:00。"""
    if seconds is None:
        return "--"
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "--"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


def _event_bool(value: object) -> str:
    """把結構化事件中的布林欄位格式化成穩定的 log 值。"""
    return "是" if value is True else "否"


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
        session.add_log(
            f"開始智慧校對：{request.scope_label()}；模型 {request.model}；"
            f"上限 {request.limit:,} 筆、每批 {request.batch_size} 筆。",
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
        last = {"processed": 0, "valid": 0, "invalid": 0, "errors": 0}

        def progress(snapshot: dict[str, Any]) -> None:
            """把核心回報的進度快照寫入狀態並轉成逐批 log。"""
            if snapshot.get("run_id") and not state.run_id:
                state.run_id = str(snapshot["run_id"])
                session.add_log(f"已建立批次 {state.run_id}", "info")
            state.selected = int(snapshot.get("selected", state.selected) or 0)
            state.processed = int(snapshot.get("processed", 0) or 0)
            state.valid = int(snapshot.get("valid", 0) or 0)
            state.invalid = int(snapshot.get("invalid", 0) or 0)
            state.request_errors = int(snapshot.get("request_errors", 0) or 0)
            state.status = str(snapshot.get("status", "running"))
            session.set_progress(state.fraction)
            phase = str(snapshot.get("phase") or "")
            timing = snapshot.get("timing")
            event = snapshot.get("event")
            if isinstance(event, dict):
                self._log_event(session, event)
            if phase == "selection_complete" and isinstance(timing, dict):
                session.add_log(
                    f"範圍搜尋完成：選取 {state.selected:,} 筆；"
                    f"搜尋 {_format_timing(timing.get('selection_seconds'))}、"
                    f"建立批次 {_format_timing(timing.get('run_record_seconds'))}。",
                    "info",
                )
            delta = state.processed - last["processed"]
            if delta <= 0:
                return
            new_errors = state.request_errors - last["errors"]
            if phase == "batch_complete" and isinstance(timing, dict) and not event:
                session.add_log(
                    f"已處理 {state.processed:,} / {state.selected:,} 筆；"
                    f"第 {timing.get('batch_number', '?')} 批 "
                    f"（{timing.get('ordinal_start', '?')}～"
                    f"{timing.get('ordinal_end', '?')}）完成："
                    f"準備 {_format_timing(timing.get('preparation_seconds'))}、"
                    f"等待配額 {_format_timing(timing.get('rate_limit_wait_seconds'))}、"
                    f"重試等待 {_format_timing(timing.get('retry_wait_seconds'))}、"
                    f"API {_format_timing(timing.get('api_seconds'))}、"
                    f"解析 {_format_timing(timing.get('parse_seconds'))}、"
                    f"寫入 JSON {_format_timing(timing.get('result_write_seconds'))}、"
                    f"本批 {_format_timing(timing.get('elapsed_seconds'))}；"
                    f"請求 {timing.get('attempts', 0)} 次。",
                    "warning" if new_errors else "info",
                )
            else:
                session.add_log(
                    f"已處理 {state.processed:,} / {state.selected:,} 筆"
                    f"（本批 +{delta}）：有效 +{state.valid - last['valid']}、"
                    f"無效 +{state.invalid - last['invalid']}、"
                    f"請求錯誤 +{new_errors}；預估剩餘 "
                    f"{format_duration(state.eta_seconds())}",
                    "warning" if new_errors else "info",
                )
            last.update(
                processed=state.processed,
                valid=state.valid,
                invalid=state.invalid,
                errors=state.request_errors,
            )

        return progress

    def _log_event(self, session: TaskSession, event: dict[str, Any]) -> None:
        """把核心的安全事件轉成可讀的 TaskSession 日誌。"""
        kind = str(event.get("kind") or "")
        if kind == "batch_start":
            self._log_batch_start(session, event)
        elif kind in {"attempt_start", "attempt_success", "attempt_failure"}:
            self._log_attempt_event(session, event)
        elif kind in {
            "retry_wait",
            "retry_resume",
            "rate_limit_wait",
            "rate_limit_resume",
        }:
            self._log_retry_event(session, event)
        elif kind.startswith("network_"):
            self._log_network_event(session, event)
        elif kind == "batch_complete":
            self._log_batch_complete(session, event)

    @staticmethod
    def _event_prefix(event: dict[str, Any]) -> str:
        """建立 Smart Review 事件的穩定批次前綴。"""
        return (
            f"[智慧校對][批次 {event.get('batch_number', '?')}/"
            f"{event.get('batch_total', '?')}]"
        )

    def _log_batch_start(self, session: TaskSession, event: dict[str, Any]) -> None:
        """記錄批次開始事件。"""
        prefix = self._event_prefix(event)
        session.add_log(
            f"{prefix} 開始：{event.get('expected_count', '?')} 筆，"
            f"序號 {event.get('ordinal_start', '?')}～"
            f"{event.get('ordinal_end', '?')}，"
            f"供應商={event.get('provider', '?')}，"
            f"模型={event.get('model', '?')}",
            "info",
        )

    def _log_attempt_event(self, session: TaskSession, event: dict[str, Any]) -> None:
        """記錄外層 Smart Review attempt 的開始、成功或失敗。"""
        prefix = self._event_prefix(event)
        attempt = event.get("smart_review_attempt", "?")
        kind = event.get("kind")
        if kind == "attempt_start":
            session.add_log(
                f"{prefix}[第 {attempt} 次嘗試] API 請求開始："
                f"校對請求序號={attempt}，"
                f"預期 {event.get('expected_count', '?')} 筆，"
                f"已觀察網路請求={event.get('network_attempts', 0)}。",
                "info",
            )
            return
        if kind == "attempt_success":
            prompt = event.get("prompt_tokens")
            output = event.get("output_tokens")
            token_text = (
                f"提示詞 token={prompt}、輸出 token={output}"
                if prompt is not None or output is not None
                else "token=未提供"
            )
            session.add_log(
                f"{prefix}[第 {attempt} 次嘗試] API 成功："
                f"耗時={_format_timing(event.get('api_elapsed_seconds'))}，"
                f"狀態碼={event.get('http_status', '?')}，"
                f"完成原因={event.get('finish_reason') or '-'}，"
                f"預期/回傳={event.get('expected_count', '?')}/"
                f"{event.get('actual_count', '?')}，{token_text}，"
                f"網路請求={event.get('network_attempts', 0)}。",
                "info",
            )
            return
        session.add_log(
            f"{prefix}[第 {attempt} 次嘗試] API 失敗："
            f"狀態碼={event.get('http_status', '?')}，"
            f"錯誤分類={event.get('error_kind', 'unknown')}，"
            f"例外類型={event.get('exception_type', 'Exception')}，"
            f"服務過載={_event_bool(event.get('provider_overload'))}，"
            f"配額／速率限制={_event_bool(event.get('quota_or_rate_limit'))}，"
            f"逾時={_event_bool(event.get('timeout'))}，"
            f"Retry-After={_format_timing(event.get('retry_after_seconds'))}，"
            f"API 金鑰輪替={_event_bool(event.get('key_rotated'))}。",
            "warning",
        )

    def _log_retry_event(self, session: TaskSession, event: dict[str, Any]) -> None:
        """記錄 provider retry 或 quota clock 的等待與恢復。"""
        prefix = self._event_prefix(event)
        attempt = event.get("smart_review_attempt", "?")
        kind = event.get("kind")
        if kind == "retry_wait":
            session.add_log(
                f"{prefix}[第 {attempt} 次嘗試] 第 "
                f"{event.get('retry_number', '?')}/{event.get('retry_total', '?')} "
                f"次重試，等待 {_format_timing(event.get('wait_seconds'))}；"
                f"錯誤分類={event.get('error_kind', 'unknown')}，"
                f"狀態碼={event.get('http_status', '?')}，"
                f"Retry-After={_format_timing(event.get('retry_after_seconds'))}。",
                "warning",
            )
        elif kind == "retry_resume":
            session.add_log(
                f"{prefix}[第 {attempt} 次嘗試] 重試等待結束，繼續下一次 API 請求。",
                "info",
            )
        elif kind == "rate_limit_wait":
            session.add_log(
                f"{prefix}[第 {attempt} 次嘗試] 等待配額時鐘，"
                f"預計 {_format_timing(event.get('wait_seconds'))}。",
                "info",
            )
        else:
            session.add_log(
                f"{prefix}[第 {attempt} 次嘗試] 配額時鐘等待結束，準備送出 API 請求。",
                "info",
            )

    def _log_network_event(self, session: TaskSession, event: dict[str, Any]) -> None:
        """記錄底層 network attempt 與 connection retry。"""
        prefix = self._event_prefix(event)
        attempt = event.get("smart_review_attempt", "?")
        network_attempt = event.get("network_attempt", "?")
        network_total = event.get("network_total", "?")
        scope = event.get("network_scope")
        scope_labels = {
            "chatgpt_provider_call": "ChatGPT 供應商請求",
        }
        scope_label = scope_labels.get(str(scope), scope) if scope else None
        scope_text = f"，範圍={scope_label}" if scope_label else ""
        kind = event.get("kind")
        if kind == "network_attempt_start":
            text = (
                f"{prefix}[第 {attempt} 次嘗試] 網路請求="
                f"{network_attempt}/{network_total}{scope_text} 開始。"
            )
            level = "info"
        elif kind == "network_failure":
            text = (
                f"{prefix}[第 {attempt} 次嘗試] 網路請求失敗："
                f"請求={network_attempt}/{network_total}"
                f"{scope_text}"
                f"，錯誤分類={event.get('error_kind', 'unknown')}，"
                f"例外類型={event.get('exception_type', 'Exception')}。"
            )
            level = "warning"
        elif kind == "network_retry_wait":
            text = (
                f"{prefix}[第 {attempt} 次嘗試] 網路重試，"
                f"等待 {_format_timing(event.get('wait_seconds'))}；"
                f"網路請求={network_attempt}/{network_total}。"
            )
            level = "warning"
        elif kind == "network_retry_resume":
            text = (
                f"{prefix}[第 {attempt} 次嘗試] 網路重試等待結束，"
                f"繼續後續網路請求（目前 {network_attempt}/{network_total}）。"
            )
            level = "info"
        else:
            text = (
                f"{prefix}[第 {attempt} 次嘗試] 網路回應："
                f"網路請求={network_attempt}/{network_total}"
                f"{scope_text}，"
                f"狀態碼={event.get('http_status', '?')}。"
            )
            level = "info"
        session.add_log(text, level)

    def _log_batch_complete(self, session: TaskSession, event: dict[str, Any]) -> None:
        """記錄批次耗時、結果分類與 validation error 分布。"""
        prefix = self._event_prefix(event)
        errors = event.get("validation_errors") or {}
        error_text = (
            "、".join(
                f"{message} × {count:,}" for message, count in list(errors.items())[:5]
            )
            or "無"
        )
        session.add_log(
            f"{prefix} 完成：序號 {event.get('ordinal_start', '?')}～"
            f"{event.get('ordinal_end', '?')}，實際 {event.get('actual_count', '?')} 筆；"
            f"校對請求 {event.get('smart_review_attempts', 0)}、"
            f"重試 {event.get('retry_count', 0)}、"
            f"配額等待 {_format_timing(event.get('rate_limit_wait_seconds'))}、"
            f"重試等待 {_format_timing(event.get('retry_wait_seconds'))}、"
            f"網路重試等待 {_format_timing(event.get('network_retry_wait_seconds'))}、"
            f"API {_format_timing(event.get('api_seconds'))}、"
            f"解析 {_format_timing(event.get('parse_seconds'))}、"
            f"寫入 JSON {_format_timing(event.get('result_write_seconds'))}、"
            f"本批 {_format_timing(event.get('elapsed_seconds'))}；"
            f"有效={event.get('valid_count', 0)}、"
            f"無效={event.get('invalid_count', 0)}、"
            f"需補充上下文={event.get('needs_context_count', 0)}、"
            f"不變更={event.get('no_change_count', 0)}、"
            f"建議修訂={event.get('suggest_revision_count', event.get('suggestion_count', 0))}；"
            f"請求錯誤={event.get('request_errors', 0)}；"
            f"驗證錯誤={error_text}。",
            "warning" if event.get("invalid_count", 0) else "info",
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
        state.status = result.status
        state.finished_at = time.monotonic()
        self._load_breakdown(request, result.run_id, session)
        label = _STATUS_LABEL.get(result.status, result.status)
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
            }
        )
        if result.status == "failed":
            session.set_error()
        state.phase = "done"

    def _log_timing_breakdown(self, result: Any, session: TaskSession) -> None:
        """把搜尋、API、retry 與 JSON 寫入的總耗時寫入執行紀錄。"""
        metrics = getattr(result, "metrics", None)
        if not isinstance(metrics, dict) or "selection_seconds" not in metrics:
            return

        def seconds(common_key: str, legacy_key: str | None = None) -> float:
            """把秒數格式化為可讀的計時文字；沒有數值時顯示 --:--。"""
            value = metrics.get(common_key)
            if value is not None:
                try:
                    return max(0.0, float(value) / 1000)
                except (TypeError, ValueError):
                    pass
            return max(0.0, float(metrics.get(legacy_key or common_key) or 0))

        total = seconds("total_ms", "duration_seconds")
        session.add_log(
            f"耗時拆解：搜尋 {_format_timing(metrics.get('selection_seconds'))}、"
            f"規劃 {_format_timing(seconds('planning_ms', 'planning_seconds'))}、"
            f"提示詞 {_format_timing(seconds('prompt_build_ms', 'preparation_seconds'))}、"
            f"API {_format_timing(seconds('api_ms', 'api_seconds'))}、"
            f"等待配額 {_format_timing(seconds('quota_wait_ms', 'rate_limit_wait_seconds'))}、"
            f"重試等待 {_format_timing(seconds('retry_backoff_ms', 'retry_wait_seconds'))}、"
            f"網路重試等待 {_format_timing(seconds('network_retry_wait_ms'))}、"
            f"解析 {_format_timing(seconds('response_parse_ms', 'parse_seconds'))}、"
            f"驗證 {_format_timing(seconds('validation_ms'))}、"
            f"寫入 JSON {_format_timing(seconds('history_write_ms', 'result_write_seconds'))}、"
            f"總計 {_format_timing(total)}；"
            f"共 {metrics.get('batch_count', 0)} 批、"
            f"校對請求 {metrics.get('request_attempts', metrics.get('smart_review_attempts', 0))} 次、"
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
        label = _STATUS_LABEL.get(state.status, state.status)
        show_snack(
            self.page,
            f"智慧校對{label}：處理 {state.processed:,} 筆，有效 {state.valid:,}、"
            f"無效 {state.invalid:,}。到條目校對按「智慧校對」即可審查。",
            C.GOLD if state.status in {"partial", "cancelled"} else C.EM,
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
