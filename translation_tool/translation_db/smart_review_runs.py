"""智慧校對批次（run）的生命週期、候選列表與統計。

新批次保存在 JSON（``SmartReviewHistoryStore``），舊版批次仍讀寫 SQLite；
每個方法先判斷批次所在的儲存，再走對應路徑。JSON 與 SQLite 套用紀錄的對帳
見 ``smart_review_projection``。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from translation_tool.translation_db.models import (
    SmartReviewCandidate,
    SmartReviewSnapshot,
)
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.smart_review_filters import (
    _smart_review_current_snapshot_sql,
)
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_status import SmartReviewStatus
from translation_tool.translation_db.smart_review_validation import _candidate_status


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


def _resume_scope_matches(current: tuple[Any, ...], scope: dict[str, Any]) -> bool:
    """確認目前快照仍在原批次的版本、來源與搜尋範圍。"""
    if scope.get("version") and current[1] != scope.get("version"):
        return False
    if scope.get("mod_id") and current[2] != scope.get("mod_id"):
        return False
    if scope.get("kind") and current[0] != scope.get("kind"):
        return False
    expected_source = scope.get("effective_source")
    if expected_source is not None and current[6] != expected_source:
        return False
    expected_review = scope.get("review_status")
    if (
        expected_review in {"unreviewed", "reviewed", "legacy_unknown"}
        and current[7] != expected_review
    ):
        return False
    needle = str(scope.get("query") or "").strip().casefold()
    return not needle or any(
        needle in str(current[index] or "").casefold() for index in (3, 4, 5)
    )


_RESUME_CAS_FIELDS = (
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

_RESUME_RETRYABLE_STATUSES = frozenset(
    {
        SmartReviewStatus.PENDING,
        SmartReviewStatus.CANCELLED,
        SmartReviewStatus.REQUEST_FAILED,
    }
)
_RESUME_CONTEXT_REFERENCE_RE = re.compile(
    r"^(?:db|module-locale):(?P<entry_id>\d+):source:(?P<source>-?\d+)"
    r":revision:(?P<revision>\d+)$"
)
_RESUME_CONTEXT_QUERY_CHUNK = 400


def _collect_resume_context_references(
    candidates: Sequence[SmartReviewCandidate],
) -> tuple[
    dict[int, list[tuple[int, int, int, Mapping[str, Any]]]],
    set[tuple[int, int]],
    set[int],
]:
    """收集待重送候選的 context reference 與無法驗證的序號。"""
    references_by_ordinal: dict[int, list[tuple[int, int, int, Mapping[str, Any]]]] = {}
    lookup_keys: set[tuple[int, int]] = set()
    stale: set[int] = set()
    for candidate in candidates:
        if str(candidate.status) not in _RESUME_RETRYABLE_STATUSES:
            continue
        for item in candidate.context:
            if not isinstance(item, Mapping):
                continue
            context_id = str(item.get("id") or "")
            if not context_id.startswith(("db:", "module-locale:")):
                continue
            match = _RESUME_CONTEXT_REFERENCE_RE.fullmatch(context_id)
            if match is None:
                # 舊格式沒有可驗證的 revision；安全起見只刷新這筆候選。
                stale.add(candidate.ordinal)
                continue
            entry_id = int(match.group("entry_id"))
            source = int(match.group("source"))
            revision = int(match.group("revision"))
            references_by_ordinal.setdefault(candidate.ordinal, []).append(
                (entry_id, source, revision, item)
            )
            lookup_keys.add((entry_id, source))
    return references_by_ordinal, lookup_keys, stale


def _resume_context_reference_is_stale(
    reference: tuple[int, int, int, Mapping[str, Any]],
    current: tuple[Any, ...] | None,
) -> bool:
    """比較一筆 context reference 的 revision、文字與審核狀態。"""
    if current is None:
        return True
    _entry_id, _source, revision, item = reference
    context_id = str(item.get("id") or "")
    default_status = (
        "unreviewed" if context_id.startswith("module-locale:") else "reviewed"
    )
    expected_status = str(item.get("review_status") or default_status)
    current_status = str(current[4] or default_status)
    return (
        int(current[2] or -1) != revision
        or str(current[3]) != str(item.get("zh_tw") or "")
        or current_status != expected_status
    )


def _resume_cas_values(
    value: Mapping[str, Any] | SmartReviewCandidate,
) -> tuple[Any, ...]:
    """取續跑 CAS 欄位；context 另由歷史快照沿用或在必要時重建。"""
    if isinstance(value, Mapping):
        return tuple(
            value.get("entry_key") if key == "entry_key" else value.get(key)
            for key in _RESUME_CAS_FIELDS
        )
    return tuple(
        getattr(value, "key" if key == "entry_key" else key, None)
        for key in _RESUME_CAS_FIELDS
    )


def _resume_snapshot(run_id: str, item: SmartReviewCandidate) -> SmartReviewSnapshot:
    """把歷史候選轉成續跑所需的不可變 snapshot。"""
    return SmartReviewSnapshot(
        run_id=run_id,
        ordinal=item.ordinal,
        entry_id=item.entry_id,
        kind=item.kind,
        mc_version=item.mc_version,
        mod_id=item.mod_id,
        key=item.key,
        en_us=item.en_us,
        current_zh_tw=item.current_zh_tw,
        effective_source=item.effective_source,
        review_status=item.review_status,
        effective_revision=item.effective_revision,
        context=item.context,
        manual_zh_tw=item.manual_zh_tw,
        manual_checker=item.manual_checker,
        manual_review_status=item.manual_review_status,
        manual_revision=item.manual_revision,
    )


def _resume_counts(items: Sequence[SmartReviewCandidate]) -> dict[str, int]:
    """由恢復後候選重建既有 AI 結果計數。"""
    counts = {
        "processed": 0,
        "valid": 0,
        "invalid": 0,
        "request_errors": 0,
        "no_change": 0,
        "suggestions": 0,
        "needs_context": 0,
    }
    for item in items:
        ai_status = item.ai_status or item.status
        if item.status in {SmartReviewStatus.APPLIED, SmartReviewStatus.REJECTED}:
            ai_status = item.ai_status or SmartReviewStatus.READY
        if ai_status == SmartReviewStatus.REQUEST_FAILED:
            counts["request_errors"] += 1
            continue
        if ai_status not in {
            SmartReviewStatus.READY,
            SmartReviewStatus.NO_CHANGE,
            SmartReviewStatus.NEEDS_CONTEXT,
            SmartReviewStatus.INVALID,
        }:
            continue
        counts["processed"] += 1
        if ai_status == SmartReviewStatus.INVALID:
            counts["invalid"] += 1
        else:
            counts["valid"] += 1
        if ai_status == SmartReviewStatus.NO_CHANGE:
            counts["no_change"] += 1
        elif ai_status == SmartReviewStatus.NEEDS_CONTEXT:
            counts["needs_context"] += 1
        elif ai_status == SmartReviewStatus.READY:
            counts["suggestions"] += 1
    return counts


class SmartReviewRunStoreMixin:
    """提供給 TranslationDB 使用的智慧校對批次與候選存取。"""

    def _smart_review_has_column(self, table: str, column: str) -> bool:
        """確認智慧校對資料表具有指定欄位。"""
        return any(row[1] == column for row in self._q(f"PRAGMA table_info({table})"))

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

    def _resume_current_payload(
        self,
        entry_id: int,
        scope: dict[str, Any],
        *,
        include_context: bool = True,
    ) -> dict[str, Any] | None:
        """讀取一筆目前快照；可略過昂貴 context 建立供續跑快速比對。"""
        current = self._one(_smart_review_current_snapshot_sql(), (int(entry_id),))
        if current is None or not _resume_scope_matches(current, scope):
            return None
        manual = self._one(
            "SELECT zh_tw, checker, review_status, revision FROM translation "
            "WHERE entry_id=? AND source=?",
            (int(entry_id), SRC_MANUAL),
        )
        return {
            "kind": current[0],
            "mc_version": current[1],
            "mod_id": current[2],
            "entry_key": current[3],
            "en_us": current[4],
            "current_zh_tw": current[5],
            "effective_source": current[6],
            "review_status": current[7],
            "effective_revision": current[8],
            "manual_zh_tw": None if manual is None else manual[0],
            "manual_checker": None if manual is None else manual[1],
            "manual_review_status": None if manual is None else manual[2],
            "manual_revision": None if manual is None else manual[3],
            "context": (
                self._smart_review_context(
                    int(entry_id),
                    str(current[0]),
                    str(current[1]),
                    str(current[2]),
                    str(current[3]),
                    str(current[4]),
                )
                if include_context
                else ()
            ),
        }

    def _stale_resume_context_ordinals(
        self, candidates: Sequence[SmartReviewCandidate]
    ) -> set[int]:
        """找出待重送候選中已變動的 DB context reference。

        context evidence 的 id 已包含 ``entry_id``、``source`` 與 translation
        revision。先批次驗證這些輕量 reference，只有真的變動的候選才會重建完整
        local context；未受影響的候選仍沿用 JSON 快照，避免續跑時重新查詢數萬筆。
        """
        references_by_ordinal, lookup_keys, stale = _collect_resume_context_references(
            candidates
        )
        if not lookup_keys:
            return stale

        current_by_key: dict[tuple[int, int], tuple[Any, ...]] = {}
        keys = sorted(lookup_keys)
        for start in range(0, len(keys), _RESUME_CONTEXT_QUERY_CHUNK):
            chunk = keys[start : start + _RESUME_CONTEXT_QUERY_CHUNK]
            where = " OR ".join("(entry_id=? AND source=?)" for _ in chunk)
            params = tuple(value for key in chunk for value in key)
            rows = self._q(
                "SELECT entry_id, source, revision, zh_tw, review_status "
                f"FROM translation WHERE {where}",
                params,
            )
            current_by_key.update({(int(row[0]), int(row[1])): row for row in rows})

        for ordinal, references in references_by_ordinal.items():
            if any(
                _resume_context_reference_is_stale(
                    reference,
                    current_by_key.get((reference[0], reference[1])),
                )
                for reference in references
            ):
                stale.add(ordinal)
        return stale

    def prepare_smart_review_resume(
        self,
        run_id: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[list[SmartReviewSnapshot], dict[str, int], dict[str, Any]]:
        """以同一 run_id 準備恢復，保留原範圍並只回傳仍需送出的候選。"""
        history = SmartReviewHistoryStore(self.path)
        if not history.exists(run_id):
            raise ValueError("只有 JSON 歷史批次支援同一 run 復原")
        run = history.get_run(run_id)
        if run is None:
            raise ValueError("找不到智慧校對歷史批次")
        if str(run.get("status") or "") == "running":
            raise RuntimeError("智慧校對批次仍在執行中，不能重複啟動")
        candidates = history.list_candidates(run_id)
        if progress is not None:
            progress(
                {
                    "run_id": run_id,
                    "phase": "resume_history_loaded",
                    "status": "running",
                    "selected": int(run.get("selected_count") or len(candidates)),
                    "message": (
                        f"已讀取續跑歷史 {len(candidates):,} 筆，正在驗證最新 context…"
                    ),
                }
            )
        scope = run.get("scope") if isinstance(run.get("scope"), dict) else {}
        stale_context_ordinals = self._stale_resume_context_ordinals(candidates)
        if progress is not None:
            progress(
                {
                    "run_id": run_id,
                    "phase": "resume_context_checked",
                    "status": "running",
                    "selected": int(run.get("selected_count") or len(candidates)),
                    "message": (
                        f"context 變更檢查完成：{len(stale_context_ordinals):,} 筆需要重建，"
                        "正在整理續跑狀態…"
                    ),
                }
            )
        context_cache_reset = False
        current_by_ordinal = {}
        for candidate in candidates:
            current = self._resume_current_payload(
                candidate.entry_id,
                scope,
                include_context=False,
            )
            if current is not None:
                cas_changed = _resume_cas_values(candidate) != _resume_cas_values(
                    current
                )
                if cas_changed or candidate.ordinal in stale_context_ordinals:
                    # CAS 或待重送候選引用的 DB context 變動時，才重建完整 context。
                    if not context_cache_reset:
                        # 續跑可能跨越另一個連線的資料庫寫入；不能沿用建立原始
                        # snapshot 時留下的 query cache。
                        self._smart_review_context_cache = {}
                        context_cache_reset = True
                    current = self._resume_current_payload(
                        candidate.entry_id,
                        scope,
                        include_context=True,
                    )
                else:
                    # 未變更的候選直接沿用 JSON 內的 context，避免續跑時為
                    # 數萬筆候選重做相同的資料庫證據查詢。
                    current["context"] = list(candidate.context)
            current_by_ordinal[candidate.ordinal] = current
        resume_stats = history.prepare_resume(run_id, current_by_ordinal)
        if progress is not None:
            progress(
                {
                    "run_id": run_id,
                    "phase": "resume_projection_saved",
                    "status": "running",
                    "selected": int(run.get("selected_count") or len(candidates)),
                    "message": "續跑狀態已更新，正在重建待處理清單…",
                }
            )
        run_metrics = run.get("metrics")
        if not isinstance(run_metrics, dict):
            run_metrics = {}
        run_metrics = dict(run_metrics)
        run_metrics["excluded_count"] = int(
            run_metrics.get("excluded_count") or 0
        ) + int(resume_stats.get("excluded") or 0)
        run_metrics["invalidated_by_manual_edit_count"] = int(
            run_metrics.get("invalidated_by_manual_edit_count") or 0
        ) + int(resume_stats.get("invalidated") or 0)
        run["metrics"] = run_metrics
        refreshed = history.list_candidates(run_id)
        snapshots = [
            _resume_snapshot(run_id, item)
            for item in refreshed
            if item.status == SmartReviewStatus.PENDING
        ]
        base = _resume_counts(refreshed)
        if progress is not None:
            progress(
                {
                    "run_id": run_id,
                    "phase": "resume_candidates_ready",
                    "status": "running",
                    "selected": int(run.get("selected_count") or len(refreshed)),
                    "processed": base["processed"],
                    "valid": base["valid"],
                    "invalid": base["invalid"],
                    "request_errors": base["request_errors"],
                    "resume_baseline_processed": base["processed"],
                    "resume_pending_total": len(snapshots),
                    "message": (
                        f"續跑候選準備完成：待處理 {len(snapshots):,} 筆，"
                        "正在建立執行批次…"
                    ),
                }
            )
        return snapshots, base, run

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
        error_code: str = SmartReviewStatus.REQUEST_FAILED,
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
        if status not in {
            None,
            "completed",
            "partial",
            SmartReviewStatus.CANCELLED,
            "failed",
            "interrupted",
        }:
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
            self._reconcile_json_applied_candidates(history, run_id)
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

    def load_smart_review_candidate_page(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        known_total: int | None = None,
    ) -> tuple[int, list[SmartReviewCandidate]]:
        """以同一個唯讀資料來源取得候選總數與目前頁面。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            self._reconcile_json_applied_candidates(history, run_id)
            return history.page_candidates(
                run_id,
                status=status,
                limit=limit,
                offset=offset,
                known_total=known_total,
                clamp_offset=True,
            )
        total = (
            self.count_smart_review_candidates_for_run(run_id, status=status)
            if known_total is None
            else max(0, int(known_total))
        )
        if not total:
            return 0, []
        rows = self.list_smart_review_candidates(
            run_id,
            status=status,
            limit=limit,
            offset=min(max(0, int(offset)), total - 1),
        )
        return total, rows

    def count_smart_review_candidates_for_run(
        self, run_id: str, *, status: str | None = None
    ) -> int:
        """計算指定智慧校對執行中的候選資料數量。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            self._reconcile_json_applied_candidates(history, run_id)
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
            self._reconcile_json_applied_candidates(history, run_id)
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

    def list_smart_review_runs(
        self, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """列出 JSON 批次與舊版 SQLite 批次，依時間合併分頁。"""
        start = max(0, int(offset))
        bounded_limit = max(1, int(limit))
        has_scope = self._smart_review_has_column("smart_review_run", "scope_json")
        scope_column = "scope_json" if has_scope else "NULL AS scope_json"
        # SQLite 舊批次必須先抓取足以填滿合併後頁面的前置資料；不能先套用
        # OFFSET，否則與 JSON 批次合併排序後會被再次偏移而漏資料。
        legacy_limit = max(1, start + bounded_limit + 1)
        history_runs = SmartReviewHistoryStore(self.path).list_runs(
            limit=legacy_limit,
            include_sort_metadata=True,
        )
        rows = self._q(
            "SELECT id, provider, model, scope_version, "
            + scope_column
            + ", started_at, "
            "finished_at, status, selected_count, processed_count, valid_count, "
            "invalid_count, request_error_count, metrics "
            "FROM smart_review_run ORDER BY started_at DESC, id DESC LIMIT ?",
            (legacy_limit,),
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
                int(item.get("_history_mtime_ns") or 0),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        page = ordered[start : start + bounded_limit]
        for item in page:
            item.pop("_history_mtime_ns", None)
        return page
