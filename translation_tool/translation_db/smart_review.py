"""模組翻譯資料庫的本地 AI 智慧校對候選流程。

本模組刻意分離三個階段：

* 從 SQLite 讀取快照與本地上下文；
* 模型回傳經嚴格機器驗證的校對候選；
* 使用者透過 compare-and-set 交易明確套用候選。

任何模型回應都不能直接寫入翻譯。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import requests

from translation_tool.core.codex_oauth import ChatGPTOAuthError, chatgpt_account_status
from translation_tool.core.lm_batch_actions import BatchAction
from translation_tool.core.lm_batch_budget import BudgetConfig, estimate_text_tokens
from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_config_rules import ApiKeyCycle, get_api_key_count
from translation_tool.core.lm_config_schema import (
    chatgpt_model_input_token_budget,
    chatgpt_model_reasoning_effort,
)
from translation_tool.core.lm_key_health import get_model_quota_registry
from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.core.official_locale import (
    load_official_locale_index,
    normalize_terminology_text,
    terminology_term_allowed,
    terminology_terms_for_item,
)
from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewRunResult,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.quality import quality_state, translation_issues
from translation_tool.translation_db.sql_helpers import like_pattern
from translation_tool.translation_db.schema import SRC_AI, SRC_MANUAL
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    interruptible_sleep,
    is_cancelled,
)
from translation_tool.utils.text_matching import contains_term, term_canonical_key

SMART_REVIEW_DECISIONS = ("no_change", "suggest_revision", "needs_context")
SMART_REVIEW_ISSUE_TYPES = (
    "meaning",
    "terminology",
    "grammar",
    "style",
    "format",
    "context",
)
SMART_REVIEW_SYSTEM_PROMPT = """你是 Minecraft 繁體中文翻譯的本地校對助手。
你只能根據每筆資料提供的英文原文、目前譯文與 local context 判斷，不得查詢網路或補造外部資料。
你的輸出只是人工審查候選，不是自動翻譯，也不會直接寫入資料庫。

