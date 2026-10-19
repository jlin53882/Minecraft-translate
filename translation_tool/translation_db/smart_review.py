"""Local AI smart-proofreading candidates for the Mod translation database.

The module deliberately separates three phases:

* snapshots and local context are read from SQLite;
* a model returns strict, machine-validated review candidates;
* a user explicitly applies a candidate through a compare-and-set transaction.

No model response is allowed to write a translation directly.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewRunResult,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.quality import translation_issues
from translation_tool.translation_db.schema import SRC_MANUAL

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
5. 保留 Minecraft § 格式碼、佔位符、Patchouli／指南結構與原文語意。
6. evidence_ids 只能引用該筆 context 中已有的 id；沒有引用時使用空陣列。
7. 只回傳 JSON，不要 Markdown code fence 或額外說明。
"""


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
                    },
                    "required": [
                        "id",
                        "decision",
                        "suggested_zh_tw",
                        "issue_types",
                        "evidence_ids",
                        "confidence",
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
    }
    if set(raw) != required:
        raise ValueError("item 欄位不符合 strict schema")
    if raw["id"] != str(snapshot.ordinal):
        raise ValueError("item id 與批次快照不符")
    decision = raw["decision"]
    if decision not in SMART_REVIEW_DECISIONS:
        raise ValueError("decision 不在允許值內")
    suggestion = raw["suggested_zh_tw"]
    if not isinstance(suggestion, str):
        raise TypeError("suggested_zh_tw 必須是字串")
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
    context_ids = {str(item.get("id")) for item in snapshot.context}
    if not set(evidence_ids) <= context_ids:
        raise ValueError("evidence_ids 引用了不存在的 local context")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TypeError("confidence 必須是數字")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence 必須介於 0 與 1")

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
    }


def parse_smart_review_response(
    raw: str,
    snapshots: Sequence[SmartReviewSnapshot],
) -> list[dict[str, Any]]:
    """Strictly validate one model response against the exact input batch."""
    parsed = _strict_json_object(raw)
    items = parsed["items"]
    expected = {str(item.ordinal): item for item in snapshots}
    actual_ids = [
        item.get("id") if isinstance(item, dict) else None for item in items
    ]
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


