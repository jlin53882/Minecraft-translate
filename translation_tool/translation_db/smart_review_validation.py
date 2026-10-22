"""智慧校對模型回應的 strict 解析與逐筆驗證。

任何模型回應都要先通過本模組的驗證才會被保存成候選；驗證失敗的條目以
``validation_error`` 標記，而不是被丟棄或自動修正。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.quality import translation_issues
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_DECISIONS,
    SMART_REVIEW_ISSUE_TYPES,
)
from translation_tool.translation_db.smart_review_status import (
    DECISION_STATUS,
    MANUAL_EDIT_STATUSES,
    SmartReviewStatus,
)

_MAX_REASON_LENGTH = 2_000
_MAX_SUGGESTION_LENGTH = 32_000
# 審查者可手動編輯並覆蓋套用的候選狀態；``ready`` 走原本的 AI 建議套用流程。
SMART_REVIEW_MANUAL_EDIT_STATUSES = MANUAL_EDIT_STATUSES
_URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def merge_smart_review_validation_repairs(
    results: Sequence[dict[str, Any]],
    repaired_results: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    """以再次嚴格驗證的結果取代原 invalid 候選並回傳修復統計。"""
    repaired_by_id = {
        str(result.get("id")): result
        for result in repaired_results
        if isinstance(result, dict)
    }
    merged: list[dict[str, Any]] = []
    repaired_valid = 0
    repaired_invalid = 0
    for result in results:
        result_id = str(result.get("id"))
        if not result.get("validation_error"):
            merged.append(result)
            continue
        repaired = repaired_by_id.get(result_id)
        if repaired is None:
            merged.append(result)
        else:
            merged.append(repaired)
            if repaired.get("validation_error"):
                repaired_invalid += 1
            else:
                repaired_valid += 1
    return merged, repaired_valid, repaired_invalid


def _strict_json_object(raw: str) -> dict[str, Any]:
    """確認模型回應是可解析的 JSON 物件。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("回應不是非空 JSON 文字")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("回應不是合法 JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"items"}:
        raise ValueError("回應最外層必須只有 items")
    if not isinstance(parsed["items"], list):
        raise TypeError("回應 items 必須是陣列")
    return parsed


def _validate_text(value: object, field: str, *, max_length: int) -> str:
    """驗證候選文字符合智慧校對的內容限制。"""
    if not isinstance(value, str):
        raise TypeError(f"{field} 必須是字串")
    if len(value) > max_length:
        raise ValueError(f"{field} 超過長度上限")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValueError(f"{field} 含無效 Unicode") from exc
    return value


def _validate_reason(
    reason: object, snapshot: SmartReviewSnapshot, decision: str
) -> str:
    """驗證候選理由是具體且可供人工檢視的文字。"""
    value = _validate_text(reason, "reason", max_length=_MAX_REASON_LENGTH).strip()
    if not value:
        raise ValueError("reason 不可為空")
    if _URL_PATTERN.search(value):
        raise ValueError("reason 不可包含外部 URL")
    if decision == "suggest_revision":
        anchors = (snapshot.en_us.strip(), snapshot.current_zh_tw.strip())
        meaningful_anchors: list[str] = []
        for anchor in anchors:
            if not anchor:
                continue
            meaningful_anchors.append(anchor)
            meaningful_anchors.extend(
                token
                for token in re.findall(r"[A-Za-z0-9_]{4,}", anchor)
                if token != anchor
            )
            for run in re.findall(r"[\u4e00-\u9fff]{2,}", anchor):
                meaningful_anchors.extend(
                    run[index : index + min(4, len(run))]
                    for index in range(max(1, len(run) - 3))
                )
        if not any(anchor and anchor in value for anchor in meaningful_anchors):
            raise ValueError(
                "suggest_revision 的 reason 必須指出可核對的原文或目前譯文差異"
            )
    return value


def _validate_item(
    raw: Any,
    snapshot: SmartReviewSnapshot,
) -> dict[str, Any]:
    """驗證單一智慧校對候選的識別碼、值與理由。"""
    if not isinstance(raw, dict):
        raise TypeError("item 必須是 object")
    required = {
        "id",
        "decision",
        "suggested_zh_tw",
        "issue_types",
        "evidence_ids",
        "confidence",
        "reason",
    }
    if set(raw) != required:
        raise ValueError("item 欄位不符合 strict schema")
    if raw["id"] != str(snapshot.ordinal):
        raise ValueError("item id 與批次快照不符")
    decision = raw["decision"]
    if decision not in SMART_REVIEW_DECISIONS:
        raise ValueError("decision 不在允許值內")
    suggestion = _validate_text(
        raw["suggested_zh_tw"], "suggested_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
    )
    issue_types = raw["issue_types"]
    evidence_ids = raw["evidence_ids"]
    if not isinstance(issue_types, list) or any(
        value not in SMART_REVIEW_ISSUE_TYPES for value in issue_types
    ):
        raise ValueError("issue_types 不符合允許值")
    if not isinstance(evidence_ids, list) or any(
        not isinstance(value, str) for value in evidence_ids
    ):
        raise ValueError("evidence_ids 必須是字串陣列")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_ids 不可重複")
    context_ids = {str(item.get("id")) for item in snapshot.context}
    if not set(evidence_ids) <= context_ids:
        raise ValueError("evidence_ids 引用了不存在的 local context")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TypeError("confidence 必須是數字")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence 必須介於 0 與 1")
    reason = _validate_reason(raw["reason"], snapshot, decision)

    if decision == "suggest_revision":
        if not suggestion.strip() or suggestion == snapshot.current_zh_tw:
            raise ValueError("suggest_revision 必須提供不同且非空的譯文")
        if translation_issues(
            snapshot.en_us, suggestion, kind=snapshot.kind, repair=False
        ):
            raise ValueError("suggested_zh_tw 未通過格式與結構驗證")
    elif suggestion:
        raise ValueError("非 suggest_revision 不可提供 suggested_zh_tw")

    return {
        "id": str(snapshot.ordinal),
        "decision": decision,
        "suggested_zh_tw": suggestion,
        "issue_types": list(dict.fromkeys(issue_types)),
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "confidence": float(confidence),
        "reason": reason,
    }


def parse_smart_review_response(
    raw: str,
    snapshots: Sequence[SmartReviewSnapshot],
) -> list[dict[str, Any]]:
    """依目前輸入批次嚴格解析並驗證模型回應。"""
    parsed = _strict_json_object(raw)
    items = parsed["items"]
    expected = {str(item.ordinal): item for item in snapshots}
    actual_ids = [item.get("id") if isinstance(item, dict) else None for item in items]
    if len(items) != len(expected) or any(
        not isinstance(item_id, str) or item_id not in expected
        for item_id in actual_ids
    ):
        raise ValueError("回應 items 的 id 集合或數量與批次不一致")
    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("回應 items 不可有重複 id")

    results: list[dict[str, Any]] = []
    for raw_item in items:
        item_id = raw_item["id"]
        try:
            result = _validate_item(raw_item, expected[item_id])
        except (TypeError, ValueError) as exc:
            result = {"id": item_id, "validation_error": str(exc)}
        results.append(result)
    return results


def _candidate_status(result: dict[str, Any]) -> str:
    """從候選資料推導目前可供 UI 顯示的狀態。"""
    if result.get("validation_error"):
        return SmartReviewStatus.INVALID
    return DECISION_STATUS[result["decision"]]
