"""智慧校對歷史 JSON 的資料結構與摘要計算。

這個模組只處理「候選／批次字典長什麼樣子」：schema 與儲存格式常數、
候選快照轉 JSON、JSON 轉 ``SmartReviewCandidate``、結果套用到候選、以及由候選
狀態計算的統計。全部是無狀態的純函式，不碰檔案系統也不持有鎖。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.smart_review_status import (
    DECISION_STATUS,
    PROCESSED_STATUSES,
    VALID_STATUSES,
    SmartReviewStatus,
    coerce_status,
)

SCHEMA_VERSION = 1
CHUNKED_STORAGE_FORMAT = "chunked_json_v1"
PROCESSED_CANDIDATE_STATUSES = PROCESSED_STATUSES
VALID_CANDIDATE_STATUSES = VALID_STATUSES
SMART_REVIEW_CAS_SNAPSHOT_FIELDS = (
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
)


def timestamp() -> str:
    """建立與 SQLite CURRENT_TIMESTAMP 相容的 UTC 顯示時間。"""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _copy_context(value: object) -> list[dict[str, Any]]:
    """把 context 正規化成可安全寫入 JSON 的字典清單。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def candidate_counts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """從已落盤候選狀態重建可恢復的批次摘要計數。"""
    candidates = [
        item for item in payload.get("candidates") or [] if isinstance(item, dict)
    ]
    return candidate_counts_from_items(candidates)