class SmartReviewMixin:
    """SQLite operations mixed into :class:`TranslationDB`."""

    def smart_review_candidates(
        self,
        *,
        version: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[SmartReviewSnapshot]:
        """Select a bounded, current-effective unreviewed snapshot with local context."""
        bounded_limit = max(0, min(int(limit), 10_000))
        if bounded_limit == 0:
            return []
        params: list[Any] = []
        effective_review = (
            "CASE WHEN f.review_status IN ('unreviewed', 'reviewed', 'legacy_unknown') "
            f"THEN f.review_status WHEN f.source={SRC_MANUAL} THEN 'legacy_unknown' "
            "WHEN f.source IS NOT NULL THEN 'unreviewed' END"
        )
        where = [
            f"({effective_review}) = 'unreviewed'",
            "f.zh_tw <> ''",
            "e.en_us <> ''",
        ]
        if version:
            where.append("e.mc_version = ?")
            params.append(version)
        rows = self._q(
            "SELECT e.id, e.kind, e.mc_version, e.mod_id, e.key, e.en_us, "
            f"f.zh_tw, f.source, ({effective_review}), t.revision, "
            "tm.zh_tw, tm.checker, tm.review_status, tm.revision "
            "FROM entry e JOIN effective f ON f.entry_id=e.id "
            "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
            f"LEFT JOIN translation tm ON tm.entry_id=e.id AND tm.source={SRC_MANUAL} "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY e.mc_version, e.mod_id, e.kind, e.key, e.id "
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

    def _smart_review_context(
        self,
        entry_id: int,
        kind: str,
        mc_version: str,
        mod_id: str,
        key: str,
        en_us: str,
    ) -> list[dict[str, str]]:
        rows = self._q(
            "SELECT e.id, e.mc_version, f.zh_tw "
            "FROM entry e JOIN effective f ON f.entry_id=e.id "
            "WHERE e.kind=? AND e.mod_id=? AND e.key=? AND e.id<>? "
            "AND f.review_status='reviewed' AND f.zh_tw<>'' "
            "ORDER BY e.mc_version, e.id LIMIT 6",
            (kind, mod_id, key, entry_id),
        )
        context = [
            {
                "id": f"same-key:{row[0]}",
                "kind": "same_key_reviewed",
                "version": str(row[1]),
                "translation": str(row[2]),
            }
            for row in rows
        ]
        if kind == "lang":
            glossary = self.reviewed_lang_glossary(
                mod_id,
                mc_version,
                source_texts=(en_us,),
                limit=12,
            )
            context.extend(
                {
                    "id": f"glossary:{index}",
                    "kind": "reviewed_glossary",
                    "key": str(item["key"]),
                    "source_text": str(item["en_us"]),
                    "translation": str(item["zh_tw"]),
                }
                for index, item in enumerate(glossary)
            )
        return context

    def create_smart_review_run(
        self,
        snapshots: Sequence[SmartReviewSnapshot],
        *,
        provider: str,
        model: str,
        version: str | None,
        requested_limit: int,
        batch_size: int,
    ) -> str:
        run_id = uuid.uuid4().hex
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO smart_review_run "
                "(id, provider, model, scope_version, requested_limit, batch_size, selected_count) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    provider,
                    model,
                    version,
                    int(requested_limit),
                    int(batch_size),
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
                "UPDATE smart_review_candidate SET status='invalid', validation_error=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                ((error_code, run_id, int(ordinal)) for ordinal in ordinals),
            )

    def finish_smart_review_run(
        self,
        run_id: str,
        *,
        processed_count: int,
        valid_count: int,
        invalid_count: int,
        request_error_count: int,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE smart_review_run SET finished_at=CURRENT_TIMESTAMP, status=?, "
                "processed_count=?, valid_count=?, invalid_count=?, request_error_count=?, metrics=? "
                "WHERE id=?",
                (
                    "completed" if not request_error_count else "partial",
                    int(processed_count),
                    int(valid_count),
                    int(invalid_count),
                    int(request_error_count),
                    json.dumps(metrics or {}, ensure_ascii=False),
                    run_id,
                ),
            )

    def get_smart_review_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT id, provider, model, scope_version, requested_limit, batch_size, "
            "started_at, finished_at, status, selected_count, processed_count, valid_count, "
            "invalid_count, request_error_count, metrics FROM smart_review_run WHERE id=?",
            (run_id,),
        )
        if row is None:
            return None
        metrics = json.loads(row[14]) if row[14] else {}
        return {
            "id": row[0],
            "provider": row[1],
            "model": row[2],
            "scope_version": row[3],
            "requested_limit": row[4],
            "batch_size": row[5],
            "started_at": row[6],
            "finished_at": row[7],
            "status": row[8],
            "selected_count": row[9],
            "processed_count": row[10],
            "valid_count": row[11],
            "invalid_count": row[12],
            "request_error_count": row[13],
            "metrics": metrics,
        }

    def list_smart_review_candidates(
        self,
        run_id: str,
        *,
        status: str | None = None,
    ) -> list[SmartReviewCandidate]:
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
            "validation_error, status, applied_batch, applied_at "
            f"FROM smart_review_candidate WHERE {' AND '.join(clauses)} ORDER BY ordinal",
            params,
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
                    validation_error=row[22],
                    status=row[23],
                    applied_batch=row[24],
                    applied_at=row[25],
                )
            )
        return result

    def apply_smart_review_candidate(
        self,
        run_id: str,
        ordinal: int,
        *,
        actor: str = "智慧校對",
    ) -> str:
        """Apply one ready candidate only if its effective snapshot is unchanged."""
        batch = uuid.uuid4().hex
        with self._tx() as conn:
            candidate = conn.execute(
                "SELECT entry_id, kind, mc_version, mod_id, entry_key, en_us, current_zh_tw, "
                "effective_source, review_status, effective_revision, manual_zh_tw, "
                "manual_checker, manual_review_status, manual_revision, suggested_zh_tw, status "
                "FROM smart_review_candidate WHERE run_id=? AND ordinal=?",
                (run_id, int(ordinal)),
            ).fetchone()
            if candidate is None:
                return "missing"
            if candidate[15] != "ready" or not candidate[14]:
                return "not_ready"
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
            if translation_issues(
                candidate[5], candidate[14], kind=candidate[1], repair=False
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
                (candidate[0], SRC_MANUAL, candidate[14]),
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
                    candidate[14],
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
) -> SmartReviewRunResult:
    """Run bounded AI proofreading and persist only candidates, never translations."""
    if not model_name.strip():
        raise ValueError("smart review 必須指定模型")
    if provider == "gemini" and not api_key.strip():
        raise ValueError("Gemini smart review 必須指定 API key")
    snapshots = db.smart_review_candidates(version=version, limit=limit)
    batch_size = max(1, min(int(batch_size), 100))
    run_id = db.create_smart_review_run(
        snapshots,
        provider=provider,
        model=model_name,
        version=version,
        requested_limit=limit,
        batch_size=batch_size,
    )
    processed = valid = invalid = request_errors = 0
    no_change = suggestions = needs_context = 0
    prompt_tokens = output_tokens = 0
    started = time.monotonic()
    for start in range(0, len(snapshots), batch_size):
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
            results = parse_smart_review_response(raw, batch)
        except Exception as exc:  # noqa: BLE001 - persist only a credential-free class
            results = []
            request_errors += 1
            db.mark_smart_review_request_error(
                run_id,
                [item.ordinal for item in batch],
                error_code=f"request_failed:{type(exc).__name__}",
            )
        else:
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
            output_tokens += int(meta.get("candidates_tokens") or 0)
        processed += len(batch)
        if not results:
            invalid += len(batch)
        if progress is not None:
            progress(
                {
                    "run_id": run_id,
                    "processed": processed,
                    "selected": len(snapshots),
                    "valid": valid,
                    "invalid": invalid,
                    "request_errors": request_errors,
                }
            )

    metrics = {
        "duration_seconds": round(time.monotonic() - started, 3),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "no_change_count": no_change,
        "suggestion_count": suggestions,
        "needs_context_count": needs_context,
    }
    db.finish_smart_review_run(
        run_id,
        processed_count=processed,
        valid_count=valid,
        invalid_count=invalid,
        request_error_count=request_errors,
        metrics=metrics,
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