規則：
1. 每個 id 必須原樣保留，不能新增、刪除、重排或改寫。
2. decision 只能是 no_change、suggest_revision、needs_context。
3. 只有確定有必要修改時才使用 suggest_revision；不確定時使用 needs_context。
4. suggest_revision 必須提供完整的繁體中文 suggested_zh_tw；其他兩種 decision 的 suggested_zh_tw 必須為空字串。
5. 每筆都必須提供具體 reason：suggest_revision 要指出原文／目前譯文哪裡造成語意或專有名詞錯誤，以及建議如何修正；不能只寫「更自然」或「建議優化」。
6. needs_context 要說明缺少什麼本地資料；沒有可靠佐證時不得寫成「已確認」或宣稱查過網路。
7. 保留 Minecraft § 格式碼、佔位符、Patchouli／指南結構與原文語意。
8. evidence_ids 只能引用該筆 context 中已有的 id；沒有引用時使用空陣列。衝突 evidence 不可當成唯一正解。
9. 只回傳 JSON，不要 Markdown code fence 或額外說明。
"""

_MAX_REASON_LENGTH = 2_000
_MAX_SUGGESTION_LENGTH = 32_000
_URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def smart_review_response_schema(ids: Sequence[str]) -> dict[str, Any]:
    """建立限制在目前批次識別碼內的 provider JSON 結構。"""
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": list(ids)},
                        "decision": {
                            "type": "string",
                            "enum": list(SMART_REVIEW_DECISIONS),
                        },
                        "suggested_zh_tw": {"type": "string"},
                        "issue_types": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(SMART_REVIEW_ISSUE_TYPES),
                            },
                        },
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "number"},
                        # Keep length enforcement local in ``_validate_reason``.
                        # Gemini's structured-output schema supports only a
                        # subset of JSON Schema and rejects ``maxLength``.
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "decision",
                        "suggested_zh_tw",
                        "issue_types",
                        "evidence_ids",
                        "confidence",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    return schema


def build_smart_review_payload(
    snapshots: Sequence[SmartReviewSnapshot],
) -> dict[str, list[dict[str, Any]]]:
    """建立送給模型的不可預測識別碼智慧校對負載。"""
    return {
        "items": [
            {
                "id": str(item.ordinal),
                "kind": item.kind,
                "mc_version": item.mc_version,
                "mod_id": item.mod_id,
                "key": item.key,
                "source_text": item.en_us,
                "current_translation": item.current_zh_tw,
                "context": list(item.context),
            }
            for item in snapshots
        ]
    }


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
        return "invalid"
    return {
        "no_change": "no_change",
        "suggest_revision": "ready",
        "needs_context": "needs_context",
    }[result["decision"]]


def _response_reason(raw: object) -> str | None:
    """安全讀取目前或舊版候選格式中的理由。"""
    if not raw:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    reason = value.get("reason") if isinstance(value, dict) else None
    return reason if isinstance(reason, str) else None


def _effective_review_sql(alias: str = "f") -> str:
    """回傳智慧校對查詢共用的生效審核狀態條件。"""
    return (
        f"CASE WHEN {alias}.review_status IN ('unreviewed', 'reviewed', 'legacy_unknown') "
        f"THEN {alias}.review_status WHEN {alias}.source={SRC_MANUAL} "
        f"THEN 'legacy_unknown' WHEN {alias}.source IS NOT NULL THEN 'unreviewed' END"
    )


def _smart_review_current_snapshot_sql() -> str:
    """回傳草稿保存與候選套用共用的 CAS 快照查詢。"""
    return (
        "SELECT e.kind, e.mc_version, e.mod_id, e.key, e.en_us, f.zh_tw, f.source, "
        + _effective_review_sql()
        + ", t.revision FROM entry e JOIN effective f ON f.entry_id=e.id "
        "LEFT JOIN translation t ON t.entry_id=f.entry_id AND t.source=f.source "
        "WHERE f.entry_id=?"
    )


@dataclass(frozen=True)
class _SmartReviewSelection:
    """保存候選資料列與數量查詢共用的 SQL 片段。"""

    effective_review: str
    where_sql: str
    params: tuple[Any, ...]
    reference_cte: str
    reference_join: str
    order_sql: str


def _build_smart_review_selection(
    *,
    version: str | None,
    mod_id: str | None,
    kind: str | None,
    effective_source: int | None,
    review_status: str,
    query: str | None,
    require_reviewed_reference: bool,
    prioritize_named_keys: bool = False,
) -> _SmartReviewSelection:
    """建立資料列與數量查詢共用的權威候選範圍。"""
    effective_review = _effective_review_sql()
    if review_status not in {"unreviewed", "reviewed", "legacy_unknown"}:
        raise ValueError("smart review 不支援的審核狀態")

    where = [
        f"({effective_review}) = ?",
        "f.zh_tw <> ''",
        "e.en_us <> ''",
    ]
    params: list[Any] = [review_status]
    if version:
        where.append("e.mc_version = ?")
        params.append(version)
    if mod_id:
        where.append("e.mod_id = ?")
        params.append(mod_id)
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    if effective_source is not None:
        where.append("f.source = ?")
        params.append(int(effective_source))
    if query and query.strip():
        needle = like_pattern(query.strip())
        where.append(
            "(e.key LIKE ? ESCAPE '\\' OR e.en_us LIKE ? ESCAPE '\\' "
            "OR f.zh_tw LIKE ? ESCAPE '\\')"
        )
        params.extend((needle, needle, needle))

    reference_cte = ""
    reference_join = ""
    if require_reviewed_reference:
        reference_cte = (
            "WITH reviewed_reference AS ("
            "SELECT r.kind, r.mod_id, r.key, r.en_us "
            "FROM entry r JOIN translation rf ON rf.entry_id=r.id "
            "WHERE rf.review_status='reviewed' AND trim(rf.zh_tw)<>'' "
            "AND r.en_us<>'' "
            "GROUP BY r.kind, r.mod_id, r.key, r.en_us "
            "HAVING COUNT(DISTINCT trim(rf.zh_tw))=1"
            ") "
        )
        reference_join = (
            "JOIN reviewed_reference rr "
            "ON rr.kind=e.kind AND rr.mod_id=e.mod_id AND rr.key=e.key "
            "AND rr.en_us=e.en_us "
        )

    order_prefix = ""
    if prioritize_named_keys:
        order_prefix = (
            "CASE WHEN e.kind='lang' AND ("
            "e.key LIKE 'item.%' OR e.key LIKE 'block.%' OR "
            "e.key LIKE 'entity.%' OR e.key LIKE 'biome.%' OR "
            "e.key LIKE 'effect.%' OR e.key LIKE 'fluid.%' OR "
            "e.key LIKE 'enchantment.%' OR e.key LIKE 'dimension.%' OR "
            "e.key LIKE 'painting.%' OR e.key LIKE 'trim_pattern.%' OR "
            "e.key LIKE 'instrument.%' OR e.key LIKE 'banner.%'"
            ") THEN 0 ELSE 1 END, "
        )

    return _SmartReviewSelection(
        effective_review=effective_review,
        where_sql=" AND ".join(where),
        params=tuple(params),
        reference_cte=reference_cte,
        reference_join=reference_join,
        order_sql=f"{order_prefix}e.mc_version, e.mod_id, e.kind, e.key, e.id",
    )


class SmartReviewMixin:
    """提供給 TranslationDB 使用的智慧校對 SQLite 操作。"""

    def _smart_review_has_column(self, table: str, column: str) -> bool:
        """確認智慧校對資料表具有指定欄位。"""
        return any(row[1] == column for row in self._q(f"PRAGMA table_info({table})"))

    def smart_review_candidates(
        self,
        *,
        version: str | None = None,
        mod_id: str | None = None,
        kind: str | None = None,
        effective_source: int | None = None,
        review_status: str = "unreviewed",
        query: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        require_reviewed_reference: bool = False,
        prioritize_named_keys: bool = False,
    ) -> list[SmartReviewSnapshot]:
        """依穩定且有界的條件選出目前智慧校對候選。"""
        bounded_limit = max(0, min(int(limit), 10_000))
        if bounded_limit == 0:
            return []
        self._smart_review_context_cache = {}
        selection = _build_smart_review_selection(
            version=version,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            review_status=review_status,
            query=query,
            require_reviewed_reference=require_reviewed_reference,
            prioritize_named_keys=prioritize_named_keys,
        )
        entry_source = "entry e"
        if prioritize_named_keys and self._one(
            "SELECT 1 FROM sqlite_schema WHERE type='index' AND name=?",
            ("idx_smart_review_named_order",),
        ):
            entry_source = "entry e INDEXED BY idx_smart_review_named_order"
        rows = self._q(
            selection.reference_cte
            + "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            f"f.zh_tw, f.source, ({selection.effective_review}), t.revision, "
            "tm.zh_tw, tm.checker, tm.review_status, tm.revision "
            "FROM "
            + entry_source
            + " JOIN effective f ON f.entry_id=e.id "
            + selection.reference_join
            + "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
            f"LEFT JOIN translation tm ON tm.entry_id=e.id AND tm.source={SRC_MANUAL} "
            f"WHERE {selection.where_sql} "
            f"ORDER BY {selection.order_sql} "
            "LIMIT ? OFFSET ?",
            (*selection.params, bounded_limit, max(0, int(offset))),
        )
        snapshots: list[SmartReviewSnapshot] = []
        absolute_offset = max(0, int(offset))
        for row_index, row in enumerate(rows, 1):
            ordinal = absolute_offset + row_index
            (
                entry_id,
                kind,
                mc_version,
                mod_id,
                key,
                en_us,
                current,
                source,
                status,
                rev,
                manual_zh_tw,
                manual_checker,
                manual_review_status,
                manual_revision,
            ) = row
            context = self._smart_review_context(
                entry_id,
                kind,
                mc_version,
                mod_id,
                key,
                en_us,
            )
            snapshots.append(
                SmartReviewSnapshot(
                    run_id="",
                    ordinal=ordinal,
                    entry_id=int(entry_id),
                    kind=str(kind),
                    mc_version=str(mc_version),
                    mod_id=str(mod_id),
                    key=str(key),
                    en_us=str(en_us),
                    current_zh_tw=str(current),
                    effective_source=None if source is None else int(source),
                    review_status=status,
                    effective_revision=None if rev is None else int(rev),
                    context=tuple(context),
                    manual_zh_tw=manual_zh_tw,
                    manual_checker=manual_checker,
                    manual_review_status=manual_review_status,
                    manual_revision=(
                        None if manual_revision is None else int(manual_revision)
                    ),
                )
            )
        return snapshots

    def count_smart_review_candidates(
        self,
        *,
        version: str | None = None,
        mod_id: str | None = None,
        kind: str | None = None,
        effective_source: int | None = None,
        review_status: str = "unreviewed",
        query: str | None = None,
        require_reviewed_reference: bool = False,
    ) -> int:
        """計算與候選列表完全相同範圍的智慧校對數量。"""
        selection = _build_smart_review_selection(
            version=version,
            mod_id=mod_id,
            kind=kind,
            effective_source=effective_source,
            review_status=review_status,
            query=query,
            require_reviewed_reference=require_reviewed_reference,
        )
        row = self._one(
            selection.reference_cte
            + "SELECT COUNT(*) FROM entry e JOIN effective f ON f.entry_id=e.id "
            + selection.reference_join
            + f"WHERE {selection.where_sql}",
            selection.params,
        )
        return int(row[0]) if row else 0

    def _smart_review_context(
        self,
        entry_id: int,
        kind: str,
        mc_version: str,
        mod_id: str,
        key: str,
        en_us: str,
    ) -> list[dict[str, Any]]:
        """建立可重播且有界的本地術語與翻譯證據快照。"""
        cache = getattr(self, "_smart_review_context_cache", {})
        key_rows_cache = cache.setdefault("key_rows", {})
        quality_cache = cache.setdefault("quality", {})
        cache_key = (kind, mod_id, key)

        def quality_details(row: tuple) -> tuple[str, tuple[str, ...], str]:
            """取得 reviewed row 的格式品質狀態與可顯示診斷。"""
            quality_key = (int(row[0]), row[9], row[6], row[1])
            if quality_key not in quality_cache:
                quality_cache[quality_key] = quality_state(
                    str(row[5]), str(row[6]), kind=str(row[1])
                )
            return quality_cache[quality_key]

        if cache_key not in key_rows_cache:
            base_sql = (
                "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
                "f.zh_tw, f.source, f.review_status, f.revision "
                "FROM entry e JOIN translation f ON f.entry_id=e.id "
                "WHERE e.kind=? AND e.mod_id=? AND e.key=? "
                "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
            )
            # 先分層抓取，再在本地把品質合格的資料排到 LIMIT 前面；
            # 不讓 24 筆舊版本或格式不合格資料吃掉目前版本的參考名額。
            tier_queries = (
                (
                    "AND e.mc_version=? AND e.en_us=? ORDER BY e.id LIMIT 96",
                    (mc_version, en_us),
                ),
                (
                    "AND e.mc_version=? AND e.en_us<>? ORDER BY e.id LIMIT 96",
                    (mc_version, en_us),
                ),
                (
                    "AND e.mc_version<>? AND e.en_us=? ORDER BY e.id LIMIT 96",
                    (mc_version, en_us),
                ),
                (
                    "AND e.mc_version<>? AND e.en_us<>? ORDER BY e.mc_version, e.id LIMIT 96",
                    (mc_version, en_us),
                ),
            )
            candidates: list[tuple] = []
            seen_ids: set[int] = set()
            for suffix, tier_params in tier_queries:
                for row in self._q(
                    base_sql + suffix,
                    (kind, mod_id, key, *tier_params),
                ):
                    row_id = int(row[0])
                    if row_id not in seen_ids:
                        seen_ids.add(row_id)
                        candidates.append(row)
            consistent = [
                row for row in candidates if quality_details(row)[0] == "consistent"
            ]
            lower_quality = [
                row for row in candidates if quality_details(row)[0] != "consistent"
            ]
            key_rows_cache[cache_key] = (consistent + lower_quality)[:24]
        rows = [row for row in key_rows_cache[cache_key] if int(row[0]) != entry_id]
        context: list[dict[str, Any]] = []

        def db_context_item(
            row: tuple,
            *,
            reference_type: str,
            match_type: str,
            confidence: str,
            limitations: str,
            en_us_value: str | None = None,
        ) -> dict[str, Any]:
            """建立含來源、修訂版與品質限制的資料庫證據項目。"""
            status, issues, whitespace = quality_details(row)
            detail_parts = [limitations]
            if status != "consistent":
                detail_parts.append(f"reviewed quality={status}")
                if issues:
                    detail_parts.append("格式問題：" + "、".join(issues))
                if whitespace:
                    detail_parts.append(whitespace)
            return {
                "id": f"db:{row[0]}:source:{row[7]}:revision:{row[9]}",
                "legacy_id": f"db:{row[0]}",
                "entry_id": int(row[0]),
                "reference_type": reference_type,
                "mod_id": str(row[3]),
                "kind": str(row[1]),
                "key": str(row[4]),
                "mc_version": str(row[2]),
                "en_us": str(row[5]) if en_us_value is None else en_us_value,
                "zh_tw": str(row[6]),
                "review_status": str(row[8] or "reviewed"),
                "source": row[7],
                "source_label": self.source_catalog.label(row[7]),
                "match_type": match_type,
                "confidence": confidence if status == "consistent" else "low",
                "quality_status": status,
                "limitations": "; ".join(detail_parts),
            }

        same_version_rows = [row for row in rows if str(row[2]) == mc_version]
        same_version_translations = {
            str(row[6]).strip()
            for row in same_version_rows
            if quality_details(row)[0] == "consistent" and str(row[6]).strip()
        }
        version_conflict = len(same_version_translations) > 1
        for row in rows:
            same_text = str(row[5]) == en_us
            same_version = str(row[2]) == mc_version
            limitations = "same module/key and reviewed local source"
            if not same_version:
                limitations = "different Minecraft version; context only"
            elif not same_text:
                limitations = "same key but different English source; context only"
            elif version_conflict:
                limitations = (
                    "conflicting reviewed translations; human decision required"
                )
            context.append(
                db_context_item(
                    row,
                    reference_type="same_key_reviewed",
                    match_type=(
                        "exact_key_same_text" if same_text else "different_source_text"
                    ),
                    confidence=(
                        "high"
                        if same_version and same_text and not version_conflict
                        else "low"
                    ),
                    limitations=limitations,
                )
            )

        def cached_rows(
            cache_name: str, sql: str, params: tuple[Any, ...]
        ) -> list[tuple]:
            """讀取或建立指定查詢的快取資料列。"""
            bucket = cache.setdefault(cache_name, {})
            cache_key = params
            if cache_key not in bucket:
                bucket[cache_key] = self._q(sql, params)
            return bucket[cache_key]

        normalized_source = normalize_terminology_text(en_us).strip()
        source_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’:+._/-]*", normalized_source)
        lookup_phrases: list[str] = []
        if normalized_source:
            lookup_phrases.append(normalized_source)
        # 有界 n-gram 精確查詢補足 bulk LIMIT；因此 id > 5000 的
        # reviewed lang 專有名詞仍能被找回，不需要掃描整張 glossary。
        for width in range(min(6, len(source_words)), 0, -1):
            for start in range(len(source_words) - width + 1):
                phrase = " ".join(source_words[start : start + width]).strip()
                if len(phrase) >= 2 and phrase not in lookup_phrases:
                    lookup_phrases.append(phrase)
                if len(lookup_phrases) >= 96:
                    break
            if len(lookup_phrases) >= 96:
                break
        glossary_target_rows: list[tuple] = []
        if lookup_phrases:
            placeholders = ",".join("?" for _ in lookup_phrases)
            # 分成目前版本與其他版本兩段查詢，讓 glossary_order 索引直接
            # 提供 LIMIT 前的順序；不要用 CASE 排序把整批候選放進暫存 B-tree。
            glossary_target_current = cached_rows(
                "glossary_target_current_rows",
                "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
                "f.zh_tw, f.source, f.review_status, f.revision "
                "FROM entry e JOIN translation f ON f.entry_id=e.id "
                "WHERE e.kind='lang' AND e.mod_id=? AND e.mc_version=? "
                "AND e.en_us IN (" + placeholders + ") "
                "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
                "ORDER BY e.id LIMIT 96",
                (mod_id, mc_version, *lookup_phrases),
            )
            glossary_target_other = cached_rows(
                "glossary_target_other_rows",
                "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
                "f.zh_tw, f.source, f.review_status, f.revision "
                "FROM entry e JOIN translation f ON f.entry_id=e.id "
                "WHERE e.kind='lang' AND e.mod_id=? AND e.mc_version<>? "
                "AND e.en_us IN (" + placeholders + ") "
                "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
                "ORDER BY e.mc_version, e.id LIMIT 96",
                (mod_id, mc_version, *lookup_phrases),
            )
            glossary_target_rows = glossary_target_current + glossary_target_other
        glossary_current_rows = cached_rows(
            "glossary_current_rows",
            "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            "f.zh_tw, f.source, f.review_status, f.revision "
            "FROM entry e JOIN translation f ON f.entry_id=e.id "
            "WHERE e.kind='lang' AND e.mod_id=? AND e.mc_version=? "
            "AND f.review_status='reviewed' AND e.en_us<>'' "
            "AND trim(f.zh_tw)<>'' ORDER BY e.id LIMIT 5000",
            (mod_id, mc_version),
        )
        glossary_remaining = 5000 - len(glossary_current_rows)
        glossary_other_rows = (
            cached_rows(
                "glossary_other_rows",
                "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
                "f.zh_tw, f.source, f.review_status, f.revision "
                "FROM entry e JOIN translation f ON f.entry_id=e.id "
                "WHERE e.kind='lang' AND e.mod_id=? AND e.mc_version<>? "
                "AND f.review_status='reviewed' AND e.en_us<>'' "
                "AND trim(f.zh_tw)<>'' ORDER BY e.mc_version, e.id LIMIT ?",
                (mod_id, mc_version, glossary_remaining),
            )
            if glossary_remaining
            else []
        )
        glossary_rows: list[tuple] = []
        seen_glossary_ids: set[int] = set()
        for row in glossary_target_rows + glossary_current_rows + glossary_other_rows:
            row_id = int(row[0])
            if row_id not in seen_glossary_ids:
                seen_glossary_ids.add(row_id)
                glossary_rows.append(row)
        official = load_official_locale_index()
        official_pairs_cache = cache.setdefault("official_pairs", {})
        official_cache_key = str(getattr(official, "revision", "default"))
        if official_cache_key not in official_pairs_cache:
            pairs: dict[tuple[str, str], Any] = {}
            for record in official.records_by_key.values():
                pair = (
                    normalize_terminology_text(record.en_us).strip(),
                    record.zh_tw.strip(),
                )
                if pair[1]:
                    pairs.setdefault(pair, record)
            official_pairs_cache[official_cache_key] = pairs
        official_pairs = official_pairs_cache[official_cache_key]
        reviewed_entries = [
            {
                "key": str(row[4]),
                "en_us": str(row[5]),
                "zh_tw": str(row[6]),
            }
            for row in glossary_rows
            if quality_details(row)[0] == "consistent"
        ]
        terminology_plan = terminology_terms_for_item(
            {
                "path": key,
                "source_text": en_us,
                "_reviewed_glossary": reviewed_entries,
            },
            index=official,
        )
        selected_pairs = {
            (
                term_canonical_key(normalize_terminology_text(source)),
                target.strip(),
            )
            for source, target in terminology_plan
        }
        selected_sources = [
            (
                normalize_terminology_text(source),
                term_canonical_key(normalize_terminology_text(source)),
            )
            for source, _target in terminology_plan
        ]
        normalized_en_us = normalize_terminology_text(en_us)
        matched_glossary = []
        for row in glossary_rows:
            if quality_details(row)[0] != "consistent":
                continue
            normalized_term = normalize_terminology_text(str(row[5])).strip()
            if not contains_term(normalized_en_us, normalized_term):
                continue
            if not terminology_term_allowed(
                en_us,
                normalized_term,
                key=str(row[4]),
                index=official,
            ):
                continue
            matched_glossary.append(row)
        by_term: dict[str, list[tuple]] = {}
        for row in matched_glossary:
            normalized_term = normalize_terminology_text(str(row[5])).strip()
            by_term.setdefault(term_canonical_key(normalized_term), []).append(row)
        for term in sorted(by_term, key=lambda value: (-len(value), value))[:12]:
            term_rows = by_term[term]
            same_version_term_rows = [
                row for row in term_rows if str(row[2]) == mc_version
            ]
            conflict_rows = same_version_term_rows or term_rows
            term_targets = {str(row[6]).strip() for row in conflict_rows}
            term_conflict = len(term_targets) != 1
            if (
                not term_conflict
                and (term, next(iter(term_targets))) not in selected_pairs
            ):
                continue
            if term_conflict and any(
                selected_key != term and contains_term(selected_source, term)
                for selected_source, selected_key in selected_sources
            ):
                continue
            for row in term_rows[:4]:
                normalized_term = normalize_terminology_text(str(row[5])).strip()
                context.append(
                    db_context_item(
                        row,
                        reference_type="reviewed_lang_glossary",
                        match_type="whole_term_same_mod",
                        confidence=(
                            "high"
                            if str(row[2]) == mc_version and not term_conflict
                            else "low"
                        ),
                        limitations=(
                            "conflicting reviewed glossary terms; not a unique answer"
                            if term_conflict
                            else "same-module reviewed lang term; applies to this text only"
                            if str(row[2]) == mc_version
                            else "different Minecraft version; context only"
                        ),
                        en_us_value=normalized_term,
                    )
                )

        related_rows = cached_rows(
            "same_text_rows",
            "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            "f.zh_tw, f.source, f.review_status, f.revision "
            "FROM entry e JOIN translation f ON f.entry_id=e.id "
            "WHERE e.en_us=? AND e.id<>? AND f.review_status='reviewed' "
            "AND trim(f.zh_tw)<>'' ORDER BY e.mod_id, e.kind, e.key, e.id LIMIT 12",
            (en_us, entry_id),
        )
        for row in related_rows:
            if str(row[1]) == kind and str(row[3]) == mod_id and str(row[4]) == key:
                continue
            context.append(
                db_context_item(
                    row,
                    reference_type="same_text_reviewed",
                    match_type="same_text",
                    confidence="low",
                    limitations="cross-module auxiliary reference; not this module's canonical name",
                )
            )

        wanted_pairs = {
            (normalize_terminology_text(source).strip(), target.strip())
            for source, target in terminology_plan
        }
        official_by_pair = {
            pair: official_pairs[pair]
            for pair in wanted_pairs
            if pair in official_pairs
        }
        for (source, target), record in sorted(
            official_by_pair.items(), key=lambda entry: (-len(entry[0][0]), entry[0])
        )[:8]:
            context.append(
                {
                    "id": f"official:{record.key}",
                    "reference_type": "official_locale",
                    "mod_id": "minecraft",
                    "kind": "lang",
                    "key": record.key,
                    "mc_version": "bundled",
                    "en_us": record.en_us,
                    "zh_tw": target,
                    "review_status": "reviewed",
                    "source": "bundled_official_locale",
                    "match_type": "whole_term",
                    "confidence": "high",
                    "limitations": "bundled local Minecraft glossary; not an external lookup",
                }
            )

        # 同一資料庫列可能同時由 same-key 與 glossary 路徑取得；保留第一個較具體的路徑。
        unique: dict[str, dict[str, Any]] = {}
        for item in context:
            unique.setdefault(str(item["id"]), item)
        return list(unique.values())[:48]

    def create_smart_review_run(
        self,
        snapshots: Sequence[SmartReviewSnapshot],
        *,
        provider: str,
        model: str,
        version: str | None,
        requested_limit: int,
        batch_size: int,
        scope: dict[str, Any] | None = None,
    ) -> str:
        """建立只保存候選、不直接修改翻譯的智慧校對執行記錄。"""
        run_id = uuid.uuid4().hex
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO smart_review_run "
                "(id, provider, model, scope_version, requested_limit, batch_size, scope_json, selected_count) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    provider,
                    model,
                    version,
                    int(requested_limit),
                    int(batch_size),
                    json.dumps(scope or {}, ensure_ascii=False),
                    len(snapshots),
                ),
            )
            conn.executemany(
                "INSERT INTO smart_review_candidate "
                "(run_id, ordinal, entry_id, kind, mc_version, mod_id, entry_key, en_us, "
                "current_zh_tw, effective_source, review_status, effective_revision, "
                "manual_zh_tw, manual_checker, manual_review_status, manual_revision, context_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    (
                        run_id,
                        item.ordinal,
                        item.entry_id,
                        item.kind,
                        item.mc_version,
                        item.mod_id,
                        item.key,
                        item.en_us,
                        item.current_zh_tw,
                        item.effective_source,
                        item.review_status,
                        item.effective_revision,
                        item.manual_zh_tw,
                        item.manual_checker,
                        item.manual_review_status,
                        item.manual_revision,
                        json.dumps(list(item.context), ensure_ascii=False),
                    )
                    for item in snapshots
                ),
            )
        return run_id

    def record_smart_review_results(
        self,
        run_id: str,
        results: Sequence[dict[str, Any]],
    ) -> None:
        """保存一批智慧校對候選結果並維持來源快照。"""
        with self._tx() as conn:
            for result in results:
                ordinal = int(result["id"])
                row = conn.execute(
                    "SELECT 1 FROM smart_review_candidate WHERE run_id=? AND ordinal=?",
                    (run_id, ordinal),
                ).fetchone()
                if row is None:
                    raise ValueError("smart review response id 不在候選快照")
                if result.get("validation_error"):
                    conn.execute(
                        "UPDATE smart_review_candidate SET status='invalid', "
                        "validation_error=?, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                        (str(result["validation_error"]), run_id, ordinal),
                    )
                    continue
                conn.execute(
                    "UPDATE smart_review_candidate SET decision=?, suggested_zh_tw=?, "
                    "issue_types_json=?, evidence_ids_json=?, confidence=?, response_json=?, "
                    "status=?, validation_error=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (
                        result["decision"],
                        result["suggested_zh_tw"],
                        json.dumps(result["issue_types"], ensure_ascii=False),
                        json.dumps(result["evidence_ids"], ensure_ascii=False),
                        float(result["confidence"]),
                        json.dumps(result, ensure_ascii=False),
                        _candidate_status(result),
                        run_id,
                        ordinal,
                    ),
                )

    def mark_smart_review_request_error(
        self,
        run_id: str,
        ordinals: Sequence[int],
        *,
        error_code: str = "request_failed",
    ) -> None:
        """把智慧校對請求錯誤記錄到執行狀態。"""
        with self._tx() as conn:
            conn.executemany(
                "UPDATE smart_review_candidate SET status='request_failed', validation_error=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                ((error_code, run_id, int(ordinal)) for ordinal in ordinals),
            )

    def mark_smart_review_pending_cancelled(self, run_id: str) -> int:
        """標記已選取但取消前尚未送出的智慧校對項目。"""
        with self._tx() as conn:
            updated = conn.execute(
                "UPDATE smart_review_candidate SET status='cancelled', "
                "validation_error='cancelled_before_request', updated_at=CURRENT_TIMESTAMP "
                "WHERE run_id=? AND status='pending'",
                (run_id,),
            ).rowcount
        return int(updated)

    def finish_smart_review_run(
        self,
        run_id: str,
        *,
        processed_count: int,
        valid_count: int,
        invalid_count: int,
        request_error_count: int,
        metrics: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        """完成智慧校對執行並保存最終統計。"""
        if status not in {None, "completed", "partial", "cancelled", "failed"}:
            raise ValueError("smart review 不支援的 run 狀態")
        with self._tx() as conn:
            conn.execute(
                "UPDATE smart_review_run SET finished_at=CURRENT_TIMESTAMP, status=?, "
                "processed_count=?, valid_count=?, invalid_count=?, request_error_count=?, metrics=? "
                "WHERE id=?",
                (
                    status or ("completed" if not request_error_count else "partial"),
                    int(processed_count),
                    int(valid_count),
                    int(invalid_count),
                    int(request_error_count),
                    json.dumps(metrics or {}, ensure_ascii=False),
                    run_id,
                ),
            )

    def get_smart_review_run(self, run_id: str) -> dict[str, Any] | None:
        """讀取單一智慧校對執行的摘要與狀態。"""
        has_scope = self._smart_review_has_column("smart_review_run", "scope_json")
        row = self._one(
            "SELECT id, provider, model, scope_version, requested_limit, batch_size, "
            + ("scope_json, " if has_scope else "")
            + "started_at, finished_at, status, selected_count, processed_count, valid_count, "
            "invalid_count, request_error_count, metrics FROM smart_review_run WHERE id=?",
            (run_id,),
        )
        if row is None:
            return None
        scope_index = 6 if has_scope else None
        started_index = 7 if has_scope else 6
        scope = json.loads(row[scope_index] or "{}") if scope_index is not None else {}
        metrics = json.loads(row[started_index + 8] or "{}")
        return {
            "id": row[0],
            "provider": row[1],
            "model": row[2],
            "scope_version": row[3],
            "requested_limit": row[4],
            "batch_size": row[5],
            "scope": scope,
            "started_at": row[started_index],
            "finished_at": row[started_index + 1],
            "status": row[started_index + 2],
            "selected_count": row[started_index + 3],
            "processed_count": row[started_index + 4],
            "valid_count": row[started_index + 5],
            "invalid_count": row[started_index + 6],
            "request_error_count": row[started_index + 7],
            "metrics": metrics,
        }

    def list_smart_review_candidates(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SmartReviewCandidate]:
        """列出指定智慧校對執行中的候選資料。"""
        has_draft = self._smart_review_has_column(
            "smart_review_candidate", "draft_zh_tw"
        )
        draft_column = "draft_zh_tw" if has_draft else "NULL AS draft_zh_tw"
        clauses = ["run_id=?"]
        params: list[Any] = [run_id]
        if status:
            clauses.append("status=?")
            params.append(status)
        rows = self._q(
            "SELECT run_id, ordinal, entry_id, kind, mc_version, mod_id, entry_key, en_us, "
            "current_zh_tw, effective_source, review_status, effective_revision, "
            "manual_zh_tw, manual_checker, manual_review_status, manual_revision, context_json, "
            "decision, suggested_zh_tw, issue_types_json, evidence_ids_json, confidence, "
            f"response_json, {draft_column}, validation_error, status, applied_batch, applied_at "
            f"FROM smart_review_candidate WHERE {' AND '.join(clauses)} ORDER BY ordinal"
            + (" LIMIT ? OFFSET ?" if limit is not None else ""),
            (*params, max(0, int(limit)), max(0, int(offset)))
            if limit is not None
            else params,
        )
        result: list[SmartReviewCandidate] = []
        for row in rows:
            result.append(
                SmartReviewCandidate(
                    run_id=row[0],
                    ordinal=int(row[1]),
                    entry_id=int(row[2]),
                    kind=row[3],
                    mc_version=row[4],
                    mod_id=row[5],
                    key=row[6],
                    en_us=row[7],
                    current_zh_tw=row[8],
                    effective_source=None if row[9] is None else int(row[9]),
                    review_status=row[10],
                    effective_revision=None if row[11] is None else int(row[11]),
                    manual_zh_tw=row[12],
                    manual_checker=row[13],
                    manual_review_status=row[14],
                    manual_revision=(None if row[15] is None else int(row[15])),
                    context=tuple(json.loads(row[16] or "[]")),
                    decision=row[17],
                    suggested_zh_tw=row[18],
                    issue_types=tuple(json.loads(row[19] or "[]")),
                    evidence_ids=tuple(json.loads(row[20] or "[]")),
                    confidence=None if row[21] is None else float(row[21]),
                    reason=_response_reason(row[22]),
                    draft_zh_tw=row[23],
                    validation_error=row[24],
                    status=row[25],
                    applied_batch=row[26],
                    applied_at=row[27],
                )
            )
        return result

    def count_smart_review_candidates_for_run(
        self, run_id: str, *, status: str | None = None
    ) -> int:
        """計算指定智慧校對執行中的候選資料數量。"""
        clauses = ["run_id=?"]
        params: list[Any] = [run_id]
        if status:
            clauses.append("status=?")
            params.append(status)
        row = self._one(
            f"SELECT COUNT(*) FROM smart_review_candidate WHERE {' AND '.join(clauses)}",
            params,
        )
        return int(row[0]) if row else 0

    def save_smart_review_draft(
        self, run_id: str, ordinal: int, draft_zh_tw: str
    ) -> str:
        """保存可人工編輯的智慧校對草稿，不碰生效翻譯與歷史。"""
        draft = _validate_text(
            draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
        )
        with self._tx() as conn:
            row = conn.execute(
                "SELECT entry_id, kind, mc_version, mod_id, entry_key, en_us, current_zh_tw, "
                "effective_source, review_status, effective_revision, manual_zh_tw, "
                "manual_checker, manual_review_status, manual_revision, status "
                "FROM smart_review_candidate "
                "WHERE run_id=? AND ordinal=?",
                (run_id, int(ordinal)),
            ).fetchone()
            if row is None:
                return "missing"
            if row[14] != "ready":
                return "not_ready"
            if not draft.strip():
                return "invalid_draft"
            current = conn.execute(
                _smart_review_current_snapshot_sql(),
                (row[0],),
            ).fetchone()
            if current is None or tuple(current) != tuple(row[1:10]):
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='snapshot_stale', updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "stale"
            current_manual = conn.execute(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (row[0], SRC_MANUAL),
            ).fetchone()
            manual_snapshot = tuple(row[10:14])
            current_manual_snapshot = (
                tuple(current_manual) if current_manual else (None, None, None, None)
            )
            if current_manual_snapshot != manual_snapshot:
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='manual_snapshot_stale', updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "stale"
            if current_manual is not None and current_manual[2] == "reviewed":
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='manual_reviewed_requires_manual_edit', "
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "manual_reviewed"
            if translation_issues(row[5], draft, kind=row[1], repair=False):
                return "invalid_format"
            updated = conn.execute(
                "UPDATE smart_review_candidate SET draft_zh_tw=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=? AND status='ready'",
                (draft, run_id, int(ordinal)),
            ).rowcount
            return "saved" if updated == 1 else "not_ready"

    def list_smart_review_runs(
        self, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """列出可重新開啟的智慧校對執行摘要。"""
        has_scope = self._smart_review_has_column("smart_review_run", "scope_json")
        scope_column = "scope_json" if has_scope else "NULL AS scope_json"
        rows = self._q(
            "SELECT id, provider, model, scope_version, "
            + scope_column
            + ", started_at, "
            "finished_at, status, selected_count, processed_count, valid_count, "
            "invalid_count, request_error_count, metrics "
            "FROM smart_review_run ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            (max(1, min(int(limit), 100)), max(0, int(offset))),
        )
        result = []
        for row in rows:
            result.append(
                {
                    "id": row[0],
                    "provider": row[1],
                    "model": row[2],
                    "scope_version": row[3],
                    "scope": json.loads(row[4] or "{}"),
                    "started_at": row[5],
                    "finished_at": row[6],
                    "status": row[7],
                    "selected_count": row[8],
                    "processed_count": row[9],
                    "valid_count": row[10],
                    "invalid_count": row[11],
                    "request_error_count": row[12],
                    "metrics": json.loads(row[13] or "{}"),
                }
            )
        return result

    def apply_smart_review_candidate(
        self,
        run_id: str,
        ordinal: int,
        *,
        actor: str = "智慧校對",
        draft_zh_tw: str | None = None,
    ) -> str:
        """僅在生效快照未變更時以 CAS 套用智慧校對候選。"""
        batch = uuid.uuid4().hex
        with self._tx() as conn:
            candidate = conn.execute(
                "SELECT entry_id, kind, mc_version, mod_id, entry_key, en_us, current_zh_tw, "
                "effective_source, review_status, effective_revision, manual_zh_tw, "
                "manual_checker, manual_review_status, manual_revision, suggested_zh_tw, "
                "status, draft_zh_tw "
                "FROM smart_review_candidate WHERE run_id=? AND ordinal=?",
                (run_id, int(ordinal)),
            ).fetchone()
            if candidate is None:
                return "missing"
            if candidate[15] != "ready" or not candidate[14]:
                return "not_ready"
            proposed = candidate[16] or candidate[14]
            if draft_zh_tw is not None:
                proposed = _validate_text(
                    draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
                )
                if not proposed.strip():
                    return "invalid_draft"
            current = conn.execute(
                _smart_review_current_snapshot_sql(),
                (candidate[0],),
            ).fetchone()
            if current is None or (
                current[0],
                current[1],
                current[2],
                current[3],
                current[4],
                current[5],
                current[6],
                current[7],
                current[8],
            ) != (
                candidate[1],
                candidate[2],
                candidate[3],
                candidate[4],
                candidate[5],
                candidate[6],
                candidate[7],
                candidate[8],
                candidate[9],
            ):
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='snapshot_stale', updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "stale"
            current_manual = conn.execute(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (candidate[0], SRC_MANUAL),
            ).fetchone()
            manual_snapshot = (
                candidate[10],
                candidate[11],
                candidate[12],
                candidate[13],
            )
            current_manual_snapshot = (
                tuple(current_manual) if current_manual else (None, None, None, None)
            )
            if current_manual_snapshot != manual_snapshot:
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='manual_snapshot_stale', updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "stale"
            if current_manual is not None and current_manual[2] == "reviewed":
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='manual_reviewed_requires_manual_edit', "
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "manual_reviewed"
            if translation_issues(
                candidate[5], proposed, kind=candidate[1], repair=False
            ):
                conn.execute(
                    "UPDATE smart_review_candidate SET status='invalid_format', "
                    "validation_error='suggested_zh_tw_format_invalid', updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "invalid_format"
            previous_manual = current_manual
            conn.execute(
                "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
                "VALUES (?,?,?,'','unreviewed') ON CONFLICT(entry_id, source) DO UPDATE SET "
                "zh_tw=excluded.zh_tw, checker='', review_status='unreviewed', "
                "updated_at=CURRENT_TIMESTAMP",
                (candidate[0], SRC_MANUAL, proposed),
            )
            if draft_zh_tw is not None:
                conn.execute(
                    "UPDATE smart_review_candidate SET draft_zh_tw=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                    (proposed, run_id, int(ordinal)),
                )
            new_revision = conn.execute(
                "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                (candidate[0], SRC_MANUAL),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
                "prev_manual, prev_checker, prev_review_status, new_checker, new_review_status, "
                "prev_revision, new_revision, source_id, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate[0],
                    batch,
                    actor,
                    "smart_review_apply",
                    candidate[6],
                    proposed,
                    previous_manual[0] if previous_manual else None,
                    previous_manual[1] if previous_manual else None,
                    previous_manual[2] if previous_manual else None,
                    "",
                    "unreviewed",
                    previous_manual[3] if previous_manual else None,
                    new_revision,
                    SRC_MANUAL,
                    f"run={run_id}; ordinal={ordinal}",
                ),
            )
            conn.execute(
                "UPDATE smart_review_candidate SET status='applied', applied_batch=?, "
                "applied_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                (batch, run_id, int(ordinal)),
            )
            self._refresh(conn, [candidate[0]])
        return "applied"

    def reject_smart_review_candidate(self, run_id: str, ordinal: int) -> str:
        """保留目前翻譯並將智慧校對候選標記為拒絕。"""
        with self._tx(bump_generation_on_noop=False) as conn:
            updated = conn.execute(
                "UPDATE smart_review_candidate SET status='rejected', "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=? AND status='ready'",
                (run_id, int(ordinal)),
            ).rowcount
        return "rejected" if updated else "not_ready"


def _next_smart_review_batch_size(size: int) -> int:
    """以有界減半方式取得下一個智慧校對批次大小。"""
    return max(1, (int(size) + 1) // 2)


def _smart_review_response_needs_shrink(raw: str, meta: dict[str, Any]) -> bool:
    """判斷空回應或 provider 截斷是否應該縮小下一次請求。"""
    if not isinstance(raw, str) or not raw.strip():
        return True
    finish_reason = str(
        meta.get("finish_reason") or meta.get("finishReason") or ""
    ).lower()
    return any(
        marker in finish_reason
        for marker in ("max_tokens", "length", "truncat", "incomplete")
    )


def _smart_review_output_token_cap(lm_config: dict[str, Any]) -> int:
    """讀取智慧校對輸出上限並限制在 provider 可接受的整數範圍。"""
    try:
        configured = int(lm_config.get("smart_review_max_output_tokens", 16_384))
    except (TypeError, ValueError):
        configured = 16_384
    return max(1, min(configured, 65_536))


@dataclass
class _SmartReviewRetryRuntime:
    """保存智慧校對重試所需的共用 provider 狀態。"""

    provider: str
    lm_cfg: dict[str, Any]
    batch_interval_sec: float
    chatgpt_retry_count: int = 0
    gemini_transient_retry_count: int = 0


def _smart_review_request_interval(lm_config: dict[str, Any]) -> float:
    """讀取與翻譯主流程相同的 provider/model 請求節流設定。"""
    rate_config = lm_config.get("rate_limit", {})
    if not isinstance(rate_config, dict):
        return 0.0
    try:
        return max(0.0, float(rate_config.get("sleep_seconds_between_batches", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _smart_review_input_budget(
    lm_config: dict[str, Any], provider: str, model_name: str
) -> int | None:
    """建立本次執行固定的輸入 token 預算快照。"""
    budget = BudgetConfig.from_config(lm_config)
    if not budget.enabled:
        return None
    limit = int(budget.max_input_token_budget)
    if provider == "chatgpt":
        model_limit = chatgpt_model_input_token_budget(lm_config, model_name)
        if model_limit is not None:
            limit = min(limit, int(model_limit))
    return max(1, limit)


def _smart_review_estimated_input_tokens(
    payload: dict[str, Any], response_schema: dict[str, Any]
) -> float:
    """使用翻譯主流程的本地 estimator 估算完整 provider 輸入。"""
    return estimate_text_tokens(
        SMART_REVIEW_SYSTEM_PROMPT
        + json.dumps(payload, ensure_ascii=False)
        + json.dumps(response_schema, ensure_ascii=False)
    )


def _smart_review_token_value(value: object) -> int | None:
    """將 provider usage 欄位轉成可累加的非負整數，未知時保留 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _smart_review_error_code(error: Exception, status: int | None = None) -> str:
    """建立不含 provider 原文、金鑰或個資的候選錯誤代碼。"""
    from translation_tool.core.lm_translator_main import _classify_batch_error

    if isinstance(error, ChatGPTAPIError):
        code = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(error.code or "unknown"))[:80]
        return f"request_failed:chatgpt:{error.status or 'unknown'}:{code}"
    error_kind = _classify_batch_error(error, status)
    if status is not None:
        return f"request_failed:http_{int(status)}:{error_kind}"
    return f"request_failed:{type(error).__name__}"


