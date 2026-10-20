"""Local AI smart-proofreading candidates for the Mod translation database.

The module deliberately separates three phases:

* snapshots and local context are read from SQLite;
* a model returns strict, machine-validated review candidates;
* a user explicitly applies a candidate through a compare-and-set transaction.

No model response is allowed to write a translation directly.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.official_locale import load_official_locale_index
from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewRunResult,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.quality import translation_issues
from translation_tool.translation_db.schema import SRC_AI, SRC_MANUAL
from translation_tool.utils.cancellation import TaskCancelled, is_cancelled
from translation_tool.utils.text_matching import contains_term, matching_terms

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
    """Build a provider JSON schema whose ids are limited to this request batch."""
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
                        "reason": {"type": "string", "maxLength": _MAX_REASON_LENGTH},
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
    """Create the opaque-id payload sent to a model."""
    return {
        "items": [
            {
                "id": str(item.ordinal),
                "kind": item.kind,
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
    """Strictly validate one model response against the exact input batch."""
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
    if result.get("validation_error"):
        return "invalid"
    return {
        "no_change": "no_change",
        "suggest_revision": "ready",
        "needs_context": "needs_context",
    }[result["decision"]]


def _response_reason(raw: object) -> str | None:
    """Read a reason from current or legacy candidate JSON safely."""
    if not raw:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    reason = value.get("reason") if isinstance(value, dict) else None
    return reason if isinstance(reason, str) else None


class SmartReviewMixin:
    """SQLite operations mixed into :class:`TranslationDB`."""

    def _smart_review_has_column(self, table: str, column: str) -> bool:
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
        """Select a stable, bounded set of current entries for proofreading.

        ``effective_source=None`` is kept for library compatibility, while the
        UI/service pass ``SRC_AI`` by default.  The selection always requires a
        non-empty effective translation and the requested review state.
        """
        bounded_limit = max(0, min(int(limit), 10_000))
        if bounded_limit == 0:
            return []
        self._smart_review_context_cache = {}
        params: list[Any] = []
        effective_review = (
            "CASE WHEN f.review_status IN ('unreviewed', 'reviewed', 'legacy_unknown') "
            f"THEN f.review_status WHEN f.source={SRC_MANUAL} THEN 'legacy_unknown' "
            "WHEN f.source IS NOT NULL THEN 'unreviewed' END"
        )
        if review_status not in {"unreviewed", "reviewed", "legacy_unknown"}:
            raise ValueError("smart review 不支援的審核狀態")
        where = [
            f"({effective_review}) = ?",
            "f.zh_tw <> ''",
            "e.en_us <> ''",
        ]
        params.append(review_status)
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
            needle = f"%{query.strip()}%"
            where.append("(e.key LIKE ? OR e.en_us LIKE ? OR f.zh_tw LIKE ?)")
            params.extend((needle, needle, needle))
        reference_cte = ""
        reference_join = ""
        order_prefix = ""
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
        rows = self._q(
            reference_cte
            + "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            f"f.zh_tw, f.source, ({effective_review}), t.revision, "
            "tm.zh_tw, tm.checker, tm.review_status, tm.revision "
            "FROM entry e JOIN effective f ON f.entry_id=e.id "
            + reference_join
            + "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
            f"LEFT JOIN translation tm ON tm.entry_id=e.id AND tm.source={SRC_MANUAL} "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY {order_prefix}e.mc_version, e.mod_id, e.kind, e.key, e.id "
            "LIMIT ? OFFSET ?",
            (*params, bounded_limit, max(0, int(offset))),
        )
        snapshots: list[SmartReviewSnapshot] = []
        for ordinal, row in enumerate(rows, 1):
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
        """Count the same selection used by ``smart_review_candidates``."""
        effective_review = (
            "CASE WHEN f.review_status IN ('unreviewed', 'reviewed', 'legacy_unknown') "
            f"THEN f.review_status WHEN f.source={SRC_MANUAL} THEN 'legacy_unknown' "
            "WHEN f.source IS NOT NULL THEN 'unreviewed' END"
        )
        if review_status not in {"unreviewed", "reviewed", "legacy_unknown"}:
            raise ValueError("smart review 不支援的審核狀態")
        clauses = [
            f"({effective_review}) = ?",
            "f.zh_tw <> ''",
            "e.en_us <> ''",
        ]
        params: list[Any] = [review_status]
        if version:
            clauses.append("e.mc_version=?")
            params.append(version)
        if mod_id:
            clauses.append("e.mod_id=?")
            params.append(mod_id)
        if kind:
            clauses.append("e.kind=?")
            params.append(kind)
        if effective_source is not None:
            clauses.append("f.source=?")
            params.append(int(effective_source))
        if query and query.strip():
            needle = f"%{query.strip()}%"
            clauses.append("(e.key LIKE ? OR e.en_us LIKE ? OR f.zh_tw LIKE ?)")
            params.extend((needle, needle, needle))
        reference_join = ""
        if require_reviewed_reference:
            reference_join = (
                "JOIN (SELECT r.kind, r.mod_id, r.key, r.en_us "
                "FROM entry r JOIN translation rf ON rf.entry_id=r.id "
                "WHERE rf.review_status='reviewed' AND trim(rf.zh_tw)<>'' "
                "AND r.en_us<>'' GROUP BY r.kind, r.mod_id, r.key, r.en_us "
                "HAVING COUNT(DISTINCT trim(rf.zh_tw))=1) rr "
                "ON rr.kind=e.kind AND rr.mod_id=e.mod_id AND rr.key=e.key "
                "AND rr.en_us=e.en_us "
            )
        row = self._one(
            "SELECT COUNT(*) FROM entry e JOIN effective f ON f.entry_id=e.id "
            + reference_join
            + f"WHERE {' AND '.join(clauses)}",
            params,
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
        """Build a bounded, replayable local evidence snapshot.

        The database rows are loaded through indexed module/key/text queries and
        cached for this selection.  Context records deliberately carry the
        source identity and match limitations so an evidence id cannot be
        mistaken for a generic, globally correct translation.
        """
        cache = getattr(self, "_smart_review_context_cache", {})
        key_rows_cache = cache.setdefault("key_rows", {})
        cache_key = (kind, mod_id, key)
        if cache_key not in key_rows_cache:
            key_rows_cache[cache_key] = self._q(
                "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
                "f.zh_tw, f.source, f.review_status, f.revision "
                "FROM entry e JOIN translation f ON f.entry_id=e.id "
                "WHERE e.kind=? AND e.mod_id=? AND e.key=? "
                "AND f.review_status='reviewed' AND trim(f.zh_tw)<>'' "
                "ORDER BY e.mc_version, e.id LIMIT 24",
                (kind, mod_id, key),
            )
        rows = [row for row in key_rows_cache[cache_key] if int(row[0]) != entry_id]
        translations = {str(row[6]).strip() for row in rows if str(row[6]).strip()}
        conflict = len(translations) > 1
        context: list[dict[str, Any]] = []
        for row in rows:
            same_text = str(row[5]) == en_us
            context.append(
                {
                    "id": f"db:{row[0]}",
                    "reference_type": "same_key_reviewed",
                    "mod_id": str(row[3]),
                    "kind": str(row[1]),
                    "key": str(row[4]),
                    "mc_version": str(row[2]),
                    "en_us": str(row[5]),
                    "zh_tw": str(row[6]),
                    "review_status": str(row[8] or "reviewed"),
                    "source": row[7],
                    "source_label": self.source_catalog.label(row[7]),
                    "match_type": "exact_key_same_text"
                    if same_text
                    else "different_source_text",
                    "confidence": "low" if conflict or not same_text else "high",
                    "limitations": (
                        "conflicting reviewed translations; human decision required"
                        if conflict
                        else "same key but different English source; context only"
                        if not same_text
                        else "same module/key and reviewed local source"
                    ),
                }
            )

        def cached_rows(
            cache_name: str, sql: str, params: tuple[Any, ...]
        ) -> list[tuple]:
            bucket = cache.setdefault(cache_name, {})
            cache_key = params
            if cache_key not in bucket:
                bucket[cache_key] = self._q(sql, params)
            return bucket[cache_key]

        glossary_rows = cached_rows(
            "glossary_rows",
            "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            "f.zh_tw, f.source, f.review_status, f.revision "
            "FROM entry e JOIN translation f ON f.entry_id=e.id "
            "WHERE e.kind='lang' AND e.mod_id=? AND f.review_status='reviewed' "
            "AND e.en_us<>'' AND trim(f.zh_tw)<>'' "
            "ORDER BY CASE WHEN e.mc_version=? THEN 0 ELSE 1 END, e.mc_version, e.id "
            "LIMIT 5000",
            (mod_id, mc_version),
        )
        matched_glossary = [
            row for row in glossary_rows if contains_term(en_us, str(row[5]))
        ]
        by_term: dict[str, list[tuple]] = {}
        for row in matched_glossary:
            by_term.setdefault(str(row[5]).casefold().strip(), []).append(row)
        for term in sorted(by_term, key=lambda value: (-len(value), value))[:12]:
            term_rows = by_term[term]
            term_targets = {str(row[6]).strip() for row in term_rows}
            term_conflict = len(term_targets) != 1
            for row in term_rows[:4]:
                context.append(
                    {
                        "id": f"db:{row[0]}",
                        "reference_type": "reviewed_lang_glossary",
                        "mod_id": str(row[3]),
                        "kind": str(row[1]),
                        "key": str(row[4]),
                        "mc_version": str(row[2]),
                        "en_us": str(row[5]),
                        "zh_tw": str(row[6]),
                        "review_status": str(row[8] or "reviewed"),
                        "source": row[7],
                        "source_label": self.source_catalog.label(row[7]),
                        "match_type": "whole_term_same_mod",
                        "confidence": "low" if term_conflict else "high",
                        "limitations": (
                            "conflicting reviewed glossary terms; not a unique answer"
                            if term_conflict
                            else "same-module reviewed lang term; applies to this text only"
                        ),
                    }
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
                {
                    "id": f"db:{row[0]}",
                    "reference_type": "same_text_reviewed",
                    "mod_id": str(row[3]),
                    "kind": str(row[1]),
                    "key": str(row[4]),
                    "mc_version": str(row[2]),
                    "en_us": str(row[5]),
                    "zh_tw": str(row[6]),
                    "review_status": str(row[8] or "reviewed"),
                    "source": row[7],
                    "source_label": self.source_catalog.label(row[7]),
                    "match_type": "same_text",
                    "confidence": "low",
                    "limitations": "cross-module auxiliary reference; not this module's canonical name",
                }
            )

        official = load_official_locale_index()
        official_terms = matching_terms((en_us,), tuple(official.unique_tw_by_en))
        official_by_source = {
            str(source): target
            for source, target in official.unique_tw_by_en.items()
            if source in official_terms and target and source != target
        }
        for source in sorted(
            official_by_source, key=lambda value: (-len(value), value)
        )[:8]:
            record = next(
                (
                    item
                    for item in official.records_by_key.values()
                    if item.en_us == source and item.zh_tw == official_by_source[source]
                ),
                None,
            )
            if record is None:
                continue
            context.append(
                {
                    "id": f"official:{record.key}",
                    "reference_type": "official_locale",
                    "mod_id": "minecraft",
                    "kind": "lang",
                    "key": record.key,
                    "mc_version": "bundled",
                    "en_us": record.en_us,
                    "zh_tw": record.zh_tw,
                    "review_status": "reviewed",
                    "source": "bundled_official_locale",
                    "match_type": "whole_term",
                    "confidence": "high",
                    "limitations": "bundled local Minecraft glossary; not an external lookup",
                }
            )

        # A repeated DB row can be reachable through same-key and glossary paths;
        # preserve the first, most specific path so evidence ids are unambiguous.
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
        with self._tx() as conn:
            conn.executemany(
                "UPDATE smart_review_candidate SET status='request_failed', validation_error=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                ((error_code, run_id, int(ordinal)) for ordinal in ordinals),
            )

    def mark_smart_review_pending_cancelled(self, run_id: str) -> int:
        """Mark work that was selected but never sent after a cancellation."""
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
        """Persist an editable draft without touching translation/effective/history."""
        draft = _validate_text(
            draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
        ).strip()
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
            if not draft:
                return "invalid_draft"
            current = conn.execute(
                "SELECT e.kind, e.mc_version, e.mod_id, e.key, e.en_us, f.zh_tw, f.source, "
                "CASE WHEN f.review_status IN ('unreviewed', 'reviewed', 'legacy_unknown') "
                f"THEN f.review_status WHEN f.source={SRC_MANUAL} THEN 'legacy_unknown' "
                "WHEN f.source IS NOT NULL THEN 'unreviewed' END, t.revision "
                "FROM entry e JOIN effective f ON f.entry_id=e.id "
                "LEFT JOIN translation t ON t.entry_id=f.entry_id AND t.source=f.source "
                "WHERE f.entry_id=?",
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
        """Return lightweight persisted runs for reopening a review queue."""
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
        """Apply one ready candidate only if its effective snapshot is unchanged."""
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
                ).strip()
                if not proposed:
                    return "invalid_draft"
            current = conn.execute(
                "SELECT e.kind, e.mc_version, e.mod_id, e.key, e.en_us, f.zh_tw, f.source, "
                "CASE WHEN f.review_status IN ('unreviewed', 'reviewed', 'legacy_unknown') "
                f"THEN f.review_status WHEN f.source={SRC_MANUAL} THEN 'legacy_unknown' "
                "WHEN f.source IS NOT NULL THEN 'unreviewed' END, t.revision "
                "FROM entry e JOIN effective f ON f.entry_id=e.id "
                "LEFT JOIN translation t ON t.entry_id=f.entry_id AND t.source=f.source "
                "WHERE f.entry_id=?",
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
        """Keep the current translation and mark one ready candidate rejected."""
        with self._tx(bump_generation_on_noop=False) as conn:
            updated = conn.execute(
                "UPDATE smart_review_candidate SET status='rejected', "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=? AND status='ready'",
                (run_id, int(ordinal)),
            ).rowcount
        return "rejected" if updated else "not_ready"


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
    """Run bounded AI proofreading and persist only candidates, never translations."""
    if not model_name.strip():
        raise ValueError("smart review 必須指定模型")
    if provider == "gemini" and not api_key.strip():
        raise ValueError("Gemini smart review 必須指定 API key")
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
    prompt_tokens = output_tokens = 0
    started = time.monotonic()
    cancelled = False
    failed = False
    try:
        for start in range(0, len(snapshots), batch_size):
            if is_cancelled() or (cancel_check is not None and cancel_check()):
                cancelled = True
                break
            batch = snapshots[start : start + batch_size]
            payload = build_smart_review_payload(batch)
            meta: dict[str, Any] = {}
            try:
                raw = call_gemini_requests(
                    model_name=model_name,
                    system_prompt=SMART_REVIEW_SYSTEM_PROMPT,
                    payload=payload,
                    api_key=api_key,
                    temperature=0.2,
                    max_output_tokens=16_384,
                    meta_out=meta,
                    provider=provider,
                    lm_config=lm_config,
                    response_schema=smart_review_response_schema(
                        [str(item.ordinal) for item in batch]
                    ),
                )
            except TaskCancelled:
                cancelled = True
                break
            except Exception as exc:  # noqa: BLE001 - persist only a credential-free class
                request_errors += 1
                db.mark_smart_review_request_error(
                    run_id,
                    [item.ordinal for item in batch],
                    error_code=f"request_failed:{type(exc).__name__}",
                )
                results = []
            else:
                try:
                    results = parse_smart_review_response(raw, batch)
                except Exception as exc:  # noqa: BLE001 - credential-free validation class
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
                prompt_tokens += int(meta.get("prompt_tokens") or 0)
                output_tokens += int(
                    meta.get("output_tokens") or meta.get("candidates_tokens") or 0
                )
            processed += len(batch)
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
    except TaskCancelled:
        cancelled = True
    except BaseException:
        failed = True
        db.mark_smart_review_pending_cancelled(run_id)
        db.finish_smart_review_run(
            run_id,
            processed_count=processed,
            valid_count=valid,
            invalid_count=invalid,
            request_error_count=request_errors,
            metrics={"duration_seconds": round(time.monotonic() - started, 3)},
            status="failed",
        )
        raise

    if cancelled:
        db.mark_smart_review_pending_cancelled(run_id)

    metrics = {
        "duration_seconds": round(time.monotonic() - started, 3),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "no_change_count": no_change,
        "suggestion_count": suggestions,
        "needs_context_count": needs_context,
        "status": (
            "cancelled"
            if cancelled
            else "failed"
            if failed
            else "partial"
            if request_errors
            else "completed"
        ),
        "token_counts": "provider_reported",
    }
    status = metrics["status"]
    db.finish_smart_review_run(
        run_id,
        processed_count=processed,
        valid_count=valid,
        invalid_count=invalid,
        request_error_count=request_errors,
        metrics=metrics,
        status=str(status),
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
        status=str(status),
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
