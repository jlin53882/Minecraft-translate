"""智慧校對的驗證修復：只針對 invalid 條目重試一次。

模型回應通過 JSON 結構檢查後，個別條目仍可能驗證失敗。本模組把這些條目連同
``validation_error`` 一起送回模型修復一次；修復結果必須再次通過同一組 strict
驗證才會取代原 invalid 候選，否則維持 invalid。修復請求失敗不會中止批次。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import requests

from translation_tool.core.common_batch_executor import classify_provider_error
from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_REPAIR_SYSTEM_PROMPT,
    SMART_REVIEW_VALIDATION_REPAIR_LIMIT,
    build_smart_review_validation_repair_payload,
    smart_review_response_schema,
)
from translation_tool.translation_db.smart_review_provider import (
    SmartReviewProviderSession,
    _smart_review_response_item_count,
    _SmartReviewRetryRuntime,
)
from translation_tool.translation_db.smart_review_request import (
    SmartReviewAttemptEvents,
)
from translation_tool.translation_db.smart_review_run_state import (
    SmartReviewBatch,
    SmartReviewRunState,
)
from translation_tool.translation_db.smart_review_validation import (
    merge_smart_review_validation_repairs,
    parse_smart_review_response,
)
from translation_tool.utils.cancellation import TaskCancelled


class _ValidationRepair:
    """執行單一批次的 invalid 條目修復請求。"""

    def __init__(
        self,
        session: SmartReviewProviderSession,
        state: SmartReviewRunState,
        batch_ctx: SmartReviewBatch,
        retry_runtime: _SmartReviewRetryRuntime,
        outer_attempt: int,
        repair_batch: Sequence[SmartReviewSnapshot],
        invalid_results: list[dict[str, Any]],
    ) -> None:
        self.session = session
        self.state = state
        self.batch_ctx = batch_ctx
        self.retry_runtime = retry_runtime
        self.attempt = outer_attempt
        self.repair_batch = repair_batch
        self.invalid_results = invalid_results
        self.events: SmartReviewAttemptEvents | None = None

    def _emit(self, kind: str, **fields: Any) -> None:
        self.state.emit_batch_event(
            self.batch_ctx, kind, self.attempt, validation_repair=True, **fields
        )

    def _emit_failure(self, **fields: Any) -> None:
        self._emit("validation_repair_failure", will_retry=False, **fields)

    def announce_start(self) -> None:
        """記錄修復筆數並送出 ``validation_repair_start``。"""
        state, timing = self.state, self.batch_ctx.timing
        timing["validation_repair_attempts"] += 1
        timing["validation_repair_items"] += len(self.repair_batch)
        state.bump("validation_repair_attempts")
        state.bump("validation_repair_items", len(self.repair_batch))
        self._emit(
            "validation_repair_start",
            expected_count=len(self.repair_batch),
            validation_errors={
                str(result.get("id")): str(result.get("validation_error") or "")[:160]
                for result in self.invalid_results
            },
        )

    def build_request(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """建立只含 invalid 條目的修復 payload 與回應 schema。"""
        prepare_started = time.monotonic()
        payload = build_smart_review_validation_repair_payload(
            self.repair_batch, self.invalid_results
        )
        response_schema = smart_review_response_schema(
            [str(item.ordinal) for item in self.repair_batch]
        )
        prepare_seconds = time.monotonic() - prepare_started
        self.state.add_elapsed(self.batch_ctx, "preparation_seconds", prepare_seconds)
        self.state.executor.add_timing("prompt_build_ms", prepare_seconds * 1000)
        return payload, response_schema

    def begin_attempt(self) -> None:
        """登記修復請求的嘗試序號並送出 ``validation_repair_attempt_start``。"""
        state = self.state
        repair_attempt = int(state.metrics["smart_review_attempts"]) + 1
        state.metrics["smart_review_attempts"] = repair_attempt
        if state.current_batch_handle is not None:
            state.current_batch_handle.attempt = repair_attempt
        self.attempt = repair_attempt
        self._emit(
            "validation_repair_attempt_start",
            expected_count=len(self.repair_batch),
            provider=self.session.provider,
            model=self.session.model_name,
        )

    def wait_for_slot(self) -> bool:
        """等待配額時鐘；被取消時回傳 False。"""
        from translation_tool.core.lm_translator_main import _wait_for_request_slot

        events = SmartReviewAttemptEvents(
            self.state, self.batch_ctx, self.attempt, validation_repair=True
        )
        self.events = events
        wait_started = time.monotonic()
        try:
            _wait_for_request_slot(
                self.retry_runtime,
                self.session.model_name,
                cancel_check=self.session.cancel_check,
                wait_event_callback=events.on_rate_limit_event,
            )
        except TaskCancelled:
            self.state.cancelled = True
            return False
        finally:
            self.state.add_elapsed(
                self.batch_ctx,
                "rate_limit_wait_seconds",
                time.monotonic() - wait_started,
            )
        return True

    def call_provider(
        self, payload: dict[str, Any], response_schema: dict[str, Any], api_key: str
    ) -> tuple[str, dict[str, Any], float] | None:
        """送出修復請求；失敗或被取消時回傳 None。"""
        state, batch_ctx = self.state, self.batch_ctx
        meta: dict[str, Any] = {}
        raw = ""
        api_started = time.monotonic()
        failed = False
        state.executor.metrics.request_attempts += 1
        state.executor.metrics.provider_attempts += 1
        try:
            raw = self.session.call(
                system_prompt=SMART_REVIEW_REPAIR_SYSTEM_PROMPT,
                payload=payload,
                api_key=api_key,
                meta_out=meta,
                response_schema=response_schema,
                network_event_callback=self.events.on_network_event,
            )
        except TaskCancelled:
            state.record_usage(meta)
            state.cancelled = True
            failed = True
        except Exception as exc:  # noqa: BLE001 - safe repair event
            state.record_usage(meta)
            failed = True
            self._record_request_error(exc)
        finally:
            api_seconds = time.monotonic() - api_started
            state.add_elapsed(batch_ctx, "api_seconds", api_seconds)
            state.add_elapsed(batch_ctx, "validation_repair_api_seconds", api_seconds)
            state.executor.add_timing("api_ms", api_seconds * 1000)
        if failed or state.cancelled:
            return None
        return raw, meta, api_seconds

    def _record_request_error(self, exc: Exception) -> None:
        """記錄修復請求失敗的安全分類；批次維持原 invalid 結果。"""
        status = getattr(exc, "status", None)
        if isinstance(exc, requests.HTTPError):
            status = exc.response.status_code if exc.response is not None else status
        classification = classify_provider_error(exc, status)
        self.state.bump("validation_repair_request_errors")
        self._emit_failure(
            http_status=classification["http_status"],
            error_kind=classification["error_class"],
            error_class=classification["error_class"],
            provider_error_code=classification["provider_error_code"],
            exception_type=classification["exception_type"],
            provider_overload=classification["overload"],
            quota_or_rate_limit=classification["rate_limit"],
            timeout=classification["timeout"],
            connection=classification["connection"],
            retry_after_ms=classification["retry_after_ms"],
        )

    def announce_success(
        self, raw: str, meta: dict[str, Any], api_seconds: float
    ) -> None:
        """記錄 usage 並送出 ``validation_repair_attempt_success``。"""
        self.state.record_usage(meta)
        self._emit(
            "validation_repair_attempt_success",
            api_elapsed_seconds=api_seconds,
            http_status=meta.get("http_status", 200),
            finish_reason=meta.get("finish_reason"),
            expected_count=len(self.repair_batch),
            actual_count=_smart_review_response_item_count(raw),
            prompt_tokens=meta.get("prompt_tokens"),
            output_tokens=meta.get("output_tokens") or meta.get("candidates_tokens"),
        )

    def parse(self, raw: str) -> list[dict[str, Any]]:
        """以相同的 strict 驗證解析修復回應；結構錯誤時回傳空清單。"""
        state, batch_ctx = self.state, self.batch_ctx
        parse_started = time.monotonic()
        try:
            return parse_smart_review_response(raw, self.repair_batch)
        except Exception as exc:  # noqa: BLE001 - strict parser
            state.bump("validation_repair_response_invalid_count")
            self._emit_failure(
                error_kind="schema_or_parse_error",
                error_class="schema_or_parse_error",
                exception_type=type(exc).__name__,
            )
            return []
        finally:
            parse_seconds = time.monotonic() - parse_started
            state.add_elapsed(batch_ctx, "parse_seconds", parse_seconds)
            state.add_elapsed(
                batch_ctx, "validation_repair_parse_seconds", parse_seconds
            )
            state.executor.add_timing("response_parse_ms", parse_seconds * 1000)

    def merge(
        self,
        results: list[dict[str, Any]],
        repaired_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """用再次驗證的修復結果取代原 invalid 候選並記錄統計。"""
        state, timing = self.state, self.batch_ctx.timing
        merged, repaired_valid, repaired_invalid = (
            merge_smart_review_validation_repairs(results, repaired_results)
        )
        timing["repaired_valid_count"] += repaired_valid
        timing["repaired_invalid_count"] += repaired_invalid
        state.bump("repaired_valid_count", repaired_valid)
        state.bump("repaired_invalid_count", repaired_invalid)
        self._emit(
            "validation_repair_complete",
            repaired_valid_count=repaired_valid,
            repaired_invalid_count=repaired_invalid,
        )
        return merged


def repair_invalid_results(
    session: SmartReviewProviderSession,
    state: SmartReviewRunState,
    batch_ctx: SmartReviewBatch,
    batch: Sequence[SmartReviewSnapshot],
    results: list[dict[str, Any]],
    retry_runtime: _SmartReviewRetryRuntime,
    outer_attempt: int,
) -> list[dict[str, Any]]:
    """只重試單批 invalid 條目一次，並在再次驗證失敗時保留 invalid。"""
    invalid_results = [result for result in results if result.get("validation_error")]
    if not invalid_results or SMART_REVIEW_VALIDATION_REPAIR_LIMIT <= 0:
        return results
    invalid_ids = {str(result.get("id")) for result in invalid_results}
    repair_batch = [item for item in batch if str(item.ordinal) in invalid_ids]
    if not repair_batch:
        return results

    repair = _ValidationRepair(
        session,
        state,
        batch_ctx,
        retry_runtime,
        outer_attempt,
        repair_batch,
        invalid_results,
    )
    repair.announce_start()
    payload, response_schema = repair.build_request()
    api_key = session.claim_api_key()
    if session.provider == "gemini" and not api_key:
        state.bump("validation_repair_request_errors")
        repair._emit_failure(
            error_kind="missing_api_key",
            error_class="authentication",
            exception_type="ApiKeyCycleExhausted",
        )
        return results
    repair.begin_attempt()
    if not repair.wait_for_slot():
        return results
    response = repair.call_provider(payload, response_schema, api_key)
    if response is None:
        return results
    raw, meta, api_seconds = response
    repair.announce_success(raw, meta, api_seconds)
    repaired_results = repair.parse(raw)
    if not repaired_results:
        return results
    return repair.merge(results, repaired_results)