def _smart_review_record_usage(
    metrics: dict[str, Any],
    meta: dict[str, Any],
    token_totals: dict[str, int | None],
) -> None:
    """記錄一次 API 嘗試的 provider usage，包含失敗或截斷回應。"""
    metrics["request_attempts"] = int(metrics.get("request_attempts", 0)) + 1
    prompt = _smart_review_token_value(meta.get("prompt_tokens"))
    output = _smart_review_token_value(
        meta.get("output_tokens") or meta.get("candidates_tokens")
    )
    if prompt is None:
        metrics["usage_missing_attempts"] = (
            int(metrics.get("usage_missing_attempts", 0)) + 1
        )
    else:
        token_totals["prompt_tokens"] = (
            token_totals.get("prompt_tokens") or 0
        ) + prompt
    if output is None:
        metrics["usage_missing_output_attempts"] = (
            int(metrics.get("usage_missing_output_attempts", 0)) + 1
        )
    else:
        token_totals["output_tokens"] = (
            token_totals.get("output_tokens") or 0
        ) + output


def _smart_review_sleep(
    seconds: float, cancel_check: Callable[[], bool] | None
) -> None:
    """以既有可取消 sleep 等待 provider 的重試延遲。"""
    if cancel_check is not None and cancel_check():
        raise TaskCancelled()
    context = cancel_scope(cancel_check) if cancel_check is not None else nullcontext()
    with context:
        interruptible_sleep(seconds)
    if cancel_check is not None and cancel_check():
        raise TaskCancelled()


