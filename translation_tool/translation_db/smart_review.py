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
from translation_tool.core.common_batch_executor import (
    CommonBatchExecutor,
    classify_provider_error,
)
from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_batch_actions import BatchAction
from translation_tool.core.lm_batch_budget import BudgetConfig, estimate_text_tokens
from translation_tool.core.lm_config_rules import ApiKeyCycle, get_api_key_count
from translation_tool.core.lm_config_schema import (
    chatgpt_model_input_token_budget,
    chatgpt_model_reasoning_effort,
)
from translation_tool.core.lm_key_health import get_model_quota_registry
from translation_tool.core.official_locale import (
    load_official_locale_index,
    normalize_terminology_text,
    terminology_term_allowed,
    terminology_terms_for_item,
)
from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewRunResult,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.quality import quality_state, translation_issues
from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_TW, SRC_MANUAL
from translation_tool.translation_db.smart_review_history import SmartReviewHistoryStore
from translation_tool.translation_db.sql_helpers import like_pattern
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
        """依穩定條件選出目前智慧校對候選；``limit=0`` 代表不限。"""
        bounded_limit = int(limit)
        if bounded_limit < 0:
            raise ValueError("smart review limit 不可小於 0")
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
        query_sql = (
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
        )
        query_params: tuple[Any, ...]
        if bounded_limit == 0:
            query_sql += "LIMIT -1 OFFSET ?"
            query_params = (*selection.params, max(0, int(offset)))
        else:
            query_sql += "LIMIT ? OFFSET ?"
            query_params = (*selection.params, bounded_limit, max(0, int(offset)))
        rows = self._q(query_sql, query_params)
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
        # 同一 key 可能跨版本或原文改版；cache identity 必須涵蓋實際 context
        # 範圍，避免把另一版本的 reviewed evidence 套到目前候選。
        cache_key = (kind, mod_id, key, mc_version, en_us)

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
                        else "medium"
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
        official_context: list[dict[str, Any]] = []
        for match in official.official_terminology_matches_for_item(
            {"path": key, "source_text": en_us, "text": en_us}
        ):
            if match.match_type in {"exact_key", "exact_text"}:
                limitations = "官方 Minecraft 完整翻譯；高信心"
            elif match.match_type == "official_phrase":
                limitations = "官方 Minecraft 完整片語；高信心"
            elif match.term_origin == "compound_component":
                limitations = "官方 Minecraft 術語；由官方複合詞拆解；高信心"
            else:
                limitations = "官方 Minecraft 術語；高信心"
            official_context.append(
                {
                    "id": match.stable_id,
                    "reference_type": "official_locale",
                    "mod_id": "minecraft",
                    "kind": "lang",
                    "key": match.key or "",
                    "mc_version": "bundled",
                    "en_us": match.source_text,
                    "zh_tw": match.target_text,
                    "source_text": match.source_text,
                    "target_text": match.target_text,
                    "review_status": "reviewed",
                    "source": "bundled_official_locale",
                    "source_label": "官方 Minecraft",
                    "match_type": match.match_type,
                    "term_origin": match.term_origin,
                    "confidence": "high",
                    "limitations": limitations,
                }
            )
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
                            else "medium"
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
                    confidence="medium",
                    limitations="cross-module auxiliary reference; not this module's canonical name",
                )
            )

        module_locale_context: list[dict[str, Any]] = []
        for zh_tw, source, review_status, revision in cached_rows(
            "module_locale_rows",
            "SELECT zh_tw, source, review_status, revision "
            "FROM translation WHERE entry_id=? AND source=? "
            "AND trim(zh_tw)<>'' "
            "AND (review_status IS NULL OR review_status<>'reviewed') "
            "ORDER BY revision DESC LIMIT 2",
            (entry_id, SRC_JAR_TW),
        ):
            value = str(zh_tw).strip()
            if not value or value == str(en_us).strip():
                continue
            module_locale_context.append(
                {
                    "id": f"module-locale:{entry_id}:source:{source}:revision:{revision or '-'}",
                    "reference_type": "module_locale",
                    "entry_id": entry_id,
                    "mod_id": mod_id,
                    "kind": kind,
                    "key": key,
                    "mc_version": mc_version,
                    "en_us": en_us,
                    "zh_tw": value,
                    "source_text": en_us,
                    "target_text": value,
                    "review_status": review_status or "unreviewed",
                    "source": source,
                    "source_label": self.source_catalog.label(source),
                    "match_type": "same_entry_unreviewed",
                    "confidence": "low",
                    "limitations": "模組自帶 zh_tw.json，尚未人工審核；僅供參考，不是官方標準",
                }
            )

        # 同一資料庫列可能同時由 same-key 與 glossary 路徑取得；保留第一個較具體的路徑。
        unique: dict[str, dict[str, Any]] = {}
        for item in (*official_context, *module_locale_context, *context):
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
        """建立只保存候選、不直接修改翻譯的 JSON 智慧校對批次。"""
        return SmartReviewHistoryStore(self.path).create_run(
            snapshots,
            provider=provider,
            model=model,
            version=version,
            requested_limit=requested_limit,
            batch_size=batch_size,
            scope=scope,
        )

    def record_smart_review_results(
        self,
        run_id: str,
        results: Sequence[dict[str, Any]],
    ) -> None:
        """保存一批智慧校對候選結果；新批次寫入 JSON，舊批次保留 SQLite 讀寫。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            history.record_results(run_id, results)
            return
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
        """把智慧校對請求錯誤記錄到 JSON 或舊版 SQLite 批次。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            history.mark_request_error(run_id, ordinals, error_code=error_code)
            return
        with self._tx() as conn:
            conn.executemany(
                "UPDATE smart_review_candidate SET status='request_failed', validation_error=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                ((error_code, run_id, int(ordinal)) for ordinal in ordinals),
            )

    def mark_smart_review_pending_cancelled(self, run_id: str) -> int:
        """標記已選取但取消前尚未送出的智慧校對項目。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return history.mark_pending_cancelled(run_id)
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
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            history.finish_run(
                run_id,
                processed_count=processed_count,
                valid_count=valid_count,
                invalid_count=invalid_count,
                request_error_count=request_error_count,
                metrics=metrics,
                status=status
                or ("completed" if not request_error_count else "partial"),
            )
            return
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
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return history.get_run(run_id)
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
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return history.list_candidates(
                run_id, status=status, limit=limit, offset=offset
            )
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
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return history.count_candidates(run_id, status=status)
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

    def smart_review_run_breakdown(
        self, run_id: str, *, error_limit: int = 5
    ) -> dict[str, Any]:
        """統計單一執行的候選狀態分佈與最常見的驗證失敗原因，供 UI 摘要使用。

        兩個查詢都只掃該 run 的主鍵前綴；排序在 Python 端完成，避免臨時排序。
        """
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return history.breakdown(run_id, error_limit=error_limit)
        status_rows = self._q(
            "SELECT status, COUNT(*) FROM smart_review_candidate "
            "WHERE run_id=? GROUP BY status",
            (run_id,),
        )
        error_rows = self._q(
            "SELECT validation_error, COUNT(*) FROM smart_review_candidate "
            "WHERE run_id=? AND validation_error IS NOT NULL AND validation_error != '' "
            "GROUP BY validation_error",
            (run_id,),
        )
        errors = sorted(
            ((str(message), int(count)) for message, count in error_rows),
            key=lambda item: (-item[1], item[0]),
        )
        return {
            "status": {str(status): int(count) for status, count in status_rows},
            "errors": errors[: max(0, int(error_limit))],
        }

    def _save_smart_review_draft_json(
        self,
        history: SmartReviewHistoryStore,
        run_id: str,
        ordinal: int,
        draft_zh_tw: str,
    ) -> str:
        """以目前資料庫快照驗證後，把草稿寫回 JSON 候選。"""
        draft = _validate_text(
            draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
        )
        with history.locked():
            candidate = history.get_candidate(run_id, ordinal)
            if candidate is None:
                return "missing"
            if candidate.get("status") != "ready":
                return "not_ready"
            if not draft.strip():
                return "invalid_draft"
            with self._lock:
                current = self._one(
                    _smart_review_current_snapshot_sql(),
                    (int(candidate["entry_id"]),),
                )
                expected = (
                    candidate.get("kind"),
                    candidate.get("mc_version"),
                    candidate.get("mod_id"),
                    candidate.get("entry_key"),
                    candidate.get("en_us"),
                    candidate.get("current_zh_tw"),
                    candidate.get("effective_source"),
                    candidate.get("review_status"),
                    candidate.get("effective_revision"),
                )
                if current is None or tuple(current) != expected:
                    history.update_candidate(
                        run_id,
                        ordinal,
                        status="stale",
                        validation_error="snapshot_stale",
                    )
                    return "stale"
                current_manual = self._one(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (int(candidate["entry_id"]), SRC_MANUAL),
                )
            manual_snapshot = (
                candidate.get("manual_zh_tw"),
                candidate.get("manual_checker"),
                candidate.get("manual_review_status"),
                candidate.get("manual_revision"),
            )
            current_manual_snapshot = (
                tuple(current_manual) if current_manual else (None, None, None, None)
            )
            if current_manual_snapshot != manual_snapshot:
                history.update_candidate(
                    run_id,
                    ordinal,
                    status="stale",
                    validation_error="manual_snapshot_stale",
                )
                return "stale"
            if current_manual is not None and current_manual[2] == "reviewed":
                history.update_candidate(
                    run_id,
                    ordinal,
                    status="stale",
                    validation_error="manual_reviewed_requires_manual_edit",
                )
                return "manual_reviewed"
            if translation_issues(
                str(candidate.get("en_us") or ""),
                draft,
                kind=str(candidate.get("kind") or "lang"),
                repair=False,
            ):
                return "invalid_format"
            history.update_candidate(run_id, ordinal, draft_zh_tw=draft)
            return "saved"

    def save_smart_review_draft(
        self, run_id: str, ordinal: int, draft_zh_tw: str
    ) -> str:
        """保存可人工編輯的智慧校對草稿，不碰生效翻譯與歷史。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return self._save_smart_review_draft_json(
                history, run_id, ordinal, draft_zh_tw
            )
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
        """列出 JSON 批次與舊版 SQLite 批次，依時間合併分頁。"""
        history_runs = SmartReviewHistoryStore(self.path).all_runs()
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
        combined = {str(run.get("id")): run for run in result}
        combined.update({str(run.get("id")): run for run in history_runs})
        ordered = sorted(
            combined.values(),
            key=lambda item: (
                str(item.get("started_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        start = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        return ordered[start : start + bounded_limit]

    def _apply_smart_review_candidate_json(
        self,
        history: SmartReviewHistoryStore,
        run_id: str,
        ordinal: int,
        *,
        actor: str,
        draft_zh_tw: str | None,
    ) -> str:
        """以資料庫 CAS 套用 JSON 候選，再回寫候選狀態。"""
        batch = uuid.uuid4().hex
        with history.locked():
            candidate = history.get_candidate(run_id, ordinal)
            if candidate is None:
                return "missing"
            if candidate.get("status") != "ready" or not candidate.get(
                "suggested_zh_tw"
            ):
                return "not_ready"
            proposed = candidate.get("draft_zh_tw") or candidate.get("suggested_zh_tw")
            if draft_zh_tw is not None:
                proposed = _validate_text(
                    draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
                )
                if not proposed.strip():
                    return "invalid_draft"
            entry_id = int(candidate["entry_id"])
            if translation_issues(
                str(candidate.get("en_us") or ""),
                str(proposed or ""),
                kind=str(candidate.get("kind") or "lang"),
                repair=False,
            ):
                history.update_candidate(
                    run_id,
                    ordinal,
                    status="invalid_format",
                    validation_error="suggested_zh_tw_format_invalid",
                )
                return "invalid_format"

            expected = (
                candidate.get("kind"),
                candidate.get("mc_version"),
                candidate.get("mod_id"),
                candidate.get("entry_key"),
                candidate.get("en_us"),
                candidate.get("current_zh_tw"),
                candidate.get("effective_source"),
                candidate.get("review_status"),
                candidate.get("effective_revision"),
            )
            manual_snapshot = (
                candidate.get("manual_zh_tw"),
                candidate.get("manual_checker"),
                candidate.get("manual_review_status"),
                candidate.get("manual_revision"),
            )
            stale_reason: str | None = None
            result_status = "applied"
            previous_manual = None
            with self._tx(bump_generation_on_noop=False) as conn:
                # BEGIN IMMEDIATE 必須早於快照讀取，讓 CAS 檢查與正式寫入
                # 位於同一個 SQLite 交易，避免讀取後被其他連線插入修改。
                current = conn.execute(
                    _smart_review_current_snapshot_sql(), (entry_id,)
                ).fetchone()
                if current is None or tuple(current) != expected:
                    result_status = "stale"
                    stale_reason = "snapshot_stale"
                else:
                    current_manual = conn.execute(
                        "SELECT zh_tw, checker, review_status, revision FROM translation "
                        "WHERE entry_id=? AND source=?",
                        (entry_id, SRC_MANUAL),
                    ).fetchone()
                    current_manual_snapshot = (
                        tuple(current_manual)
                        if current_manual
                        else (None, None, None, None)
                    )
                    if current_manual_snapshot != manual_snapshot:
                        result_status = "stale"
                        stale_reason = "manual_snapshot_stale"
                    elif current_manual is not None and current_manual[2] == "reviewed":
                        result_status = "manual_reviewed"
                        stale_reason = "manual_reviewed_requires_manual_edit"
                    else:
                        previous_manual = current_manual
                        conn.execute(
                            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
                            "VALUES (?,?,?,'','unreviewed') ON CONFLICT(entry_id, source) DO UPDATE SET "
                            "zh_tw=excluded.zh_tw, checker='', review_status='unreviewed', "
                            "updated_at=CURRENT_TIMESTAMP",
                            (entry_id, SRC_MANUAL, proposed),
                        )
                        new_revision = conn.execute(
                            "SELECT revision FROM translation WHERE entry_id=? AND source=?",
                            (entry_id, SRC_MANUAL),
                        ).fetchone()[0]
                        conn.execute(
                            "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, new_zh_tw, "
                            "prev_manual, prev_checker, prev_review_status, new_checker, new_review_status, "
                            "prev_revision, new_revision, source_id, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                entry_id,
                                batch,
                                actor,
                                "smart_review_apply",
                                candidate.get("current_zh_tw"),
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
                        self._refresh(conn, [entry_id])

            if result_status != "applied":
                history.update_candidate(
                    run_id,
                    ordinal,
                    status="stale",
                    validation_error=stale_reason,
                )
                return (
                    "manual_reviewed" if result_status == "manual_reviewed" else "stale"
                )
            history.update_candidate(
                run_id,
                ordinal,
                draft_zh_tw=proposed
                if draft_zh_tw is not None
                else candidate.get("draft_zh_tw"),
                status="applied",
                applied_batch=batch,
                applied_at=history.timestamp(),
            )
            return "applied"

    def apply_smart_review_candidate(
        self,
        run_id: str,
        ordinal: int,
        *,
        actor: str = "智慧校對",
        draft_zh_tw: str | None = None,
    ) -> str:
        """僅在生效快照未變更時以 CAS 套用智慧校對候選。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            return self._apply_smart_review_candidate_json(
                history,
                run_id,
                ordinal,
                actor=actor,
                draft_zh_tw=draft_zh_tw,
            )
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
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            candidate = history.get_candidate(run_id, ordinal)
            if candidate is None or candidate.get("status") != "ready":
                return "not_ready"
            history.update_candidate(run_id, ordinal, status="rejected")
            return "rejected"
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
    batch_interval_sec: float
    chatgpt_retry_count: int = 0
    gemini_transient_retry_count: int = 0


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


def _smart_review_response_item_count(raw: object) -> int | None:
    """只讀回應的 items 數量，不把模型內容寫入診斷事件。"""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    items = parsed.get("items") if isinstance(parsed, dict) else None
    return len(items) if isinstance(items, list) else None


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
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    """依既有 Gemini 503 policy 對 500/502/503/504 做有限次重試。"""
    from translation_tool.core.lm_translator_main import (
        GEMINI_503_MAX_RETRY_AFTER_SEC,
        GEMINI_503_RETRY_DELAYS_SEC,
        _classify_batch_error,
        _parse_gemini_retry_after_seconds,
    )

    error_kind = _classify_batch_error(error, status)
    if isinstance(error, requests.Timeout):
        error_kind = "timeout"
    elif isinstance(error, requests.ConnectionError):
        error_kind = "connection_error"
    if error_kind not in {
        "server_error",
        "service_unavailable",
        "deadline_exceeded",
        "timeout",
        "connection_error",
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
    if event_callback is not None:
        event_callback(
            {
                "kind": "retry_wait",
                "retry_number": retry_index + 1,
                "retry_total": len(GEMINI_503_RETRY_DELAYS_SEC),
                "wait_seconds": wait_sec,
                "retry_after_seconds": retry_after,
                "error_kind": error_kind,
                "http_status": status,
                "exception_type": type(error).__name__,
                "provider_overload": bool(
                    status == 503 or error_kind == "service_unavailable"
                ),
                "quota_or_rate_limit": bool(status == 429),
                "timeout": bool(error_kind == "timeout"),
            }
        )
    wait_started = time.monotonic()
    quota = get_model_quota_registry()
    quota.defer_request_slot(runtime.provider, model_name, wait_sec)
    _smart_review_sleep(wait_sec, cancel_check)
    if event_callback is not None:
        event_callback(
            {
                "kind": "retry_resume",
                "retry_number": retry_index + 1,
                "retry_total": len(GEMINI_503_RETRY_DELAYS_SEC),
                "wait_seconds": wait_sec,
                "elapsed_seconds": time.monotonic() - wait_started,
            }
        )
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
    task_id: str | None = None,
) -> SmartReviewRunResult:
    """執行有界的 AI 智慧校對，只保存候選，不直接寫入翻譯。"""
    if not model_name.strip():
        raise ValueError("smart review 必須指定模型")
    workflow_started = time.monotonic()
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
    batch_size = max(1, min(int(batch_size), 100))
    planning_started = time.monotonic()
    planned_batch_count = max(1, (len(snapshots) + batch_size - 1) // batch_size)
    planning_seconds = round(time.monotonic() - planning_started, 3)
    run_record_started = time.monotonic()
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
    run_record_seconds = round(time.monotonic() - run_record_started, 3)
    executor = CommonBatchExecutor(
        operation="smart_review",
        provider=provider,
        model=model_name,
        task_id=task_id,
        run_id=run_id,
        cancel_check=cancel_check,
    )
    executor.add_timing("selection_ms", selection_seconds * 1000)
    executor.add_timing("planning_ms", planning_seconds * 1000)
    executor.add_timing("history_write_ms", run_record_seconds * 1000)
    processed = valid = invalid = request_errors = 0
    no_change = suggestions = needs_context = 0
    output_token_cap = _smart_review_output_token_cap(lm_config)
    input_token_budget = _smart_review_input_budget(lm_config, provider, model_name)
    started = workflow_started
    cancelled = False
    failed = False
    token_totals: dict[str, int | None] = {
        "prompt_tokens": None,
        "output_tokens": None,
    }
    metrics: dict[str, Any] = {
        "duration_seconds": 0.0,
        "total_duration_seconds": 0.0,
        "setup_seconds": round(max(0.0, selection_started - workflow_started), 3),
        "selection_seconds": selection_seconds,
        "run_record_seconds": run_record_seconds,
        "preparation_seconds": 0.0,
        "rate_limit_wait_seconds": 0.0,
        "retry_wait_seconds": 0.0,
        "network_retry_wait_seconds": 0.0,
        "api_seconds": 0.0,
        "parse_seconds": 0.0,
        "result_write_seconds": 0.0,
        "batch_seconds": 0.0,
        "batch_count": 0,
        "prompt_tokens": None,
        "output_tokens": None,
        "no_change_count": 0,
        "suggestion_count": 0,
        "suggest_revision_count": 0,
        "needs_context_count": 0,
        "request_attempts": 0,
        "smart_review_attempts": 0,
        "network_attempts": 0,
        "network_retry_count": 0,
        "retry_count": 0,
        "transient_retry_count": 0,
        "overload_count": 0,
        "key_rotation_count": 0,
        "shrink_count": 0,
        "input_budget_shrink_count": 0,
        "input_budget_exceeded_count": 0,
        "response_invalid_count": 0,
        "usage_missing_attempts": 0,
        "usage_missing_output_attempts": 0,
        "permanent_error_count": 0,
        "failure_classes": {},
        "validation_errors": {},
        "input_token_budget": input_token_budget,
    }
    current_batch_handle = None

    def merge_common_metrics() -> None:
        """補上共用執行器的計時與識別欄位，不改變既有 Smart Review 計數。"""
        common = executor.metrics_snapshot()
        for key in (
            "selection_ms",
            "planning_ms",
            "prompt_build_ms",
            "quota_wait_ms",
            "retry_backoff_ms",
            "api_ms",
            "response_parse_ms",
            "validation_ms",
            "commit_ms",
            "history_write_ms",
            "network_retry_wait_ms",
            "total_ms",
            "batch_count",
            "error_batches",
            "task_id",
            "run_id",
            "operation",
            "provider",
            "model",
        ):
            metrics[key] = common[key]
        metrics["common_request_attempts"] = common["request_attempts"]
        metrics["common_provider_attempts"] = common["provider_attempts"]
        metrics["common_retry_count"] = common["retry_count"]
        metrics["common_transient_retry_count"] = common["transient_retry_count"]

    # 延遲取得主翻譯流程的共用 policy，避免 translation_db 初始化時形成循環 import。
    from translation_tool.core.lm_translator_main import (
        _batch_interval_seconds,
        _classify_batch_error,
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

    def report_progress(
        *,
        phase: str = "batch",
        timing: dict[str, Any] | None = None,
        event: dict[str, Any] | None = None,
    ) -> None:
        """回報目前已處理數量、階段與可診斷的耗時。"""
        if progress is not None:
            snapshot: dict[str, Any] = {
                "run_id": run_id,
                "processed": processed,
                "selected": len(snapshots),
                "valid": valid,
                "invalid": invalid,
                "request_errors": request_errors,
                "status": "running",
                "phase": phase,
            }
            if timing is not None:
                snapshot["timing"] = timing
            if event is not None:
                snapshot["event"] = event
            progress(snapshot)

    def emit_event(kind: str, **fields: Any) -> None:
        """回報不含 prompt、response、key 或錯誤原文的結構化事件。"""
        if current_batch_handle is not None:
            if kind == "attempt_start":
                current_batch_handle.attempt = int(
                    fields.get("smart_review_attempt")
                    or current_batch_handle.attempt + 1
                )
                executor.metrics.request_attempts += 1
            elif kind == "attempt_success":
                elapsed_value = fields.get("api_elapsed_seconds")
                try:
                    elapsed_ms = float(elapsed_value or 0) * 1000
                    executor.metrics.add_ms("api_ms", elapsed_ms)
                    fields.setdefault("elapsed_ms", round(elapsed_ms))
                except (TypeError, ValueError):
                    pass
            elif kind == "network_attempt_start":
                executor.metrics.network_attempts += 1
            elif kind == "network_retry_wait":
                executor.metrics.network_retry_count += 1
                try:
                    wait_ms = max(0, float(fields.get("wait_seconds") or 0) * 1000)
                    executor.metrics.add_ms("network_retry_wait_ms", wait_ms)
                    fields.setdefault("wait_ms", round(wait_ms))
                except (TypeError, ValueError):
                    pass
            elif kind == "retry_wait":
                wait_value = fields.get("wait_seconds")
                try:
                    wait_ms = max(0, float(wait_value or 0) * 1000)
                except (TypeError, ValueError):
                    wait_ms = 0
                current_batch_handle.retry_count += 1
                executor.metrics.retry_count += 1
                executor.metrics.transient_retry_count += 1
                executor.metrics.add_ms("retry_backoff_ms", wait_ms)
                fields.setdefault("wait_ms", round(wait_ms))
                fields.setdefault("backoff_ms", round(wait_ms))
            elif kind == "rate_limit_wait":
                wait_value = fields.get("wait_seconds")
                try:
                    executor.metrics.add_ms(
                        "quota_wait_ms", max(0, float(wait_value or 0) * 1000)
                    )
                except (TypeError, ValueError):
                    pass
            elif kind == "attempt_failure" and fields.get("key_rotated"):
                executor.metrics.key_rotation_count += 1
        if progress is None:
            return
        if current_batch_handle is not None:
            event = current_batch_handle.emit(kind, **fields)
        else:
            event = executor.emit(kind, **fields)
        report_progress(
            phase="event",
            event=event,
        )

    def finish_batch_timing(
        timing: dict[str, Any],
        started_at: float,
        attempts_before: int,
        status: str,
        *,
        counters_before: dict[str, int],
        retry_before: dict[str, int],
        validation_errors: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """結算一個邏輯批次的耗時並回傳不含敏感資料的診斷摘要。"""
        elapsed = round(time.monotonic() - started_at, 3)
        finalized = dict(timing)
        finalized["elapsed_seconds"] = elapsed
        finalized["attempts"] = max(
            0, int(metrics["request_attempts"]) - attempts_before
        )
        finalized["smart_review_attempts"] = max(
            0,
            int(metrics["smart_review_attempts"])
            - retry_before.get("smart_review_attempts", 0),
        )
        for key in (
            "retry_count",
            "transient_retry_count",
            "overload_count",
            "key_rotation_count",
        ):
            finalized[key] = max(
                0, int(metrics[key]) - retry_before.get(key, int(metrics[key]))
            )
        finalized.update(
            {
                "valid_count": max(0, valid - counters_before.get("valid", valid)),
                "invalid_count": max(
                    0, invalid - counters_before.get("invalid", invalid)
                ),
                "needs_context_count": max(
                    0,
                    needs_context - counters_before.get("needs_context", needs_context),
                ),
                "no_change_count": max(
                    0, no_change - counters_before.get("no_change", no_change)
                ),
                "suggestion_count": max(
                    0, suggestions - counters_before.get("suggestions", suggestions)
                ),
                "suggest_revision_count": max(
                    0, suggestions - counters_before.get("suggestions", suggestions)
                ),
                "validation_errors": dict(validation_errors or {}),
            }
        )
        finalized["status"] = status
        metrics["batch_count"] = int(metrics["batch_count"]) + 1
        metrics["batch_seconds"] = round(float(metrics["batch_seconds"]) + elapsed, 3)
        return finalized

    def report_batch_complete(timing: dict[str, Any]) -> None:
        """同時送出舊版批次進度與可供 UI 即時顯示的完整事件。"""
        event_fields = {
            "batch_number": timing.get("batch_number"),
            "batch_total": timing.get("batch_total"),
            "ordinal_start": timing.get("ordinal_start"),
            "ordinal_end": timing.get("ordinal_end"),
            "actual_count": timing.get("actual_count"),
            "attempts": timing.get("attempts", 0),
            "smart_review_attempts": timing.get("smart_review_attempts", 0),
            "retry_count": timing.get("retry_count", 0),
            "rate_limit_wait_seconds": timing.get("rate_limit_wait_seconds", 0.0),
            "retry_wait_seconds": timing.get("retry_wait_seconds", 0.0),
            "network_retry_wait_seconds": timing.get("network_retry_wait_seconds", 0.0),
            "api_seconds": timing.get("api_seconds", 0.0),
            "parse_seconds": timing.get("parse_seconds", 0.0),
            "result_write_seconds": timing.get("result_write_seconds", 0.0),
            "elapsed_seconds": timing.get("elapsed_seconds", 0.0),
            "valid_count": timing.get("valid_count", 0),
            "invalid_count": timing.get("invalid_count", 0),
            "needs_context_count": timing.get("needs_context_count", 0),
            "no_change_count": timing.get("no_change_count", 0),
            "suggestion_count": timing.get("suggestion_count", 0),
            "suggest_revision_count": timing.get("suggest_revision_count", 0),
            "validation_errors": timing.get("validation_errors", {}),
            "request_errors": request_errors,
        }
        if current_batch_handle is not None:
            current_batch_handle.complete(
                status=str(timing.get("status") or "completed"),
                processed_count=processed,
                emit=False,
                **event_fields,
            )
            event = current_batch_handle.emit(
                "batch_complete", processed_count=processed, **event_fields
            )
        else:
            event = executor.emit("batch_complete", **event_fields)
        report_progress(phase="batch_complete", timing=timing, event=event)

    def emit_attempt_failure(
        error: Exception,
        *,
        batch_number: int,
        smart_review_attempt: int,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        key_rotated: bool = False,
        error_kind: str | None = None,
    ) -> None:
        """送出可供診斷的錯誤分類；不使用 provider 的完整錯誤內容。"""
        kind = error_kind or _classify_batch_error(error, status)
        if isinstance(error, requests.Timeout):
            kind = "timeout"
        elif isinstance(error, requests.ConnectionError):
            kind = "connection_error"
        classification = classify_provider_error(error, status)
        if status == 503 or kind == "service_unavailable":
            metrics["overload_count"] = int(metrics["overload_count"]) + 1
            executor.metrics.overload_count += 1
        executor.metrics.error_batches.append(batch_number)
        code = str(getattr(error, "code", "") or "").lower()
        emit_event(
            "attempt_failure",
            batch_number=batch_number,
            batch_total=planned_batch_count,
            smart_review_attempt=smart_review_attempt,
            http_status=status,
            error_kind=kind,
            error_class=classification["error_class"],
            provider_error_code=classification["provider_error_code"],
            exception_type=type(error).__name__,
            provider_overload=bool(status == 503 or kind == "service_unavailable"),
            quota_or_rate_limit=bool(status == 429 or "quota" in code),
            timeout=bool(isinstance(error, requests.Timeout) or kind == "timeout"),
            connection=classification["connection"],
            retry_after_seconds=retry_after_seconds,
            retry_after_ms=(
                round(float(retry_after_seconds) * 1000)
                if retry_after_seconds is not None
                else None
            ),
            will_retry=bool(
                key_rotated
                or status in {408, 429, 500, 502, 503, 504}
                or isinstance(error, (requests.Timeout, requests.ConnectionError))
            ),
            key_rotated=key_rotated,
            network_attempts=int(metrics.get("network_attempts", 0)),
            network_retry_count=int(metrics.get("network_retry_count", 0)),
        )

    report_progress(
        phase="selection_complete",
        timing={
            "selection_seconds": selection_seconds,
            "planning_seconds": planning_seconds,
            "run_record_seconds": run_record_seconds,
        },
    )

    try:
        position = 0
        adaptive_batch_size = batch_size
        batch_number = 0
        while position < len(snapshots):
            if is_cancelled() or (cancel_check is not None and cancel_check()):
                cancelled = True
                break
            current_size = min(adaptive_batch_size, len(snapshots) - position)
            batch_number += 1
            batch_started = time.monotonic()
            attempts_before = int(metrics["request_attempts"])
            counters_before = {
                "valid": valid,
                "invalid": invalid,
                "needs_context": needs_context,
                "no_change": no_change,
                "suggestions": suggestions,
            }
            retry_before = {
                key: int(metrics[key])
                for key in (
                    "smart_review_attempts",
                    "retry_count",
                    "transient_retry_count",
                    "overload_count",
                    "key_rotation_count",
                )
            }
            batch_timing: dict[str, Any] = {
                "batch_number": batch_number,
                "batch_total": max(planned_batch_count, batch_number),
                "ordinal_start": int(snapshots[position].ordinal),
                "ordinal_end": int(
                    snapshots[
                        min(len(snapshots) - 1, position + current_size - 1)
                    ].ordinal
                ),
                "actual_count": current_size,
                "preparation_seconds": 0.0,
                "rate_limit_wait_seconds": 0.0,
                "retry_wait_seconds": 0.0,
                "network_retry_wait_seconds": 0.0,
                "api_seconds": 0.0,
                "parse_seconds": 0.0,
                "result_write_seconds": 0.0,
            }
            current_batch_handle = executor.begin_batch(
                batch_index=batch_number,
                batch_total=max(planned_batch_count, batch_number),
                batch_size=current_size,
                selected_count=len(snapshots),
                processed_count=processed,
                ordinal_start=batch_timing["ordinal_start"],
                ordinal_end=batch_timing["ordinal_end"],
            )
            emit_event(
                "batch_start",
                batch_number=batch_number,
                batch_total=max(planned_batch_count, batch_number),
                ordinal_start=batch_timing["ordinal_start"],
                ordinal_end=batch_timing["ordinal_end"],
                expected_count=current_size,
                provider=provider,
                model=model_name,
            )
            abort_remaining = False
            batch_validation_errors: dict[str, int] = {}
            while True:
                if is_cancelled() or (cancel_check is not None and cancel_check()):
                    cancelled = True
                    break
                batch = snapshots[position : position + current_size]
                batch_timing["ordinal_end"] = int(batch[-1].ordinal)
                preparation_started = time.monotonic()
                payload = build_smart_review_payload(batch)
                response_schema = smart_review_response_schema(
                    [str(item.ordinal) for item in batch]
                )
                estimated_input = _smart_review_estimated_input_tokens(
                    payload, response_schema
                )
                metrics["last_estimated_input_tokens"] = round(estimated_input, 2)
                preparation_seconds = time.monotonic() - preparation_started
                batch_timing["preparation_seconds"] = round(
                    float(batch_timing["preparation_seconds"]) + preparation_seconds,
                    3,
                )
                metrics["preparation_seconds"] = round(
                    float(metrics["preparation_seconds"]) + preparation_seconds,
                    3,
                )
                executor.add_timing("prompt_build_ms", preparation_seconds * 1000)
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
                    batch_timing["actual_count"] = len(batch)
                    mark_request_failure(
                        [item.ordinal for item in batch],
                        error_code="request_failed:input_budget_exceeded",
                    )
                    processed += len(batch)
                    position += len(batch)
                    report_batch_complete(
                        finish_batch_timing(
                            batch_timing,
                            batch_started,
                            attempts_before,
                            "input_budget_exceeded",
                            counters_before=counters_before,
                            retry_before=retry_before,
                            validation_errors=batch_validation_errors,
                        )
                    )
                    break

                retry_runtime = _SmartReviewRetryRuntime(
                    provider=provider,
                    batch_interval_sec=_batch_interval_seconds(lm_config),
                )
                request_failed = False
                shrink_requested = False
                raw = ""
                smart_review_attempt = 0
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

                    smart_review_attempt += 1
                    metrics["smart_review_attempts"] = (
                        int(metrics["smart_review_attempts"]) + 1
                    )

                    def record_retry_event(
                        event: dict[str, Any],
                        *,
                        _batch_timing: dict[str, Any] = batch_timing,
                        _batch_number: int = batch_number,
                        _smart_review_attempt: int = smart_review_attempt,
                    ) -> None:
                        """累計 retry wait 並把安全事件轉回 Smart Review。"""
                        kind = str(event.get("kind") or "retry")
                        wait_value = event.get("wait_seconds")
                        try:
                            wait_seconds = max(0.0, float(wait_value or 0.0))
                        except (TypeError, ValueError):
                            wait_seconds = 0.0
                        if kind == "retry_wait":
                            metrics["retry_wait_seconds"] = round(
                                float(metrics["retry_wait_seconds"]) + wait_seconds,
                                3,
                            )
                            _batch_timing["retry_wait_seconds"] = round(
                                float(_batch_timing["retry_wait_seconds"])
                                + wait_seconds,
                                3,
                            )
                        elif kind == "network_retry_wait":
                            metrics["network_retry_count"] = (
                                int(metrics["network_retry_count"]) + 1
                            )
                            metrics["network_retry_wait_seconds"] = round(
                                float(metrics["network_retry_wait_seconds"])
                                + wait_seconds,
                                3,
                            )
                            _batch_timing["network_retry_wait_seconds"] = round(
                                float(_batch_timing["network_retry_wait_seconds"])
                                + wait_seconds,
                                3,
                            )
                        fields = {
                            key: value for key, value in event.items() if key != "kind"
                        }
                        emit_event(
                            kind,
                            batch_number=_batch_number,
                            batch_total=max(planned_batch_count, _batch_number),
                            smart_review_attempt=_smart_review_attempt,
                            **fields,
                        )

                    def on_network_event(
                        event: dict[str, object],
                        *,
                        _batch_number: int = batch_number,
                        _smart_review_attempt: int = smart_review_attempt,
                        _record_retry_event: Callable[
                            [dict[str, Any]], None
                        ] = record_retry_event,
                    ) -> None:
                        """接收 API client 的 network attempt 診斷。"""
                        kind = str(event.get("kind") or "network_event")
                        if kind == "network_attempt_start":
                            metrics["network_attempts"] = (
                                int(metrics["network_attempts"]) + 1
                            )
                        if kind == "network_retry_wait":
                            _record_retry_event(event)
                            return
                        fields = {
                            key: value for key, value in event.items() if key != "kind"
                        }
                        emit_event(
                            kind,
                            batch_number=_batch_number,
                            batch_total=max(planned_batch_count, _batch_number),
                            smart_review_attempt=_smart_review_attempt,
                            **fields,
                        )

                    def on_rate_limit_event(
                        event: dict[str, object],
                        *,
                        _batch_number: int = batch_number,
                        _smart_review_attempt: int = smart_review_attempt,
                    ) -> None:
                        """把配額時鐘等待轉成 UI 可顯示的事件。"""
                        kind = str(event.get("kind") or "rate_limit_wait")
                        fields = {
                            key: value for key, value in event.items() if key != "kind"
                        }
                        emit_event(
                            kind,
                            batch_number=_batch_number,
                            batch_total=max(planned_batch_count, _batch_number),
                            smart_review_attempt=_smart_review_attempt,
                            **fields,
                        )

                    attempt_meta: dict[str, Any] | None = None
                    try:
                        wait_started = time.monotonic()
                        try:
                            _wait_for_request_slot(
                                retry_runtime,
                                model_name,
                                cancel_check=cancel_check,
                                wait_event_callback=on_rate_limit_event,
                            )
                        finally:
                            wait_seconds = time.monotonic() - wait_started
                            batch_timing["rate_limit_wait_seconds"] = round(
                                float(batch_timing["rate_limit_wait_seconds"])
                                + wait_seconds,
                                3,
                            )
                            metrics["rate_limit_wait_seconds"] = round(
                                float(metrics["rate_limit_wait_seconds"])
                                + wait_seconds,
                                3,
                            )
                        attempt_meta = {}
                        emit_event(
                            "attempt_start",
                            batch_number=batch_number,
                            batch_total=max(planned_batch_count, batch_number),
                            smart_review_attempt=smart_review_attempt,
                            expected_count=len(batch),
                            provider=provider,
                            model=model_name,
                            network_attempts=int(metrics["network_attempts"]),
                        )
                        api_started = time.monotonic()
                        try:
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
                                network_event_callback=on_network_event,
                                **chatgpt_options,
                            )
                        finally:
                            api_seconds = time.monotonic() - api_started
                            batch_timing["api_seconds"] = round(
                                float(batch_timing["api_seconds"]) + api_seconds,
                                3,
                            )
                            metrics["api_seconds"] = round(
                                float(metrics["api_seconds"]) + api_seconds,
                                3,
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
                        emit_attempt_failure(
                            exc,
                            batch_number=batch_number,
                            smart_review_attempt=smart_review_attempt,
                            error_kind="oauth",
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
                        emit_attempt_failure(
                            exc,
                            batch_number=batch_number,
                            smart_review_attempt=smart_review_attempt,
                            status=exc.status,
                            retry_after_seconds=getattr(exc, "retry_after", None),
                        )
                        try:
                            with (
                                cancel_scope(cancel_check)
                                if cancel_check is not None
                                else nullcontext()
                            ):
                                action = _handle_chatgpt_api_error(
                                    retry_runtime,
                                    exc,
                                    retry_event_callback=record_retry_event,
                                )
                        except TaskCancelled:
                            cancelled = True
                            break
                        except RuntimeError:
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
                        error_json: dict[str, Any] = {}
                        try:
                            decoded = (
                                exc.response.json() if exc.response is not None else {}
                            )
                            if isinstance(decoded, dict):
                                error_json = decoded
                        except (AttributeError, TypeError, ValueError):
                            error_json = {}
                        retry_after = _parse_gemini_retry_after_seconds(
                            exc.response, error_json
                        )
                        if status_code == 429 and retry_after:
                            get_model_quota_registry().defer_request_slot(
                                provider, model_name, retry_after
                            )
                        key_rotated = False
                        if (
                            key_cycle is not None
                            and status_code in {403, 429}
                            and key_cycle.mark_failed(
                                reason="forbidden" if status_code == 403 else None
                            )
                        ):
                            key_rotated = True
                            metrics["key_rotation_count"] = (
                                int(metrics["key_rotation_count"]) + 1
                            )
                            metrics["retry_count"] = int(metrics["retry_count"]) + 1
                            emit_attempt_failure(
                                exc,
                                batch_number=batch_number,
                                smart_review_attempt=smart_review_attempt,
                                status=status_code,
                                retry_after_seconds=retry_after,
                                key_rotated=key_rotated,
                            )
                            continue
                        if (
                            _payload_error_allows_shrink(exc, status_code)
                            and current_size > 1
                        ):
                            emit_attempt_failure(
                                exc,
                                batch_number=batch_number,
                                smart_review_attempt=smart_review_attempt,
                                status=status_code,
                                retry_after_seconds=retry_after,
                            )
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
                                    emit_attempt_failure(
                                        exc,
                                        batch_number=batch_number,
                                        smart_review_attempt=smart_review_attempt,
                                        status=status_code,
                                        retry_after_seconds=retry_after,
                                        key_rotated=True,
                                    )
                                    continue
                            emit_attempt_failure(
                                exc,
                                batch_number=batch_number,
                                smart_review_attempt=smart_review_attempt,
                                status=status_code,
                                retry_after_seconds=retry_after,
                            )
                            if _smart_review_transient_retry(
                                retry_runtime,
                                model_name,
                                exc,
                                status_code,
                                cancel_check=cancel_check,
                                event_callback=record_retry_event,
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
                        emit_attempt_failure(
                            exc,
                            batch_number=batch_number,
                            smart_review_attempt=smart_review_attempt,
                            status=status_code,
                            retry_after_seconds=retry_after,
                        )
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
                        emit_attempt_failure(
                            exc,
                            batch_number=batch_number,
                            smart_review_attempt=smart_review_attempt,
                        )
                        if _smart_review_transient_retry(
                            retry_runtime,
                            model_name,
                            exc,
                            None,
                            cancel_check=cancel_check,
                            event_callback=record_retry_event,
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
                        emit_attempt_failure(
                            exc,
                            batch_number=batch_number,
                            smart_review_attempt=smart_review_attempt,
                        )
                        mark_request_failure([item.ordinal for item in batch], exc)
                        request_failed = True
                        break
                    else:
                        _smart_review_record_usage(
                            metrics, attempt_meta or {}, token_totals
                        )
                        emit_event(
                            "attempt_success",
                            batch_number=batch_number,
                            batch_total=max(planned_batch_count, batch_number),
                            smart_review_attempt=smart_review_attempt,
                            api_elapsed_seconds=api_seconds,
                            http_status=(attempt_meta or {}).get("http_status", 200),
                            finish_reason=(attempt_meta or {}).get("finish_reason"),
                            expected_count=len(batch),
                            actual_count=_smart_review_response_item_count(raw),
                            prompt_tokens=(attempt_meta or {}).get("prompt_tokens"),
                            output_tokens=(attempt_meta or {}).get("output_tokens")
                            or (attempt_meta or {}).get("candidates_tokens"),
                            network_attempts=int(metrics["network_attempts"]),
                            network_retry_count=int(metrics["network_retry_count"]),
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
                    batch_timing["actual_count"] = len(batch)
                    report_batch_complete(
                        finish_batch_timing(
                            batch_timing,
                            batch_started,
                            attempts_before,
                            "request_failed",
                            counters_before=counters_before,
                            retry_before=retry_before,
                            validation_errors=batch_validation_errors,
                        )
                    )
                    break
                if shrink_requested:
                    smaller = _next_smart_review_batch_size(current_size)
                    if smaller < current_size:
                        current_size = smaller
                        adaptive_batch_size = min(adaptive_batch_size, current_size)
                        metrics["shrink_count"] = int(metrics["shrink_count"]) + 1
                        continue
                parse_started = time.monotonic()
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
                finally:
                    parse_seconds = time.monotonic() - parse_started
                    batch_timing["parse_seconds"] = round(
                        float(batch_timing["parse_seconds"]) + parse_seconds,
                        3,
                    )
                    metrics["parse_seconds"] = round(
                        float(metrics["parse_seconds"]) + parse_seconds,
                        3,
                    )
                    executor.add_timing("response_parse_ms", parse_seconds * 1000)
                write_started = time.monotonic()
                try:
                    db.record_smart_review_results(run_id, results)
                finally:
                    write_seconds = time.monotonic() - write_started
                    batch_timing["result_write_seconds"] = round(
                        float(batch_timing["result_write_seconds"]) + write_seconds,
                        3,
                    )
                    metrics["result_write_seconds"] = round(
                        float(metrics["result_write_seconds"]) + write_seconds,
                        3,
                    )
                    executor.add_timing("history_write_ms", write_seconds * 1000)
                    executor.add_timing("commit_ms", write_seconds * 1000)
                validation_started = time.monotonic()
                for result in results:
                    if result.get("validation_error"):
                        invalid += 1
                        reason = str(result["validation_error"])[:160]
                        batch_validation_errors[reason] = (
                            batch_validation_errors.get(reason, 0) + 1
                        )
                        validation_errors = metrics["validation_errors"]
                        validation_errors[reason] = (
                            int(validation_errors.get(reason, 0)) + 1
                        )
                        continue
                    valid += 1
                    if result["decision"] == "no_change":
                        no_change += 1
                    elif result["decision"] == "suggest_revision":
                        suggestions += 1
                    else:
                        needs_context += 1
                executor.add_timing(
                    "validation_ms", (time.monotonic() - validation_started) * 1000
                )
                processed += len(batch)
                position += len(batch)
                adaptive_batch_size = min(adaptive_batch_size, current_size)
                batch_timing["actual_count"] = len(batch)
                report_batch_complete(
                    finish_batch_timing(
                        batch_timing,
                        batch_started,
                        attempts_before,
                        "completed",
                        counters_before=counters_before,
                        retry_before=retry_before,
                        validation_errors=batch_validation_errors,
                    )
                )
                break
            if cancelled:
                break
    except TaskCancelled:
        cancelled = True
    except BaseException:
        failed = True
        db.mark_smart_review_pending_cancelled(run_id)
        total_seconds = round(time.monotonic() - started, 3)
        executor.metrics.total_ms = max(
            executor.metrics.total_ms, round(total_seconds * 1000)
        )
        executor.finish(status="failed")
        merge_common_metrics()
        metrics.update(
            {
                "duration_seconds": total_seconds,
                "total_duration_seconds": total_seconds,
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
    total_seconds = round(time.monotonic() - started, 3)
    executor.metrics.total_ms = max(
        executor.metrics.total_ms, round(total_seconds * 1000)
    )
    executor.finish(status=status)
    merge_common_metrics()
    metrics.update(
        {
            "duration_seconds": total_seconds,
            "total_duration_seconds": total_seconds,
            "prompt_tokens": token_totals["prompt_tokens"],
            "output_tokens": token_totals["output_tokens"],
            "no_change_count": no_change,
            "suggestion_count": suggestions,
            "suggest_revision_count": suggestions,
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