def candidate_counts_from_items(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """從候選字典清單建立狀態、錯誤與摘要計數。"""
    statuses = [coerce_status(item.get("status")) for item in candidates]
    ai_statuses: list[str | None] = []
    for item, status in zip(candidates, statuses, strict=True):
        candidate_ai_status = item.get("ai_status")
        if (
            isinstance(candidate_ai_status, str)
            and candidate_ai_status in PROCESSED_CANDIDATE_STATUSES
        ):
            ai_statuses.append(candidate_ai_status)
        elif status in PROCESSED_CANDIDATE_STATUSES:
            ai_statuses.append(status)
        else:
            ai_statuses.append(None)
    status_counts = Counter(statuses)
    error_counts = Counter(
        str(item.get("validation_error"))
        for item in candidates
        if item.get("validation_error")
    )
    return {
        "processed_count": sum(
            status in PROCESSED_CANDIDATE_STATUSES for status in ai_statuses
        ),
        "valid_count": sum(
            status in VALID_CANDIDATE_STATUSES for status in ai_statuses
        ),
        "invalid_count": ai_statuses.count(SmartReviewStatus.INVALID),
        "request_error_count": ai_statuses.count(SmartReviewStatus.REQUEST_FAILED),
        "pending_count": statuses.count(SmartReviewStatus.PENDING),
        "status_counts": dict(status_counts),
        "error_counts": dict(error_counts),
    }


def snapshot_payload(run_id: str, item: SmartReviewSnapshot) -> dict[str, Any]:
    """把候選輸入快照轉成 JSON 結構。"""
    candidate = {
        "run_id": run_id,
        "ordinal": int(item.ordinal),
        "entry_id": int(item.entry_id),
        "kind": item.kind,
        "mc_version": item.mc_version,
        "mod_id": item.mod_id,
        "entry_key": item.key,
        "en_us": item.en_us,
        "current_zh_tw": item.current_zh_tw,
        "effective_source": item.effective_source,
        "review_status": item.review_status,
        "effective_revision": item.effective_revision,
        "manual_zh_tw": item.manual_zh_tw,
        "manual_checker": item.manual_checker,
        "manual_review_status": item.manual_review_status,
        "manual_revision": item.manual_revision,
        "twin_entry_ids": [int(value) for value in item.twin_entry_ids],
        "context": _copy_context(item.context),
        "decision": None,
        "suggested_zh_tw": None,
        "issue_types": [],
        "evidence_ids": [],
        "confidence": None,
        "reason": None,
        "response": None,
        "draft_zh_tw": None,
        "validation_error": None,
        "status": SmartReviewStatus.PENDING,
        "ai_status": None,
        "applied_batch": None,
        "applied_at": None,
    }
    candidate["original_snapshot"] = {
        key: candidate[key] for key in SMART_REVIEW_CAS_SNAPSHOT_FIELDS
    }
    return candidate


def candidate_from_payload(
    run_id: str, payload: dict[str, Any]
) -> SmartReviewCandidate:
    """把 JSON 候選轉回既有的資料模型。"""
    response = payload.get("response")
    reason = payload.get("reason")
    if reason is None and isinstance(response, dict):
        candidate_reason = response.get("reason")
        reason = candidate_reason if isinstance(candidate_reason, str) else None
    issue_types = payload.get("issue_types") or []
    evidence_ids = payload.get("evidence_ids") or []
    return SmartReviewCandidate(
        run_id=run_id,
        ordinal=int(payload.get("ordinal") or 0),
        entry_id=int(payload.get("entry_id") or 0),
        kind=str(payload.get("kind") or ""),
        mc_version=str(payload.get("mc_version") or ""),
        mod_id=str(payload.get("mod_id") or ""),
        key=str(payload.get("entry_key") or ""),
        en_us=str(payload.get("en_us") or ""),
        current_zh_tw=str(payload.get("current_zh_tw") or ""),
        effective_source=(
            None
            if payload.get("effective_source") is None
            else int(payload["effective_source"])
        ),
        review_status=payload.get("review_status"),
        effective_revision=(
            None
            if payload.get("effective_revision") is None
            else int(payload["effective_revision"])
        ),
        context=tuple(_copy_context(payload.get("context"))),
        manual_zh_tw=payload.get("manual_zh_tw"),
        manual_checker=payload.get("manual_checker"),
        manual_review_status=payload.get("manual_review_status"),
        manual_revision=(
            None
            if payload.get("manual_revision") is None
            else int(payload["manual_revision"])
        ),
        decision=payload.get("decision"),
        suggested_zh_tw=payload.get("suggested_zh_tw"),
        issue_types=tuple(str(item) for item in issue_types),
        evidence_ids=tuple(str(item) for item in evidence_ids),
        confidence=(
            None if payload.get("confidence") is None else float(payload["confidence"])
        ),
        reason=reason,
        draft_zh_tw=payload.get("draft_zh_tw"),
        validation_error=payload.get("validation_error"),
        status=coerce_status(payload.get("status")),
        applied_batch=payload.get("applied_batch"),
        applied_at=payload.get("applied_at"),
    )


def is_chunked(payload: Mapping[str, Any]) -> bool:
    """判斷 manifest 是否使用候選 JSON 分片格式。"""
    return payload.get("storage_format") == CHUNKED_STORAGE_FORMAT


def candidate_ai_status(candidate: Mapping[str, Any]) -> str | None:
    """取得候選的 AI 執行結果狀態，不受人工套用狀態影響。"""
    ai_status = candidate.get("ai_status")
    if isinstance(ai_status, str) and ai_status in PROCESSED_CANDIDATE_STATUSES:
        return ai_status
    status = candidate.get("status")
    if isinstance(status, str) and status in PROCESSED_CANDIDATE_STATUSES:
        return status
    return None


def apply_ai_status_delta(
    manifest: dict[str, Any],
    old_ai_status: object,
    new_ai_status: object,
) -> None:
    """更新只代表 AI 執行結果的累計統計，不追蹤人工狀態轉換。"""
    old = old_ai_status if isinstance(old_ai_status, str) else None
    new = new_ai_status if isinstance(new_ai_status, str) else None
    if old == new:
        return
    counters = {
        "processed_count": (
            old in PROCESSED_CANDIDATE_STATUSES,
            new in PROCESSED_CANDIDATE_STATUSES,
        ),
        "valid_count": (
            old in VALID_CANDIDATE_STATUSES,
            new in VALID_CANDIDATE_STATUSES,
        ),
        "invalid_count": (
            old == SmartReviewStatus.INVALID,
            new == SmartReviewStatus.INVALID,
        ),
        "request_error_count": (
            old == SmartReviewStatus.REQUEST_FAILED,
            new == SmartReviewStatus.REQUEST_FAILED,
        ),
    }
    for field, (had_old, has_new) in counters.items():
        value = int(manifest.get(field) or 0)
        if had_old:
            value = max(0, value - 1)
        if has_new:
            value += 1
        manifest[field] = value


def candidate_metadata_delta(
    manifest: dict[str, Any],
    old_status: object,
    new_status: object,
    old_error: object,
    new_error: object,
) -> None:
    """更新分片 checkpoint 後 manifest 的狀態與錯誤摘要。"""
    old = coerce_status(old_status)
    new = coerce_status(new_status)
    statuses = manifest.setdefault("status_counts", {})
    if not isinstance(statuses, dict):
        statuses = {}
        manifest["status_counts"] = statuses
    if old != new:
        statuses[old] = max(0, int(statuses.get(old, 0)) - 1)
        statuses[new] = int(statuses.get(new, 0)) + 1
    errors = manifest.setdefault("error_counts", {})
    if not isinstance(errors, dict):
        errors = {}
        manifest["error_counts"] = errors
    old_key = str(old_error) if old_error else None
    new_key = str(new_error) if new_error else None
    if old_key != new_key:
        if old_key is not None:
            errors[old_key] = max(0, int(errors.get(old_key, 0)) - 1)
        if new_key is not None:
            errors[new_key] = int(errors.get(new_key, 0)) + 1
    for key in list(statuses):
        if int(statuses[key]) <= 0:
            del statuses[key]
    for key in list(errors):
        if int(errors[key]) <= 0:
            del errors[key]


def refresh_status_counts(payload: dict[str, Any]) -> None:
    """單檔批次：候選狀態改變後，以目前候選重算 ``status_counts``。"""
    payload["status_counts"] = dict(
        Counter(
            coerce_status(item.get("status"))
            for item in payload.get("candidates") or []
            if isinstance(item, dict)
        )
    )


def apply_result_delta(
    manifest: dict[str, Any],
    old_status: object,
    new_status: object,
    old_error: object,
    new_error: object,
    old_ai_status: object,
    new_ai_status: object,
) -> None:
    """同時更新目前候選狀態與不可被人工狀態覆蓋的 AI 統計。"""
    candidate_metadata_delta(manifest, old_status, new_status, old_error, new_error)
    apply_ai_status_delta(manifest, old_ai_status, new_ai_status)


def apply_result_to_candidate(
    candidate: dict[str, Any], result: Mapping[str, Any]
) -> tuple[str, str, object, object, str | None, str]:
    """套用一筆已解析結果並回傳狀態、錯誤與 AI 狀態的前後值。"""
    old_status = coerce_status(candidate.get("status"))
    old_error = candidate.get("validation_error")
    old_ai_status = candidate_ai_status(candidate)
    if result.get("validation_error"):
        candidate["status"] = SmartReviewStatus.INVALID
        candidate["validation_error"] = str(result["validation_error"])
        new_ai_status = "invalid"
    else:
        new_ai_status = candidate_status(dict(result))
        candidate.update(
            {
                "decision": result["decision"],
                "suggested_zh_tw": result["suggested_zh_tw"],
                "issue_types": list(result["issue_types"]),
                "evidence_ids": list(result["evidence_ids"]),
                "confidence": float(result["confidence"]),
                "reason": result.get("reason"),
                "response": dict(result),
                "validation_error": None,
                "status": new_ai_status,
                "ai_status": new_ai_status,
            }
        )
    if result.get("validation_error"):
        candidate["ai_status"] = new_ai_status
    return (
        old_status,
        coerce_status(candidate.get("status")),
        old_error,
        candidate.get("validation_error"),
        old_ai_status,
        new_ai_status,
    )


def find_candidate(payload: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
    """在已讀取的批次 payload 中依 ordinal 找到對應候選。"""
    for candidate in payload.get("candidates") or []:
        if (
            isinstance(candidate, dict)
            and int(candidate.get("ordinal") or 0) == ordinal
        ):
            return candidate
    return None


def candidate_status(result: dict[str, Any]) -> str:
    """從已驗證模型結果推導候選狀態。"""
    if result.get("validation_error"):
        return SmartReviewStatus.INVALID
    return DECISION_STATUS[result["decision"]]
