"""智慧校對單一批次的 provider 請求與錯誤處置。

``request_batch_response`` 為一個邏輯批次送出請求並處理 key 輪替、暫時性錯誤
重試、ChatGPT／HTTP 錯誤與批次縮小訊號。回傳的 ``BatchRequestOutcome`` 只描述
結果（原始回應或失敗／縮小／中止旗標）；回應的解析與驗證由呼叫端負責。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import requests

from translation_tool.core.batch_events import canonicalize_event
from translation_tool.core.batch_timing import timing_ms
from translation_tool.core.codex_oauth import ChatGPTOAuthError
from translation_tool.core.lm_batch_actions import BatchAction
from translation_tool.core.lm_key_health import get_model_quota_registry
from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.core.retry_after import parse_response_retry_after
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_SYSTEM_PROMPT,
)
from translation_tool.translation_db.smart_review_provider import (
    SmartReviewProviderSession,
    _smart_review_response_item_count,
    _smart_review_response_needs_shrink,
    _smart_review_transient_retry,
    _SmartReviewRetryRuntime,
)
from translation_tool.translation_db.smart_review_run_state import (
    SmartReviewBatch,
    SmartReviewRunState,
)
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


class SmartReviewAttemptEvents:
    """把 API client 與配額時鐘的回呼轉成帶批次資訊的 Smart Review 事件。"""

    def __init__(
        self,
        state: SmartReviewRunState,
        batch: SmartReviewBatch,
        attempt: int,
        *,
        validation_repair: bool = False,
    ) -> None:
        self.state = state
        self.batch = batch
        self.attempt = attempt
        self.extra: dict[str, Any] = (
            {"validation_repair": True} if validation_repair else {}
        )

    def _emit(self, kind: str, event: dict[str, object]) -> None:
        fields = {
            key: value
            for key, value in canonicalize_event(event).items()
            if key != "kind"
        }
        self.state.emit_batch_event(
            self.batch, kind, self.attempt, **self.extra, **fields
        )

    def record_retry_event(self, event: dict[str, Any]) -> None:
        """累計 retry wait 並把安全事件轉回 Smart Review。"""
        state, batch = self.state, self.batch
        event = canonicalize_event(event)
        kind = str(event.get("kind") or "retry")
        wait_seconds = (timing_ms(event, "wait_ms") or 0) / 1000
        if kind == "retry_wait":
            state.add_timing_seconds(batch, "retry_backoff_ms", wait_seconds)
        elif kind == "network_retry_wait":
            state.bump("network_retry_count")
            state.add_timing_seconds(batch, "network_retry_wait_ms", wait_seconds)
        self._emit(kind, event)

    def on_network_event(self, event: dict[str, object]) -> None:
        """接收 API client 的 network attempt 診斷。"""
        kind = str(event.get("kind") or "network_event")
        if kind == "network_attempt_start":
            self.state.bump("network_attempts")
        if kind == "network_retry_wait":
            self.record_retry_event(event)
            return
        self._emit(kind, event)

    def on_rate_limit_event(self, event: dict[str, object]) -> None:
        """把配額時鐘等待轉成 UI 可顯示的事件。"""
        self._emit(str(event.get("kind") or "rate_limit_wait"), event)


@dataclass
class BatchRequestOutcome:
    """一個批次的請求結果；request_started 代表進入 provider request 邊界。"""

    raw: str = ""
    attempt: int = 0
    request_failed: bool = False
    abort_remaining: bool = False
    shrink_requested: bool = False
    stop_reason: str | None = None
    request_started: bool = False


def _http_retry_after(exc: requests.HTTPError) -> float | None:
    """從 Gemini HTTP 錯誤回應（header 或 RetryInfo）讀取 Retry-After 秒數。"""
    retry_after = parse_response_retry_after(exc.response)
    response = exc.response
    status = getattr(response, "status_code", None)
    # 429 is a rate-limit wait, not a promise that the application should
    # sleep indefinitely.  Keep the raw value for 503 because the dedicated
    # transient-retry policy uses it to reject waits over its own limit.
    if status == 429 and (retry_after is None or retry_after < 0 or retry_after > 120):
        return None
    return retry_after


class _BatchRequester:
    """對單一批次重複送出請求，直到成功、失敗、被取消或需縮小批次。"""

    def __init__(
        self,
        session: SmartReviewProviderSession,
        state: SmartReviewRunState,
        batch_ctx: SmartReviewBatch,
        batch: Sequence[SmartReviewSnapshot],
        payload: dict[str, Any],
        response_schema: dict[str, Any],
        retry_runtime: _SmartReviewRetryRuntime,
        current_size: int,
    ) -> None:
        self.session = session
        self.state = state
        self.batch_ctx = batch_ctx
        self.batch = batch
        self.ordinals = [item.ordinal for item in batch]
        self.payload = payload
        self.response_schema = response_schema
        self.retry_runtime = retry_runtime
        self.current_size = current_size
        self.outcome = BatchRequestOutcome()
        self.events: SmartReviewAttemptEvents | None = None
        self.attempt_meta: dict[str, Any] | None = None
        self.api_seconds = 0.0

    def run(self) -> BatchRequestOutcome:
        """重複嘗試直到取得可處理的結果。"""
        outcome = self.outcome
        while True:
            request_api_key = self.session.claim_api_key()
            if self.session.provider == "gemini" and not request_api_key:
                self.state.mark_request_failure(
                    self.ordinals,
                    error_code="request_failed:ApiKeyCycleExhausted",
                    permanent=True,
                    stop_reason="quota_exhausted",
                )
                outcome.request_failed = True
                outcome.abort_remaining = True
                outcome.stop_reason = "quota_exhausted"
                return outcome
            outcome.attempt += 1
            self.state.bump("attempts")
            if not self._attempt(request_api_key):
                return outcome

    def _attempt(self, request_api_key: str) -> bool:
        """執行一次嘗試；回傳 True 代表應以同一批次再試一次。"""
        self.events = SmartReviewAttemptEvents(
            self.state, self.batch_ctx, self.outcome.attempt
        )
        self.attempt_meta = None
        try:
            self._send(request_api_key)
        except TaskCancelled:
            return self._on_cancelled()
        except ChatGPTOAuthError as exc:
            return self._on_oauth_error(exc)
        except ChatGPTAPIError as exc:
            return self._on_chatgpt_api_error(exc)
        except requests.HTTPError as exc:
            return self._on_http_error(exc)
        except (requests.Timeout, requests.ConnectionError) as exc:
            return self._on_connection_error(exc)
        except Exception as exc:  # noqa: BLE001 - credential-free class
            return self._on_unexpected_error(exc)
        return self._on_success()

    def _send(self, request_api_key: str) -> None:
        """等待配額時鐘後送出請求，並累計等待與 API 耗時。"""
        from translation_tool.core.lm_translator_main import _wait_for_request_slot

        state, session, batch_ctx = self.state, self.session, self.batch_ctx
        events = self.events
        wait_started = time.monotonic()
        try:
            _wait_for_request_slot(
                self.retry_runtime,
                session.model_name,
                cancel_check=session.cancel_check,
                wait_event_callback=events.on_rate_limit_event,
            )
        finally:
            state.add_timing_seconds(
                batch_ctx, "quota_wait_ms", time.monotonic() - wait_started
            )
        self.attempt_meta = {}
        state.emit_batch_event(
            batch_ctx,
            "attempt_start",
            self.outcome.attempt,
            expected_count=len(self.batch),
            provider=session.provider,
            model=session.model_name,
            network_attempts=int(state.metrics["network_attempts"]),
        )
        self.outcome.request_started = True
        api_started = time.monotonic()
        try:
            self.outcome.raw = session.call(
                system_prompt=SMART_REVIEW_SYSTEM_PROMPT,
                payload=self.payload,
                api_key=request_api_key,
                meta_out=self.attempt_meta,
                response_schema=self.response_schema,
                network_event_callback=events.on_network_event,
            )
        finally:
            self.api_seconds = time.monotonic() - api_started
            state.add_timing_seconds(batch_ctx, "api_ms", self.api_seconds)

    # -- 結果處置：回傳 True 代表重試，False 代表停止本批次請求 ------------------

    def _fail_request(
        self,
        error: Exception,
        *,
        status: int | None = None,
        permanent: bool = False,
        abort: bool = False,
        stop_reason: str | None = None,
    ) -> bool:
        """把整批標為請求失敗；``abort`` 代表剩餘批次也不再送出。"""
        self.state.mark_request_failure(
            self.ordinals,
            error,
            status,
            permanent=permanent,
            stop_reason=stop_reason,
        )
        self.outcome.request_failed = True
        self.outcome.abort_remaining = abort
        self.outcome.stop_reason = stop_reason
        return False

    def _emit_failure(
        self,
        error: Exception,
        *,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        key_rotated: bool = False,
        error_kind: str | None = None,
    ) -> None:
        self.state.emit_attempt_failure(
            error,
            batch_number=self.batch_ctx.number,
            attempt=self.outcome.attempt,
            status=status,
            retry_after_seconds=retry_after_seconds,
            key_rotated=key_rotated,
            error_kind=error_kind,
        )

    def _count_transient_retry(self) -> None:
        self.state.bump("retry_count")
        self.state.bump("transient_retry_count")

    def _count_key_rotation(self) -> None:
        self.state.bump("key_rotation_count")
        self.state.bump("retry_count")

    def _on_cancelled(self) -> bool:
        if self.attempt_meta is not None:
            self.state.record_usage(self.attempt_meta)
        self.state.cancelled = True
        return False

    def _on_oauth_error(self, exc: ChatGPTOAuthError) -> bool:
        if self.attempt_meta is not None:
            self.state.record_usage(self.attempt_meta)
        self._emit_failure(exc, error_kind="oauth")
        self.state.mark_request_failure(
            self.ordinals,
            exc,
            error_code="request_failed:chatgpt_oauth",
            permanent=True,
            stop_reason="auth_required",
        )
        self.outcome.request_failed = True
        self.outcome.abort_remaining = True
        self.outcome.stop_reason = "auth_required"
        return False

    def _on_chatgpt_api_error(self, exc: ChatGPTAPIError) -> bool:
        from translation_tool.core.lm_translator_main import _handle_chatgpt_api_error

        state, session, outcome = self.state, self.session, self.outcome
        state.record_usage(self.attempt_meta or {})
        self._emit_failure(
            exc,
            status=exc.status,
            retry_after_seconds=getattr(exc, "retry_after", None),
        )
        try:
            with (
                cancel_scope(session.cancel_check)
                if session.cancel_check is not None
                else nullcontext()
            ):
                action = _handle_chatgpt_api_error(
                    self.retry_runtime,
                    exc,
                    retry_event_callback=self.events.record_retry_event,
                )
        except TaskCancelled:
            state.cancelled = True
            return False
        except RuntimeError:
            from translation_tool.core.lm_translator_main import (
                _is_chatgpt_quota_exhaustion,
            )

            stop_reason = (
                "quota_exhausted" if _is_chatgpt_quota_exhaustion(exc) else None
            )
            return self._fail_request(
                exc,
                status=exc.status,
                permanent=True,
                abort=bool(stop_reason) or exc.status in {400, 401, 403, 404, 422},
                stop_reason=stop_reason,
            )
        if action is BatchAction.SHRINK_BATCH:
            if self.current_size > 1:
                outcome.shrink_requested = True
                return False
            return self._fail_request(exc, status=exc.status)
        if action is BatchAction.RETRY_SAME_MODEL:
            self._count_transient_retry()
            return True
        return self._fail_request(
            exc,
            status=exc.status,
            permanent=True,
            abort=True,
            stop_reason=(
                "quota_exhausted"
                if getattr(exc, "code", None)
                in {
                    "insufficient_quota",
                    "quota_exceeded",
                    "rate_limit_exceeded_quota",
                }
                else None
            ),
        )

    def _on_http_error(self, exc: requests.HTTPError) -> bool:
        from translation_tool.core.lm_translator_main import (
            _payload_error_allows_shrink,
        )

        session = self.session
        self.state.record_usage(self.attempt_meta or {})
        status_code = exc.response.status_code if exc.response is not None else None
        retry_after = _http_retry_after(exc)
        if status_code == 429 and retry_after:
            get_model_quota_registry().defer_request_slot(
                session.provider, session.model_name, retry_after
            )
        key_cycle = session.key_cycle
        if (
            key_cycle is not None
            and status_code in {403, 429}
            and key_cycle.mark_failed(
                reason="forbidden" if status_code == 403 else None
            )
        ):
            self._count_key_rotation()
            self._emit_failure(
                exc,
                status=status_code,
                retry_after_seconds=retry_after,
                key_rotated=True,
            )
            return True
        if _payload_error_allows_shrink(exc, status_code) and self.current_size > 1:
            self._emit_failure(exc, status=status_code, retry_after_seconds=retry_after)
            self.outcome.shrink_requested = True
            return False
        if status_code in {500, 502, 503, 504}:
            return self._on_server_error(exc, status_code, retry_after)
        self._emit_failure(exc, status=status_code, retry_after_seconds=retry_after)
        client_error = status_code in {400, 404, 422}
        return self._fail_request(
            exc, status=status_code, permanent=client_error, abort=client_error
        )

    def _on_server_error(
        self, exc: requests.HTTPError, status_code: int, retry_after: float | None
    ) -> bool:
        """處理 5xx：先嘗試輪替 key，再依既有 policy 做有限次重試。"""
        session = self.session
        key_cycle = session.key_cycle
        if status_code == 503 and key_cycle is not None:
            overload_count = key_cycle.record_overload()
            if overload_count >= 3 and key_cycle.mark_failed():
                self._count_key_rotation()
                self._emit_failure(
                    exc,
                    status=status_code,
                    retry_after_seconds=retry_after,
                    key_rotated=True,
                )
                return True
        self._emit_failure(exc, status=status_code, retry_after_seconds=retry_after)
        if _smart_review_transient_retry(
            self.retry_runtime,
            session.model_name,
            exc,
            status_code,
            cancel_check=session.cancel_check,
            event_callback=self.events.record_retry_event,
        ):
            self._count_transient_retry()
            return True
        return self._fail_request(
            exc, status=status_code, abort=True, stop_reason="service_unavailable"
        )

    def _on_connection_error(self, exc: Exception) -> bool:
        self.state.record_usage(self.attempt_meta or {})
        self._emit_failure(exc)
        if _smart_review_transient_retry(
            self.retry_runtime,
            self.session.model_name,
            exc,
            None,
            cancel_check=self.session.cancel_check,
            event_callback=self.events.record_retry_event,
        ):
            self._count_transient_retry()
            return True
        return self._fail_request(exc, abort=True, stop_reason="service_unavailable")

    def _on_unexpected_error(self, exc: Exception) -> bool:
        self.state.record_usage(self.attempt_meta or {})
        self._emit_failure(exc)
        return self._fail_request(exc)

    def _on_success(self) -> bool:
        state, session, outcome = self.state, self.session, self.outcome
        meta = self.attempt_meta or {}
        state.record_usage(meta)
        state.emit_batch_event(
            self.batch_ctx,
            "attempt_success",
            outcome.attempt,
            api_ms=round(self.api_seconds * 1000),
            http_status=meta.get("http_status", 200),
            finish_reason=meta.get("finish_reason"),
            expected_count=len(self.batch),
            actual_count=_smart_review_response_item_count(outcome.raw),
            prompt_tokens=meta.get("prompt_tokens"),
            output_tokens=meta.get("output_tokens") or meta.get("candidates_tokens"),
            network_attempts=int(state.metrics["network_attempts"]),
            network_retry_count=int(state.metrics["network_retry_count"]),
        )
        if session.key_cycle is not None:
            session.key_cycle.record_success()
        self.retry_runtime.gemini_transient_retry_count = 0
        outcome.shrink_requested = (
            _smart_review_response_needs_shrink(outcome.raw, meta)
            and self.current_size > 1
        )
        return False


def request_batch_response(
    session: SmartReviewProviderSession,
    state: SmartReviewRunState,
    batch_ctx: SmartReviewBatch,
    batch: Sequence[SmartReviewSnapshot],
    payload: dict[str, Any],
    response_schema: dict[str, Any],
    retry_runtime: _SmartReviewRetryRuntime,
    current_size: int,
) -> BatchRequestOutcome:
    """為一個批次向 provider 取得回應，回傳結果旗標而不解析內容。"""
    return _BatchRequester(
        session,
        state,
        batch_ctx,
        batch,
        payload,
        response_schema,
        retry_runtime,
        current_size,
    ).run()
