"""可恢復智慧校對的歷史規劃。

這裡只做純規劃，不呼叫 provider，也不修改翻譯。新 run 最多讀取兩份
可解析的 JSON manifest；歷史候選只有在 CAS 快照完全相同時才可重用。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


def _fingerprint_value(key: str, value: Any) -> Any:
    """把 JSON list 與 dataclass tuple 的 context 正規化成同一表示。"""
    if key != "context":
        return value
    return json.dumps(
        value or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def snapshot_fingerprint(snapshot: SmartReviewSnapshot) -> tuple[Any, ...]:
    """建立與 JSON ``original_snapshot`` 相容的穩定 CAS 指紋。"""
    return tuple(
        _fingerprint_value(
            key, snapshot.key if key == "entry_key" else getattr(snapshot, key, None)
        )
        for key in _SNAPSHOT_KEYS
    )


def payload_fingerprint(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """讀取歷史候選的原始快照，兼容 schema v1 舊資料。"""
    original = candidate.get("original_snapshot")
    source = original if isinstance(original, Mapping) else candidate
    return tuple(
        _fingerprint_value(
            key, source.get("entry_key") if key == "entry_key" else source.get(key)
        )
        for key in _SNAPSHOT_KEYS
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
) -> SmartReviewPlan:
    """依最近歷史規劃可重用結果，限制由呼叫端保證為最近兩份 manifest。"""
    candidates_by_fingerprint: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for candidate in historical_candidates:
        history_provider = candidate.get("_history_provider")
        history_model = candidate.get("_history_model")
        if provider and str(history_provider or "") != str(provider):
            continue
        if model and str(history_model or "") != str(model):
            continue
        candidates_by_fingerprint.setdefault(payload_fingerprint(candidate), []).append(
            candidate
        )

    to_process: list[SmartReviewSnapshot] = []
    reused_results: list[dict[str, Any]] = []
    applied_skipped = 0
    no_change_skipped = 0
    pending_review = 0
    invalidated = 0
    for snapshot in snapshots:
        matching = candidates_by_fingerprint.get(snapshot_fingerprint(snapshot), [])
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
    )


__all__ = [
    "SmartReviewPlan",
    "payload_fingerprint",
    "plan_new_smart_review",
    "snapshot_fingerprint",
]