def _smart_review_transient_retry(
    runtime: _SmartReviewRetryRuntime,
    model_name: str,
    error: Exception,
    status: int | None,
    *,
    cancel_check: Callable[[], bool] | None,
) -> bool:
    """依既有 Gemini 503 policy 對 500/502/503/504 做有限次重試。"""
    from translation_tool.core.lm_translator_main import (
        GEMINI_503_MAX_RETRY_AFTER_SEC,
        GEMINI_503_RETRY_DELAYS_SEC,
        _classify_batch_error,
        _parse_gemini_retry_after_seconds,
    )

    error_kind = _classify_batch_error(error, status)
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        error_kind = "timeout"
    if error_kind not in {
        "server_error",
        "service_unavailable",
        "deadline_exceeded",
        "timeout",
    }:
        return False
    if runtime.gemini_transient_retry_count >= len(GEMINI_503_RETRY_DELAYS_SEC):
        return False
    response = getattr(error, "response", None)
    error_json: dict[str, Any] = {}
    try:
        value = response.json() if response is not None else {}
        error_json = value if isinstance(value, dict) else {}
    except (AttributeError, TypeError, ValueError):
        error_json = {}
    retry_after = _parse_gemini_retry_after_seconds(response, error_json)
    if retry_after is not None and retry_after > GEMINI_503_MAX_RETRY_AFTER_SEC:
        return False
    retry_index = runtime.gemini_transient_retry_count
    runtime.gemini_transient_retry_count += 1
    wait_sec = GEMINI_503_RETRY_DELAYS_SEC[retry_index]
    if retry_after is not None:
        wait_sec = max(wait_sec, retry_after)
    quota = get_model_quota_registry()
    quota.defer_request_slot(runtime.provider, model_name, wait_sec)
    _smart_review_sleep(wait_sec, cancel_check)
    return True


