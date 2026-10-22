"""智慧校對的批次執行流程：選取候選、逐批請求、驗證、保存並結算。

模型回應只會被保存成候選，不會直接寫入翻譯。本模組負責流程編排：

* 選出候選並建立 JSON 批次紀錄；
* 依自適應批次大小逐批向 provider 取得回應（``smart_review_request``）；
* 嚴格驗證回應並對 invalid 條目做一次修復（``smart_review_repair``）；
* 保存候選、結算計數與 metrics（``smart_review_run_state``）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from translation_tool.core.common_batch_executor import CommonBatchExecutor
from translation_tool.translation_db.models import (
    SmartReviewRunResult,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.schema import SRC_AI
from translation_tool.translation_db.smart_review_prompt import (
    build_smart_review_payload,
    smart_review_response_schema,
)
from translation_tool.translation_db.smart_review_provider import (
    SmartReviewProviderSession,
    _next_smart_review_batch_size,
    _smart_review_estimated_input_tokens,
    _SmartReviewRetryRuntime,
)
from translation_tool.translation_db.smart_review_repair import repair_invalid_results
from translation_tool.translation_db.smart_review_request import request_batch_response
from translation_tool.translation_db.smart_review_run_state import (
    SmartReviewBatch,
    SmartReviewRunState,
    new_smart_review_metrics,
)
from translation_tool.translation_db.smart_review_status import SmartReviewStatus
from translation_tool.translation_db.smart_review_validation import (
    parse_smart_review_response,
)
from translation_tool.utils.cancellation import TaskCancelled, is_cancelled
from translation_tool.utils.config_manager import get_smart_review_settings


@dataclass
class _BatchCursor:
    """目前批次在候選清單中的位置與自適應批次大小。"""

    position: int
    current_size: int
    adaptive_size: int

    def shrink(self) -> bool:
        """以有界減半縮小目前批次；已無法再縮小時回傳 False。"""
        smaller = _next_smart_review_batch_size(self.current_size)
        if smaller < self.current_size:
            self.current_size = smaller
            self.adaptive_size = min(self.adaptive_size, smaller)
            return True
        return False


class _SmartReviewBatchRunner:
    """依序處理所有候選批次；取消與失敗狀態記錄在 ``SmartReviewRunState``。"""

    def __init__(
        self,
        db: Any,
        session: SmartReviewProviderSession,
        state: SmartReviewRunState,
        snapshots: Sequence[SmartReviewSnapshot],
    ) -> None:
        self.db = db
        self.session = session
        self.state = state
        self.snapshots = snapshots

    def _cancel_requested(self) -> bool:
        cancel_check = self.session.cancel_check
        return is_cancelled() or (cancel_check is not None and cancel_check())

    def run(self, batch_size: int) -> None:
        """處理所有批次，直到完成、被取消或 provider 要求中止。"""
        snapshots, state = self.snapshots, self.state
        cursor = _BatchCursor(position=0, current_size=0, adaptive_size=batch_size)
        batch_number = 0
        while cursor.position < len(snapshots):
            if self._cancel_requested():
                state.cancelled = True
                break
            cursor.current_size = min(
                cursor.adaptive_size, len(snapshots) - cursor.position
            )
            batch_number += 1
            batch_ctx = state.begin_batch(
                number=batch_number,
                size=cursor.current_size,
                ordinal_start=int(snapshots[cursor.position].ordinal),
                ordinal_end=int(
                    snapshots[
                        min(
                            len(snapshots) - 1,
                            cursor.position + cursor.current_size - 1,
                        )
                    ].ordinal
                ),
                provider=self.session.provider,
                model_name=self.session.model_name,
            )
            while self._run_batch_pass(cursor, batch_ctx):
                pass
            if state.cancelled:
                break

    def _run_batch_pass(
        self, cursor: _BatchCursor, batch_ctx: SmartReviewBatch
    ) -> bool:
        """對目前批次執行一輪請求與保存；回傳 True 代表縮小後需重新執行。"""
        from translation_tool.core.lm_translator_main import _batch_interval_seconds

        state, session = self.state, self.session
        if self._cancel_requested():
            state.cancelled = True
            return False
        batch = self.snapshots[cursor.position : cursor.position + cursor.current_size]
        batch_ctx.ordinal_end = int(batch[-1].ordinal)
        payload, response_schema, estimated_input = self._prepare_request(
            batch, batch_ctx
        )
        budget = session.input_token_budget
        if budget is not None and estimated_input > budget:
            return self._reject_over_budget_batch(cursor, batch_ctx, batch)

        retry_runtime = _SmartReviewRetryRuntime(
            provider=session.provider,
            batch_interval_sec=_batch_interval_seconds(session.lm_config),
        )
        outcome = request_batch_response(
            session,
            state,
            batch_ctx,
            batch,
            payload,
            response_schema,
            retry_runtime,
            cursor.current_size,
        )
        if state.cancelled:
            return False
        if outcome.request_failed:
            self._finish_failed_request(
                cursor, batch_ctx, batch, abort_remaining=outcome.abort_remaining
            )
            return False
        if outcome.shrink_requested and cursor.shrink():
            state.bump("shrink_count")
            return True
        results = self._parse_response(outcome.raw, batch, cursor, batch_ctx)
        if results is None:
            return True
        results = repair_invalid_results(
            session,
            state,
            batch_ctx,
            batch,
            results,
            retry_runtime,
            outcome.attempt,
        )
        self._persist_results(results, batch_ctx)
        self._count_results(results, batch_ctx)
        self._finish_batch(cursor, batch_ctx, batch)
        return False

    def _prepare_request(
        self, batch: Sequence[SmartReviewSnapshot], batch_ctx: SmartReviewBatch
    ) -> tuple[dict[str, Any], dict[str, Any], float]:
        """組裝請求 payload／schema 並估算完整輸入 token。"""
        state = self.state
        preparation_started = time.monotonic()
        payload = build_smart_review_payload(batch)
        response_schema = smart_review_response_schema(
            [str(item.ordinal) for item in batch]
        )
        estimated_input = _smart_review_estimated_input_tokens(payload, response_schema)
        state.metrics["last_estimated_input_tokens"] = round(estimated_input, 2)
        preparation_seconds = time.monotonic() - preparation_started
        state.add_timing_seconds(batch_ctx, "prompt_build_ms", preparation_seconds)
        return payload, response_schema, estimated_input

    def _reject_over_budget_batch(
        self,
        cursor: _BatchCursor,
        batch_ctx: SmartReviewBatch,
        batch: Sequence[SmartReviewSnapshot],
    ) -> bool:
        """輸入超過預算：能縮小就縮小重試，否則把整批標為請求失敗。"""
        state = self.state
        if cursor.current_size > 1 and cursor.shrink():
            state.bump("input_budget_shrink_count")
            state.bump("shrink_count")
            return True
        state.bump("input_budget_exceeded_count", len(batch))
        batch_ctx.actual_count = len(batch)
        state.mark_request_failure(
            [item.ordinal for item in batch],
            error_code="request_failed:input_budget_exceeded",
        )
        state.counts.processed += len(batch)
        cursor.position += len(batch)
        state.complete_batch(batch_ctx, "input_budget_exceeded")
        return False

    def _finish_failed_request(
        self,
        cursor: _BatchCursor,
        batch_ctx: SmartReviewBatch,
        batch: Sequence[SmartReviewSnapshot],
        *,
        abort_remaining: bool,
    ) -> None:
        """請求失敗：前進游標；provider 要求中止時把剩餘候選標為中止。"""
        state, snapshots = self.state, self.snapshots
        state.counts.processed += len(batch)
        cursor.position += len(batch)
        if abort_remaining and cursor.position < len(snapshots):
            self.db.mark_smart_review_request_error(
                state.run_id,
                [item.ordinal for item in snapshots[cursor.position :]],
                error_code="request_failed:aborted_after_provider_error",
            )
            state.counts.processed = len(snapshots)
            cursor.position = len(snapshots)
        batch_ctx.actual_count = len(batch)
        state.complete_batch(batch_ctx, SmartReviewStatus.REQUEST_FAILED)

    def _parse_response(
        self,
        raw: str,
        batch: Sequence[SmartReviewSnapshot],
        cursor: _BatchCursor,
        batch_ctx: SmartReviewBatch,
    ) -> list[dict[str, Any]] | None:
        """嚴格解析回應；結構錯誤時優先縮小批次（回傳 None 代表需重試）。"""
        state = self.state
        parse_started = time.monotonic()
        try:
            return parse_smart_review_response(raw, batch)
        except Exception as exc:  # noqa: BLE001 - response contract class
            if cursor.current_size > 1 and cursor.shrink():
                state.bump("shrink_count")
                state.bump("response_invalid_count")
                return None
            state.bump("response_invalid_count", len(batch))
            return [
                {
                    "id": str(item.ordinal),
                    "validation_error": f"response_invalid:{type(exc).__name__}",
                }
                for item in batch
            ]
        finally:
            parse_seconds = time.monotonic() - parse_started
            state.add_timing_seconds(batch_ctx, "response_parse_ms", parse_seconds)

    def _persist_results(
        self, results: list[dict[str, Any]], batch_ctx: SmartReviewBatch
    ) -> None:
        """把候選結果寫入批次紀錄並累計寫入耗時。"""
        state = self.state
        write_started = time.monotonic()
        try:
            self.db.record_smart_review_results(state.run_id, results)
        finally:
            write_seconds = time.monotonic() - write_started
            state.add_timing_seconds(batch_ctx, "history_write_ms", write_seconds)
            state.add_timing_seconds(batch_ctx, "commit_ms", write_seconds)

    def _count_results(
        self, results: list[dict[str, Any]], batch_ctx: SmartReviewBatch
    ) -> None:
        """依驗證結果與 decision 更新有效／無效與各決策計數。"""
        state, counts = self.state, self.state.counts
        validation_started = time.monotonic()
        for result in results:
            if result.get("validation_error"):
                counts.invalid += 1
                reason = str(result["validation_error"])[:160]
                batch_ctx.validation_errors[reason] = (
                    batch_ctx.validation_errors.get(reason, 0) + 1
                )
                validation_errors = state.metrics["validation_errors"]
                validation_errors[reason] = int(validation_errors.get(reason, 0)) + 1
                continue
            counts.valid += 1
            if result["decision"] == "no_change":
                counts.no_change += 1
            elif result["decision"] == "suggest_revision":
                counts.suggestions += 1
            else:
                counts.needs_context += 1
        state.add_timing_seconds(
            batch_ctx, "validation_ms", time.monotonic() - validation_started
        )

    def _finish_batch(
        self,
        cursor: _BatchCursor,
        batch_ctx: SmartReviewBatch,
        batch: Sequence[SmartReviewSnapshot],
    ) -> None:
        """批次完成：前進游標並回報完成事件。"""
        self.state.counts.processed += len(batch)
        cursor.position += len(batch)
        cursor.adaptive_size = min(cursor.adaptive_size, cursor.current_size)
        batch_ctx.actual_count = len(batch)
        self.state.complete_batch(batch_ctx, "completed")


def _open_run_state(
    db: Any,
    session: SmartReviewProviderSession,
    snapshots: Sequence[SmartReviewSnapshot],
    *,
    batch_size: int,
    limit: int,
    version: str | None,
    scope: dict[str, Any],
    task_id: str | None,
    progress: Callable[[dict[str, Any]], None] | None,
    workflow_started: float,
    selection_started: float,
    selection_seconds: float,
) -> SmartReviewRunState:
    """建立批次紀錄與共用執行器，並回報 ``selection_complete``。"""
    planning_started = time.monotonic()
    planned_batch_count = max(1, (len(snapshots) + batch_size - 1) // batch_size)
    planning_seconds = round(time.monotonic() - planning_started, 3)
    run_record_started = time.monotonic()
    run_id = db.create_smart_review_run(
        snapshots,
        provider=session.provider,
        model=session.model_name,
        version=version,
        requested_limit=limit,
        batch_size=batch_size,
        scope=scope,
    )
    run_record_seconds = round(time.monotonic() - run_record_started, 3)
    executor = CommonBatchExecutor(
        operation="smart_review",
        provider=session.provider,
        model=session.model_name,
        task_id=task_id,
        run_id=run_id,
        cancel_check=session.cancel_check,
    )
    executor.add_timing(
        "setup_ms", max(0.0, selection_started - workflow_started) * 1000
    )
    executor.add_timing("selection_ms", selection_seconds * 1000)
    executor.add_timing("planning_ms", planning_seconds * 1000)
    executor.add_timing("run_record_ms", run_record_seconds * 1000)
    executor.add_timing("history_write_ms", run_record_seconds * 1000)
    session.configure_limits()
    state = SmartReviewRunState(
        db=db,
        run_id=run_id,
        executor=executor,
        selected_count=len(snapshots),
        planned_batch_count=planned_batch_count,
        progress=progress,
        metrics=new_smart_review_metrics(input_token_budget=session.input_token_budget),
        started=workflow_started,
    )
    state.report_progress(
        phase="selection_complete",
        timing={
            "selection_ms": round(selection_seconds * 1000),
            "planning_ms": round(planning_seconds * 1000),
            "run_record_ms": round(run_record_seconds * 1000),
        },
    )
    return state


def _finish_run(
    db: Any,
    session: SmartReviewProviderSession,
    state: SmartReviewRunState,
    snapshots: Sequence[SmartReviewSnapshot],
) -> SmartReviewRunResult:
    """正常結束（含被取消）時結算狀態並回傳執行結果。"""
    counts = state.counts
    if state.cancelled:
        db.mark_smart_review_pending_cancelled(state.run_id)
    status = (
        SmartReviewStatus.CANCELLED
        if state.cancelled
        else "partial"
        if counts.request_errors
        else "completed"
    )
    state.close_run(status, include_outcome_counts=True)
    return SmartReviewRunResult(
        run_id=state.run_id,
        provider=session.provider,
        model=session.model_name,
        selected_count=len(snapshots),
        processed_count=counts.processed,
        valid_count=counts.valid,
        invalid_count=counts.invalid,
        request_error_count=counts.request_errors,
        no_change_count=counts.no_change,
        suggestion_count=counts.suggestions,
        needs_context_count=counts.needs_context,
        metrics=state.metrics,
        status=status,
    )


def run_smart_review(
    db,
    *,
    model_name: str,
    api_key: str,
    provider: str = "gemini",
    version: str | None = None,
    limit: int = 1000,
    batch_size: int | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    lm_config: dict[str, Any] | None = None,
    require_reviewed_reference: bool = False,
    prioritize_named_keys: bool = False,
    mod_id: str | None = None,
    kind: str | None = None,
    effective_source: int | None = SRC_AI,
    review_status: str = "unreviewed",
    query: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    task_id: str | None = None,
) -> SmartReviewRunResult:
    """執行有界的 AI 智慧校對，只保存候選，不直接寫入翻譯。"""
    if not model_name.strip():
        raise ValueError("smart review 必須指定模型")
    workflow_started = time.monotonic()
    lm_config = lm_config if isinstance(lm_config, dict) else {}
    smart_review_settings = get_smart_review_settings()
    if batch_size is None:
        batch_size = smart_review_settings["default_batch_size"]
    session = SmartReviewProviderSession.open(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        lm_config=lm_config,
        cancel_check=cancel_check,
    )
    selection_started = time.monotonic()
    snapshots = db.smart_review_candidates(
        version=version,
        mod_id=mod_id,
        kind=kind,
        effective_source=effective_source,
        review_status=review_status,
        query=query,
        limit=limit,
        require_reviewed_reference=require_reviewed_reference,
        prioritize_named_keys=prioritize_named_keys,
    )
    selection_seconds = round(time.monotonic() - selection_started, 3)
    batch_size = max(
        1,
        min(int(batch_size), smart_review_settings["max_batch_size"]),
    )
    state = _open_run_state(
        db,
        session,
        snapshots,
        batch_size=batch_size,
        limit=limit,
        version=version,
        scope={
            "version": version,
            "mod_id": mod_id,
            "kind": kind,
            "effective_source": effective_source,
            "review_status": review_status,
            "query": query or "",
            "require_reviewed_reference": require_reviewed_reference,
            "prioritize_named_keys": prioritize_named_keys,
        },
        task_id=task_id,
        progress=progress,
        workflow_started=workflow_started,
        selection_started=selection_started,
        selection_seconds=selection_seconds,
    )
    try:
        _SmartReviewBatchRunner(db, session, state, snapshots).run(batch_size)
    except TaskCancelled:
        state.cancelled = True
    except BaseException:
        db.mark_smart_review_pending_cancelled(state.run_id)
        state.close_run("failed", include_outcome_counts=False)
        raise
    return _finish_run(db, session, state, snapshots)
