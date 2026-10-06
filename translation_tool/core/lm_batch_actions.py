"""Pure error-to-action decisions for the batch translation state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BatchAction(StrEnum):
    """State-machine actions; execution remains owned by the caller."""

    RETRY_SAME_MODEL = "retry_same_model"
    RETRY_SAME_KEY = "retry_same_key"
    ROTATE_KEY = "rotate_key"
    NEXT_MODEL = "next_model"
    SHRINK_BATCH = "shrink_batch"
    FAIL = "fail"
    PARTIAL = "partial"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class BatchActionDecision:
    """Immutable result of classifying an error and choosing its next action."""

    action: BatchAction
    reason: str


def decide_batch_action(
    error_kind: str,
    *,
    quota_kind: str | None = None,
    has_alternative_key: bool = True,
    has_next_model: bool = True,
    overload_count: int = 0,
    overload_threshold: int = 3,
    has_failed_precondition: bool = False,
    has_output_token_cap_fallback: bool = False,
) -> BatchActionDecision:
    """Map an error classification to a retry action without mutating state."""
    if error_kind == "model_missing":
        return BatchActionDecision(BatchAction.NEXT_MODEL, "model_missing")
    if error_kind == "key_forbidden":
        if has_alternative_key:
            return BatchActionDecision(BatchAction.ROTATE_KEY, "key_forbidden")
        return BatchActionDecision(BatchAction.FAIL, "all_keys_forbidden")
    if error_kind == "invalid_argument":
        if has_failed_precondition:
            return BatchActionDecision(BatchAction.FAIL, "failed_precondition")
        if has_output_token_cap_fallback and has_next_model:
            return BatchActionDecision(BatchAction.NEXT_MODEL, "model_output_cap")
        return BatchActionDecision(BatchAction.SHRINK_BATCH, "invalid_argument")
    if error_kind == "rate_limited":
        if quota_kind == "rpd":
            # 同專案模式：每日配額算在「專案 × 模型」，換 key 沒有幫助；
            # 改試下一個還有額度的模型，全部模型都用完才算耗盡。
            if has_next_model:
                return BatchActionDecision(BatchAction.NEXT_MODEL, "daily_quota_model")
            return BatchActionDecision(BatchAction.EXHAUSTED, "daily_quota")
        if quota_kind == "rpm":
            return BatchActionDecision(BatchAction.RETRY_SAME_KEY, "minute_quota")
        if has_alternative_key:
            return BatchActionDecision(BatchAction.ROTATE_KEY, "quota")
        return BatchActionDecision(BatchAction.EXHAUSTED, "quota")
    if error_kind == "service_unavailable":
        if quota_kind == "overloaded":
            if overload_count < overload_threshold:
                return BatchActionDecision(
                    BatchAction.RETRY_SAME_MODEL, "overload_backoff"
                )
            if has_alternative_key:
                return BatchActionDecision(BatchAction.ROTATE_KEY, "overload_threshold")
            return BatchActionDecision(BatchAction.PARTIAL, "overload_no_key")
        if has_next_model:
            return BatchActionDecision(BatchAction.NEXT_MODEL, "service_unavailable")
        return BatchActionDecision(BatchAction.SHRINK_BATCH, "service_unavailable")
    if error_kind in {"deadline_exceeded", "timeout", "server_error"}:
        return BatchActionDecision(BatchAction.SHRINK_BATCH, error_kind)
    if error_kind == "unknown":
        return BatchActionDecision(BatchAction.FAIL, "unknown")
    return BatchActionDecision(BatchAction.FAIL, error_kind)