def run_smart_review(
    db,
    *,
    model_name: str,
    api_key: str,
    provider: str = "gemini",
    version: str | None = None,
    limit: int = 1000,
    batch_size: int = 40,
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
) -> SmartReviewRunResult:
    """執行有界的 AI 智慧校對，只保存候選，不直接寫入翻譯。"""
    if not model_name.strip():
        raise ValueError("smart review 必須指定模型")
    lm_config = lm_config if isinstance(lm_config, dict) else {}
    # service path 不預先選 key，仍由既有 ApiKeyCycle 負責 Gemini 輪替。
    key_cycle = ApiKeyCycle() if provider == "gemini" and not api_key.strip() else None
    if provider == "gemini" and not api_key.strip() and not get_api_key_count():
        raise ValueError("Gemini smart review 必須指定 API key")
    chatgpt_options: dict[str, Any] = {}
    if provider == "chatgpt":
        chatgpt_options["fast"] = bool(lm_config.get("fast", False))
        chatgpt_options["reasoning_effort"] = chatgpt_model_reasoning_effort(
            lm_config, model_name
        )
        profile_id = str(
            chatgpt_account_status().get("active_profile_id")
            or lm_config.get("chatgpt_model_profile_id")
            or ""
        ).strip()
        if profile_id:
            chatgpt_options["profile_id"] = profile_id
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
    batch_size = max(1, min(int(batch_size), 100))
    run_id = db.create_smart_review_run(
        snapshots,
        provider=provider,
        model=model_name,
        version=version,
        requested_limit=limit,
        batch_size=batch_size,
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
    )
    processed = valid = invalid = request_errors = 0
    no_change = suggestions = needs_context = 0
    output_token_cap = _smart_review_output_token_cap(lm_config)
    input_token_budget = _smart_review_input_budget(lm_config, provider, model_name)
    started = time.monotonic()
    cancelled = False
    failed = False
    token_totals: dict[str, int | None] = {
        "prompt_tokens": None,
        "output_tokens": None,
    }
    metrics: dict[str, Any] = {
        "duration_seconds": 0.0,
        "prompt_tokens": None,
        "output_tokens": None,
        "no_change_count": 0,
        "suggestion_count": 0,
        "needs_context_count": 0,
        "request_attempts": 0,
        "retry_count": 0,
        "transient_retry_count": 0,
        "key_rotation_count": 0,
        "shrink_count": 0,
        "input_budget_shrink_count": 0,
        "input_budget_exceeded_count": 0,
        "response_invalid_count": 0,
        "usage_missing_attempts": 0,
        "usage_missing_output_attempts": 0,
        "permanent_error_count": 0,
        "failure_classes": {},
        "input_token_budget": input_token_budget,
    }
    # 延遲取得主翻譯流程的共用 policy，避免 translation_db 初始化時形成循環 import。
    from translation_tool.core.lm_translator_main import (
        _handle_chatgpt_api_error,
        _parse_gemini_retry_after_seconds,
        _payload_error_allows_shrink,
        _wait_for_request_slot,
    )

    def mark_request_failure(
        ordinals: Sequence[int],
        error: Exception | None = None,
        status: int | None = None,
        *,
        error_code: str | None = None,
        permanent: bool = False,
    ) -> str:
        """保存安全錯誤代碼並更新錯誤分類統計。"""
        nonlocal request_errors
        code = error_code or _smart_review_error_code(
            error or RuntimeError("request_failed"), status
        )
        db.mark_smart_review_request_error(run_id, ordinals, error_code=code)
        request_errors += 1
        if permanent:
            metrics["permanent_error_count"] = int(metrics["permanent_error_count"]) + 1
        classes = metrics["failure_classes"]
        classes[code] = int(classes.get(code, 0)) + 1
        return code

    def report_progress() -> None:
        """回報目前已處理數量與統計。"""
        if progress is not None:
            progress(
                {
                    "run_id": run_id,
                    "processed": processed,
                    "selected": len(snapshots),
                    "valid": valid,
                    "invalid": invalid,
                    "request_errors": request_errors,
                    "status": "running",
                }
            )

    try:
        position = 0
        adaptive_batch_size = batch_size
        while position < len(snapshots):
            if is_cancelled() or (cancel_check is not None and cancel_check()):
                cancelled = True
                break
            current_size = min(adaptive_batch_size, len(snapshots) - position)
            abort_remaining = False
            while True:
                if is_cancelled() or (cancel_check is not None and cancel_check()):
                    cancelled = True
                    break
                batch = snapshots[position : position + current_size]
                payload = build_smart_review_payload(batch)
                response_schema = smart_review_response_schema(
                    [str(item.ordinal) for item in batch]
                )
                estimated_input = _smart_review_estimated_input_tokens(
                    payload, response_schema
                )
                metrics["last_estimated_input_tokens"] = round(estimated_input, 2)
                if (
                    input_token_budget is not None
                    and estimated_input > input_token_budget
                ):
                    if current_size > 1:
                        smaller = _next_smart_review_batch_size(current_size)
                        if smaller < current_size:
                            current_size = smaller
                            adaptive_batch_size = min(adaptive_batch_size, current_size)
                            metrics["input_budget_shrink_count"] = (
                                int(metrics["input_budget_shrink_count"]) + 1
                            )
                            metrics["shrink_count"] = int(metrics["shrink_count"]) + 1
                            continue
                    metrics["input_budget_exceeded_count"] = int(
                        metrics["input_budget_exceeded_count"]
                    ) + len(batch)
                    mark_request_failure(
                        [item.ordinal for item in batch],
                        error_code="request_failed:input_budget_exceeded",
                    )
                    processed += len(batch)
                    position += len(batch)
                    report_progress()
                    break

                retry_runtime = _SmartReviewRetryRuntime(
                    provider=provider,
                    lm_cfg=lm_config,
                    batch_interval_sec=_smart_review_request_interval(lm_config),
                )
                request_failed = False
                shrink_requested = False
                raw = ""
                while True:
                    request_api_key = ""
                    if key_cycle is not None:
                        request_api_key = key_cycle.claim()
                        if not request_api_key and not key_cycle.failed_indexes:
                            request_api_key = api_key.strip()
                    else:
                        request_api_key = api_key.strip()
                    if provider == "gemini" and not request_api_key:
                        mark_request_failure(
                            [item.ordinal for item in batch],
                            error_code="request_failed:ApiKeyCycleExhausted",
                            permanent=True,
                        )
                        request_failed = True
                        abort_remaining = True
                        break

                    attempt_meta: dict[str, Any] | None = None
                    try:
                        _wait_for_request_slot(
                            retry_runtime,
                            model_name,
                            cancel_check=cancel_check,
                        )
                        attempt_meta = {}
                        raw = call_gemini_requests(
                            model_name=model_name,
                            system_prompt=SMART_REVIEW_SYSTEM_PROMPT,
                            payload=payload,
                            api_key=request_api_key,
                            temperature=0.2,
                            max_output_tokens=output_token_cap,
                            meta_out=attempt_meta,
                            provider=provider,
                            lm_config=lm_config,
                            response_schema=response_schema,
                            **chatgpt_options,
                        )
                    except TaskCancelled:
                        if attempt_meta is not None:
                            _smart_review_record_usage(
                                metrics, attempt_meta, token_totals
                            )
                        cancelled = True
                        break
                    except ChatGPTOAuthError as exc:
                        if attempt_meta is not None:
                            _smart_review_record_usage(
                                metrics, attempt_meta, token_totals
                            )
                        mark_request_failure(
                            [item.ordinal for item in batch],
                            exc,
                            error_code="request_failed:chatgpt_oauth",
                            permanent=True,
                        )
                        request_failed = True
                        abort_remaining = True
                        break
                    except ChatGPTAPIError as exc:
                        _smart_review_record_usage(
                            metrics, attempt_meta or {}, token_totals
                        )
                        try:
                            with (
                                cancel_scope(cancel_check)
                                if cancel_check is not None
                                else nullcontext()
                            ):
                                action = _handle_chatgpt_api_error(retry_runtime, exc)
                        except TaskCancelled:
                            cancelled = True
                            break
                        except Exception:
                            mark_request_failure(
                                [item.ordinal for item in batch],
                                exc,
                                status=exc.status,
                                permanent=True,
                            )
                            request_failed = True
                            abort_remaining = exc.status in {400, 401, 403, 404, 422}
                            break
                        if action is BatchAction.SHRINK_BATCH:
                            if current_size > 1:
                                shrink_requested = True
                                break
                            mark_request_failure(
                                [item.ordinal for item in batch],
                                exc,
                                status=exc.status,
                            )
                            request_failed = True
                            break
                        if action is BatchAction.RETRY_SAME_MODEL:
                            metrics["retry_count"] = int(metrics["retry_count"]) + 1
                            metrics["transient_retry_count"] = (
                                int(metrics["transient_retry_count"]) + 1
                            )
                            continue
                        mark_request_failure(
                            [item.ordinal for item in batch],
                            exc,
                            status=exc.status,
                            permanent=True,
                        )
                        request_failed = True
                        abort_remaining = True
                        break
                    except requests.HTTPError as exc:
                        _smart_review_record_usage(
                            metrics, attempt_meta or {}, token_totals
                        )
                        status_code = (
                            exc.response.status_code
                            if exc.response is not None
                            else None
                        )
                        if status_code == 429:
                            error_json: dict[str, Any] = {}
                            try:
                                decoded = (
                                    exc.response.json()
                                    if exc.response is not None
                                    else {}
                                )
                                if isinstance(decoded, dict):
                                    error_json = decoded
                            except (AttributeError, TypeError, ValueError):
                                error_json = {}
                            retry_after = _parse_gemini_retry_after_seconds(
                                exc.response, error_json
                            )
                            if retry_after:
                                get_model_quota_registry().defer_request_slot(
                                    provider, model_name, retry_after
                                )
                        if (
                            key_cycle is not None
                            and status_code in {403, 429}
                            and key_cycle.mark_failed(
                                reason="forbidden" if status_code == 403 else None
                            )
                        ):
                            metrics["key_rotation_count"] = (
                                int(metrics["key_rotation_count"]) + 1
                            )
                            metrics["retry_count"] = int(metrics["retry_count"]) + 1
                            continue
                        if (
                            _payload_error_allows_shrink(exc, status_code)
                            and current_size > 1
                        ):
                            shrink_requested = True
                            break
                        if status_code in {500, 502, 503, 504}:
                            if status_code == 503 and key_cycle is not None:
                                overload_count = key_cycle.record_overload()
                                if overload_count >= 3 and key_cycle.mark_failed():
                                    metrics["key_rotation_count"] = (
                                        int(metrics["key_rotation_count"]) + 1
                                    )
                                    metrics["retry_count"] = (
                                        int(metrics["retry_count"]) + 1
                                    )
                                    continue
                            if _smart_review_transient_retry(
                                retry_runtime,
                                model_name,
                                exc,
                                status_code,
                                cancel_check=cancel_check,
                            ):
                                metrics["retry_count"] = int(metrics["retry_count"]) + 1
                                metrics["transient_retry_count"] = (
                                    int(metrics["transient_retry_count"]) + 1
                                )
                                continue
                            mark_request_failure(
                                [item.ordinal for item in batch],
                                exc,
                                status=status_code,
                            )
                            request_failed = True
                            abort_remaining = True
                            break
                        mark_request_failure(
                            [item.ordinal for item in batch],
                            exc,
                            status=status_code,
                            permanent=status_code in {400, 404, 422},
                        )
                        request_failed = True
                        abort_remaining = status_code in {400, 404, 422}
                        break
                    except (requests.Timeout, requests.ConnectionError) as exc:
                        _smart_review_record_usage(
                            metrics, attempt_meta or {}, token_totals
                        )
                        if _smart_review_transient_retry(
                            retry_runtime,
                            model_name,
                            exc,
                            None,
                            cancel_check=cancel_check,
                        ):
                            metrics["retry_count"] = int(metrics["retry_count"]) + 1
                            metrics["transient_retry_count"] = (
                                int(metrics["transient_retry_count"]) + 1
                            )
                            continue
                        mark_request_failure([item.ordinal for item in batch], exc)
                        request_failed = True
                        abort_remaining = True
                        break
                    except Exception as exc:  # noqa: BLE001 - credential-free class
                        _smart_review_record_usage(
                            metrics, attempt_meta or {}, token_totals
                        )
                        mark_request_failure([item.ordinal for item in batch], exc)
                        request_failed = True
                        break
                    else:
                        _smart_review_record_usage(
                            metrics, attempt_meta or {}, token_totals
                        )
                        if key_cycle is not None:
                            key_cycle.record_success()
                        retry_runtime.gemini_transient_retry_count = 0
                        shrink_requested = (
                            _smart_review_response_needs_shrink(raw, attempt_meta or {})
                            and current_size > 1
                        )
                        break

                if cancelled:
                    break
                if request_failed:
                    processed += len(batch)
                    position += len(batch)
                    if abort_remaining and position < len(snapshots):
                        remaining_ordinals = [
                            item.ordinal for item in snapshots[position:]
                        ]
                        db.mark_smart_review_request_error(
                            run_id,
                            remaining_ordinals,
                            error_code="request_failed:aborted_after_provider_error",
                        )
                        processed = len(snapshots)
                        position = len(snapshots)
                    report_progress()
                    break
                if shrink_requested:
                    smaller = _next_smart_review_batch_size(current_size)
                    if smaller < current_size:
                        current_size = smaller
                        adaptive_batch_size = min(adaptive_batch_size, current_size)
                        metrics["shrink_count"] = int(metrics["shrink_count"]) + 1
                        continue
                try:
                    results = parse_smart_review_response(raw, batch)
                except Exception as exc:  # noqa: BLE001 - response contract class
                    if current_size > 1:
                        smaller = _next_smart_review_batch_size(current_size)
                        if smaller < current_size:
                            current_size = smaller
                            adaptive_batch_size = min(adaptive_batch_size, current_size)
                            metrics["shrink_count"] = int(metrics["shrink_count"]) + 1
                            metrics["response_invalid_count"] = (
                                int(metrics["response_invalid_count"]) + 1
                            )
                            continue
                    metrics["response_invalid_count"] = int(
                        metrics["response_invalid_count"]
                    ) + len(batch)
                    results = [
                        {
                            "id": str(item.ordinal),
                            "validation_error": f"response_invalid:{type(exc).__name__}",
                        }
                        for item in batch
                    ]
                db.record_smart_review_results(run_id, results)
                for result in results:
                    if result.get("validation_error"):
                        invalid += 1
                        continue
                    valid += 1
                    if result["decision"] == "no_change":
                        no_change += 1
                    elif result["decision"] == "suggest_revision":
                        suggestions += 1
                    else:
                        needs_context += 1
                processed += len(batch)
                position += len(batch)
                adaptive_batch_size = min(adaptive_batch_size, current_size)
                report_progress()
                break
            if cancelled:
                break
    except TaskCancelled:
        cancelled = True
    except BaseException:
        failed = True
        db.mark_smart_review_pending_cancelled(run_id)
        metrics.update(
            {
                "duration_seconds": round(time.monotonic() - started, 3),
                "prompt_tokens": token_totals["prompt_tokens"],
                "output_tokens": token_totals["output_tokens"],
                "status": "failed",
                "token_counts": (
                    "not_reported"
                    if metrics["request_attempts"] == 0
                    else "provider_reported_partial"
                    if metrics["usage_missing_attempts"]
                    or metrics["usage_missing_output_attempts"]
                    else "provider_reported"
                ),
            }
        )
        db.finish_smart_review_run(
            run_id,
            processed_count=processed,
            valid_count=valid,
            invalid_count=invalid,
            request_error_count=request_errors,
            metrics=metrics,
            status="failed",
        )
        raise

    if cancelled:
        db.mark_smart_review_pending_cancelled(run_id)

    status = (
        "cancelled"
        if cancelled
        else "failed"
        if failed
        else "partial"
        if request_errors
        else "completed"
    )
    metrics.update(
        {
            "duration_seconds": round(time.monotonic() - started, 3),
            "prompt_tokens": token_totals["prompt_tokens"],
            "output_tokens": token_totals["output_tokens"],
            "no_change_count": no_change,
            "suggestion_count": suggestions,
            "needs_context_count": needs_context,
            "status": status,
            "token_counts": (
                "not_reported"
                if metrics["request_attempts"] == 0
                else "provider_reported_partial"
                if metrics["usage_missing_attempts"]
                or metrics["usage_missing_output_attempts"]
                else "provider_reported"
            ),
        }
    )
    db.finish_smart_review_run(
        run_id,
        processed_count=processed,
        valid_count=valid,
        invalid_count=invalid,
        request_error_count=request_errors,
        metrics=metrics,
        status=status,
    )
    return SmartReviewRunResult(
        run_id=run_id,
        provider=provider,
        model=model_name,
        selected_count=len(snapshots),
        processed_count=processed,
        valid_count=valid,
        invalid_count=invalid,
        request_error_count=request_errors,
        no_change_count=no_change,
        suggestion_count=suggestions,
        needs_context_count=needs_context,
        metrics=metrics,
        status=status,
    )


__all__ = [
    "SMART_REVIEW_DECISIONS",
    "SMART_REVIEW_ISSUE_TYPES",
    "SMART_REVIEW_SYSTEM_PROMPT",
    "SmartReviewMixin",
    "build_smart_review_payload",
    "parse_smart_review_response",
    "run_smart_review",
    "smart_review_response_schema",
]
