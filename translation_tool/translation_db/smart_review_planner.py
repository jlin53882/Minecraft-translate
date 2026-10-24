"""可恢復智慧校對的歷史規劃。

這裡只做純規劃，不呼叫 provider，也不修改翻譯。新 run 最多讀取兩份
可解析的 JSON manifest；精確執行模式只有在完整 CAS（含 local context）快照
完全相同時才可重用。快速預估模式可以略過 context，只能作為估算。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.smart_review_status import (
    PROCESSED_STATUSES,
    SmartReviewStatus,
)

_SNAPSHOT_KEYS = (
    "kind",
    "mc_version",
    "mod_id",
    "entry_key",
    "en_us",
    "current_zh_tw",
    "effective_source",
    "review_status",
    "effective_revision",
    "manual_zh_tw",
    "manual_checker",
    "manual_review_status",
    "manual_revision",
    "context",
)


class SmartReviewPlanMode(str, Enum):
    """指出規劃結果是快速預估，還是可直接執行的精確規劃。"""

    FAST_ESTIMATE = "fast_estimate"
    EXACT_EXECUTION = "exact_execution"


@dataclass(frozen=True)
class SmartReviewPlanInput:
    """新批次規劃共用的不可變範圍輸入。

    預估與實際執行都由同一組欄位建立候選與歷史查詢；差異只在規劃模式：
    快速預估可略過 local context，精確執行必須載入並比對完整 context。
    """

    version: str | None
    mod_id: str | None
    kind: str | None
    effective_source: int | None
    review_status: str
    query: str | None
    limit: int
    provider: str | None
    model: str | None
    use_recent_history: bool
    skip_valid_history: bool
    require_reviewed_reference: bool = False
    prioritize_named_keys: bool = False

    def candidate_kwargs(self, *, include_context: bool) -> dict[str, Any]:
        """回傳預估與實際執行共用的候選查詢參數。"""
        return {
            "version": self.version,
            "mod_id": self.mod_id,
            "kind": self.kind,
            "effective_source": self.effective_source,
            "review_status": self.review_status,
            "query": self.query,
            "limit": self.limit,
            "include_context": include_context,
            "require_reviewed_reference": self.require_reviewed_reference,
            "prioritize_named_keys": self.prioritize_named_keys,
        }

    def scope(self) -> dict[str, Any]:
        """回傳要保存到 run record 的穩定範圍快照。"""
        return {
            "version": self.version,
            "mod_id": self.mod_id,
            "kind": self.kind,
            "effective_source": self.effective_source,
            "review_status": self.review_status,
            "query": self.query or "",
            "require_reviewed_reference": self.require_reviewed_reference,
            "prioritize_named_keys": self.prioritize_named_keys,
        }


def _fingerprint_value(key: str, value: Any) -> Any:
    """把 JSON list 與 dataclass tuple 的 context 正規化成同一表示。"""
    if key != "context":
        return value
    return json.dumps(
        value or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def snapshot_fingerprint(
    snapshot: SmartReviewSnapshot, *, include_context: bool = True
) -> tuple[Any, ...]:
    """建立與 JSON ``original_snapshot`` 相容的穩定 CAS 指紋。"""
    keys = _SNAPSHOT_KEYS if include_context else _SNAPSHOT_KEYS[:-1]
    return tuple(
        _fingerprint_value(
            key, snapshot.key if key == "entry_key" else getattr(snapshot, key, None)
        )
        for key in keys
    )


def payload_fingerprint(
    candidate: Mapping[str, Any], *, include_context: bool = True
) -> tuple[Any, ...]:
    """讀取歷史候選的原始快照，兼容 schema v1 舊資料。"""
    original = candidate.get("original_snapshot")
    source = original if isinstance(original, Mapping) else candidate
    keys = _SNAPSHOT_KEYS if include_context else _SNAPSHOT_KEYS[:-1]
    return tuple(
        _fingerprint_value(
            key, source.get("entry_key") if key == "entry_key" else source.get(key)
        )
        for key in keys
    )


def _candidate_result(
    candidate: Mapping[str, Any], ordinal: int
) -> dict[str, Any] | None:
    """把可重用的歷史候選轉成目前 run 可套用的結果字典。"""
    status = candidate.get("ai_status") or candidate.get("status")
    if status not in PROCESSED_STATUSES:
        return None
    if status == SmartReviewStatus.REQUEST_FAILED:
        return None
    decision = candidate.get("decision")
    if decision not in {"no_change", "suggest_revision", "needs_context"}:
        return None
    return {
        "id": str(ordinal),
        "decision": decision,
        "suggested_zh_tw": candidate.get("suggested_zh_tw") or "",
        "issue_types": list(candidate.get("issue_types") or []),
        "evidence_ids": list(candidate.get("evidence_ids") or []),
        "confidence": float(candidate.get("confidence") or 0),
        "reason": candidate.get("reason") or "沿用最近一次有效的校對結果。",
        "reused_from_run_id": candidate.get("run_id"),
        "reused_status": candidate.get("status"),
        "reused_ai_status": candidate.get("ai_status") or status,
        "reused_applied_batch": candidate.get("applied_batch"),
        "reused_applied_at": candidate.get("applied_at"),
    }


@dataclass(frozen=True)
class SmartReviewPlan:
    """新 run 的處理計畫；``selected`` 保持原候選範圍與 ordinal。"""

    selected: tuple[SmartReviewSnapshot, ...]
    to_process: tuple[SmartReviewSnapshot, ...]
    reused_results: tuple[dict[str, Any], ...]
    reused_count: int
    actual_ai_count: int
    history_runs_consulted: int
    applied_skipped_count: int = 0
    no_change_skipped_count: int = 0
    pending_review_count: int = 0
    invalidated_count: int = 0
    mode: SmartReviewPlanMode = SmartReviewPlanMode.EXACT_EXECUTION

    @property
    def to_process_count(self) -> int:
        """回傳本計畫未重用、需要送入 provider 的候選數。"""
        return len(self.to_process)

    @property
    def is_authoritative(self) -> bool:
        """只有精確執行計畫可以作為實際送出數量的依據。"""
        return self.mode is SmartReviewPlanMode.EXACT_EXECUTION

    def summary(self, *, batch_size: int) -> SmartReviewPlanSummary:
        """建立供 UI／log 使用的計畫摘要，不混淆預估與實際數量。"""
        normalized_batch = max(1, int(batch_size))
        return SmartReviewPlanSummary(
            mode=self.mode,
            selected_count=len(self.selected),
            reused_count=self.reused_count,
            to_process_count=self.to_process_count,
            pending_review_count=self.pending_review_count,
            history_runs_consulted=self.history_runs_consulted,
            batch_count=(self.to_process_count + normalized_batch - 1)
            // normalized_batch
            if self.to_process_count
            else 0,
        )


@dataclass(frozen=True)
class SmartReviewPlanSummary:
    """不論快速預估或精確執行都可安全顯示的計畫摘要。"""

    mode: SmartReviewPlanMode
    selected_count: int
    reused_count: int
    to_process_count: int
    pending_review_count: int
    history_runs_consulted: int
    batch_count: int

    @property
    def is_authoritative(self) -> bool:
        """指出摘要是否來自真正執行前的完整 CAS 規劃。"""
        return self.mode is SmartReviewPlanMode.EXACT_EXECUTION

    def as_dict(self) -> dict[str, Any]:
        """轉成 UI/service 可傳輸的摘要欄位。"""
        return {
            "mode": self.mode.value,
            "is_authoritative": self.is_authoritative,
            "selected_count": self.selected_count,
            "reused_count": self.reused_count,
            "to_process_count": self.to_process_count,
            "pending_review_count": self.pending_review_count,
            "history_runs_consulted": self.history_runs_consulted,
            "batch_count": self.batch_count,
        }


def _choose_historical_result(
    matching: Sequence[Mapping[str, Any]],
    ordinal: int,
    *,
    skip_valid_history: bool,
) -> tuple[dict[str, Any] | None, str, str]:
    """選擇最新可用候選，必要時只回看暫時失敗的較舊結果。"""
    if not matching:
        return None, "", "process"
    candidate = matching[0]
    status = str(candidate.get("status") or "")
    result = _candidate_result(candidate, ordinal)
    if result is not None and status in {
        SmartReviewStatus.APPLIED,
        SmartReviewStatus.NO_CHANGE,
    }:
        if not skip_valid_history:
            return None, status, "rerun_valid"
        reason = (
            "applied_skipped"
            if status == SmartReviewStatus.APPLIED
            else "no_change_skipped"
        )
        return result, status, reason
    if result is not None:
        return result, status, "reuse"
    if status not in {
        SmartReviewStatus.REQUEST_FAILED,
        SmartReviewStatus.PENDING,
        SmartReviewStatus.CANCELLED,
    }:
        return None, status, "process"
    for older in matching[1:]:
        older_result = _candidate_result(older, ordinal)
        if older_result is None:
            continue
        older_status = str(older.get("status") or "")
        if (
            older_status
            in {
                SmartReviewStatus.APPLIED,
                SmartReviewStatus.NO_CHANGE,
            }
            and not skip_valid_history
        ):
            continue
        reason = (
            "applied_skipped"
            if older_status == SmartReviewStatus.APPLIED
            else "no_change_skipped"
            if older_status == SmartReviewStatus.NO_CHANGE
            else "reuse"
        )
        return older_result, older_status, reason
    return None, status, "process"


def plan_new_smart_review(
    snapshots: Sequence[SmartReviewSnapshot],
    historical_candidates: Sequence[Mapping[str, Any]],
    *,
    skip_valid_history: bool,
    history_runs_consulted: int,
    provider: str | None = None,
    model: str | None = None,
    compare_context: bool | None = None,
    plan_input: SmartReviewPlanInput | None = None,
    mode: SmartReviewPlanMode | None = None,
) -> SmartReviewPlan:
    """依最近歷史規劃可重用結果，限制由呼叫端保證為最近兩份 manifest。

    ``FAST_ESTIMATE`` 僅適用於不會送出模型請求的預估，讓預估不必為數萬筆
    候選建立完整 local context；``EXACT_EXECUTION`` 才能作為實際送出依據。
    ``compare_context`` 保留給既有呼叫端，``False`` 等同快速預估、``True``
    等同精確執行。
    """
    if plan_input is not None:
        provider = plan_input.provider
        model = plan_input.model
    legacy_mode = (
        SmartReviewPlanMode.FAST_ESTIMATE
        if compare_context is False
        else SmartReviewPlanMode.EXACT_EXECUTION
    )
    if mode is None:
        mode = legacy_mode
    elif compare_context is not None and mode is not legacy_mode:
        raise ValueError("compare_context 與 mode 指定了互相衝突的規劃模式")
    include_context = mode is SmartReviewPlanMode.EXACT_EXECUTION
    candidates_by_fingerprint: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for candidate in historical_candidates:
        history_provider = candidate.get("_history_provider")
        history_model = candidate.get("_history_model")
        if provider and str(history_provider or "") != str(provider):
            continue
        if model and str(history_model or "") != str(model):
            continue
        candidates_by_fingerprint.setdefault(
            payload_fingerprint(candidate, include_context=include_context), []
        ).append(candidate)

    to_process: list[SmartReviewSnapshot] = []
    reused_results: list[dict[str, Any]] = []
    applied_skipped = 0
    no_change_skipped = 0
    pending_review = 0
    invalidated = 0
    for snapshot in snapshots:
        matching = candidates_by_fingerprint.get(
            snapshot_fingerprint(snapshot, include_context=include_context), []
        )
        result, status, reason = _choose_historical_result(
            matching,
            snapshot.ordinal,
            skip_valid_history=skip_valid_history,
        )
        if reason == "applied_skipped":
            applied_skipped += 1
        elif reason == "no_change_skipped":
            no_change_skipped += 1
        elif reason == "rerun_valid":
            invalidated += 1

        if result is None:
            to_process.append(snapshot)
            continue

        # ready and needs_context remain reviewable history, while rejected
        # preserves the human decision in the new JSON projection.
        ai_status = str(result.get("reused_ai_status") or status)
        if (
            status == SmartReviewStatus.READY
            or ai_status == SmartReviewStatus.READY
            or status == SmartReviewStatus.NEEDS_CONTEXT
        ):
            pending_review += 1
        reused_results.append(result)

    return SmartReviewPlan(
        selected=tuple(snapshots),
        to_process=tuple(to_process),
        reused_results=tuple(reused_results),
        reused_count=len(reused_results),
        actual_ai_count=len(to_process),
        history_runs_consulted=history_runs_consulted,
        applied_skipped_count=applied_skipped,
        no_change_skipped_count=no_change_skipped,
        pending_review_count=pending_review,
        invalidated_count=invalidated,
        mode=mode,
    )


__all__ = [
    "SmartReviewPlan",
    "SmartReviewPlanInput",
    "SmartReviewPlanMode",
    "SmartReviewPlanSummary",
    "payload_fingerprint",
    "plan_new_smart_review",
    "snapshot_fingerprint",
]
