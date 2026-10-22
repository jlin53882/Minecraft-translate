"""智慧校對候選的套用、拒絕、人工編輯與草稿保存。

模型回應不會直接寫入翻譯：所有寫入都在 compare-and-set 交易內比對候選保存的
生效快照與人工列快照，不一致時把候選標為 stale 並拒絕。成功後在同一交易寫入
未審核的人工譯文與 ``smart_review_apply`` history。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from translation_tool.translation_db.quality import translation_issues
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.smart_review_filters import (
    _effective_review_sql,
    _smart_review_current_snapshot_sql,
)
from translation_tool.translation_db.smart_review_history import (
    SMART_REVIEW_CAS_SNAPSHOT_FIELDS,
    SmartReviewHistoryBatch,
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_status import SmartReviewStatus
from translation_tool.translation_db.smart_review_validation import (
    _MAX_SUGGESTION_LENGTH,
    SMART_REVIEW_MANUAL_EDIT_STATUSES,
    _validate_text,
)

logger = logging.getLogger(__name__)

_STATE_SYNC_ATTEMPTS = 3
_STATE_SYNC_RETRY_SECONDS = 0.05
# 候選 JSON 內保存的 CAS 快照欄位，順序對應 ``_smart_review_current_snapshot_sql``
# 的欄位再接 manual 列的四個欄位。
_SNAPSHOT_FIELDS = SMART_REVIEW_CAS_SNAPSHOT_FIELDS


class SmartReviewApplyMixin:
    """提供給 TranslationDB 使用的智慧校對 CAS 套用與人工審查操作。"""

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
            if candidate.get("status") != SmartReviewStatus.READY:
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
                        status=SmartReviewStatus.STALE,
                        validation_error="snapshot_stale",
                    )
                    return SmartReviewStatus.STALE
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
                    status=SmartReviewStatus.STALE,
                    validation_error="manual_snapshot_stale",
                )
                return SmartReviewStatus.STALE
            if current_manual is not None and current_manual[2] == "reviewed":
                history.update_candidate(
                    run_id,
                    ordinal,
                    status=SmartReviewStatus.STALE,
                    validation_error="manual_reviewed_requires_manual_edit",
                )
                return "manual_reviewed"
            if translation_issues(
                str(candidate.get("en_us") or ""),
                draft,
                kind=str(candidate.get("kind") or "lang"),
                repair=False,
            ):
                return SmartReviewStatus.INVALID_FORMAT
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
            if row[14] != SmartReviewStatus.READY:
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
                return SmartReviewStatus.STALE
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
                return SmartReviewStatus.STALE
            if current_manual is not None and current_manual[2] == "reviewed":
                conn.execute(
                    "UPDATE smart_review_candidate SET status='stale', "
                    "validation_error='manual_reviewed_requires_manual_edit', "
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return "manual_reviewed"
            if translation_issues(row[5], draft, kind=row[1], repair=False):
                return SmartReviewStatus.INVALID_FORMAT
            updated = conn.execute(
                "UPDATE smart_review_candidate SET draft_zh_tw=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=? AND status='ready'",
                (draft, run_id, int(ordinal)),
            ).rowcount
            return "saved" if updated == 1 else "not_ready"

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
            self._reconcile_json_applied_candidates(history, run_id)
            candidate = history.get_candidate(run_id, ordinal)
            if candidate is None:
                return "missing"
            if candidate.get("status") != SmartReviewStatus.READY or not candidate.get(
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
                    status=SmartReviewStatus.INVALID_FORMAT,
                    validation_error="suggested_zh_tw_format_invalid",
                )
                return SmartReviewStatus.INVALID_FORMAT

            result_status, stale_reason = self._cas_write_json_candidate(
                candidate,
                entry_id,
                proposed,
                batch=batch,
                actor=actor,
                run_id=run_id,
                ordinal=ordinal,
            )

            if result_status != SmartReviewStatus.APPLIED:
                history.update_candidate(
                    run_id,
                    ordinal,
                    status=SmartReviewStatus.STALE,
                    validation_error=stale_reason,
                )
                return (
                    "manual_reviewed" if result_status == "manual_reviewed" else "stale"
                )
            try:
                updated = history.update_candidate(
                    run_id,
                    ordinal,
                    draft_zh_tw=proposed
                    if draft_zh_tw is not None
                    else candidate.get("draft_zh_tw"),
                    status=SmartReviewStatus.APPLIED,
                    applied_batch=batch,
                    applied_at=history.timestamp(),
                )
            except Exception:
                # SQLite 已經提交；JSON 是另一個儲存邊界，失敗時回報已套用，
                # 並由下一次讀取依 SQLite audit marker 對帳，而不是讓 UI 誤報
                # 翻譯未寫入或讓使用者重複套用。
                logger.warning(
                    "Smart Review JSON 狀態回寫失敗，將於下次讀取對帳：run=%s ordinal=%s",
                    run_id,
                    ordinal,
                    exc_info=True,
                )
                return SmartReviewStatus.APPLIED
            if not updated:
                logger.warning(
                    "Smart Review JSON 候選不存在，SQLite 已套用：run=%s ordinal=%s",
                    run_id,
                    ordinal,
                )
            return SmartReviewStatus.APPLIED

    def _cas_write_json_candidate(
        self,
        candidate: dict[str, Any],
        entry_id: int,
        proposed: str,
        *,
        batch: str,
        actor: str,
        run_id: str,
        ordinal: int,
    ) -> tuple[str, str | None]:
        """在單一 SQLite 交易內比對候選快照，通過才寫入人工譯文與同文條目。

        回傳（``applied``／``stale``／``manual_reviewed``, stale 原因）。
        """
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
        result_status = SmartReviewStatus.APPLIED
        with self._tx(bump_generation_on_noop=False) as conn:
            # BEGIN IMMEDIATE 必須早於快照讀取，讓 CAS 檢查與正式寫入
            # 位於同一個 SQLite 交易，避免讀取後被其他連線插入修改。
            current = conn.execute(
                _smart_review_current_snapshot_sql(), (entry_id,)
            ).fetchone()
            if current is None or tuple(current) != expected:
                result_status = SmartReviewStatus.STALE
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
                    result_status = SmartReviewStatus.STALE
                    stale_reason = "manual_snapshot_stale"
                elif current_manual is not None and current_manual[2] == "reviewed":
                    result_status = "manual_reviewed"
                    stale_reason = "manual_reviewed_requires_manual_edit"
                else:
                    self._write_smart_review_manual_row(
                        conn,
                        entry_id,
                        proposed,
                        previous_manual=current_manual,
                        old_zh_tw=candidate.get("current_zh_tw"),
                        batch=batch,
                        actor=actor,
                        note=f"run={run_id}; ordinal={ordinal}",
                    )
                    self._apply_smart_review_twins(
                        conn,
                        entry_id,
                        current,
                        proposed,
                        candidate.get("twin_entry_ids") or (),
                        batch=batch,
                        actor=actor,
                        run_id=run_id,
                        ordinal=ordinal,
                    )
        return result_status, stale_reason

    def _apply_smart_review_candidate_history_batch(
        self,
        history: SmartReviewHistoryBatch,
        run_id: str,
        ordinal: int,
        *,
        draft_zh_tw: str | None,
        actor: str,
    ) -> str:
        """在共用歷史批次中套用一筆 JSON 候選。"""
        batch = uuid.uuid4().hex
        candidate = history.get_candidate(ordinal)
        if candidate is None:
            return "missing"
        if candidate.get("status") != SmartReviewStatus.READY or not candidate.get(
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
                ordinal,
                status=SmartReviewStatus.INVALID_FORMAT,
                validation_error="suggested_zh_tw_format_invalid",
            )
            return SmartReviewStatus.INVALID_FORMAT

        result_status, stale_reason = self._cas_write_json_candidate(
            candidate,
            entry_id,
            proposed,
            batch=batch,
            actor=actor,
            run_id=run_id,
            ordinal=ordinal,
        )
        if result_status != SmartReviewStatus.APPLIED:
            history.update_candidate(
                ordinal,
                status=SmartReviewStatus.STALE,
                validation_error=stale_reason,
            )
            return "manual_reviewed" if result_status == "manual_reviewed" else "stale"
        history.update_candidate(
            ordinal,
            draft_zh_tw=proposed
            if draft_zh_tw is not None
            else candidate.get("draft_zh_tw"),
            status=SmartReviewStatus.APPLIED,
            applied_batch=batch,
            applied_at=history.store.timestamp(),
        )
        return SmartReviewStatus.APPLIED

    @staticmethod
    def _reject_smart_review_candidate_history_batch(
        history: SmartReviewHistoryBatch, ordinal: int
    ) -> str:
        """在共用歷史批次中拒絕一筆候選。"""
        candidate = history.get_candidate(ordinal)
        if candidate is None or candidate.get("status") != SmartReviewStatus.READY:
            return "not_ready"
        updated = history.update_candidate(ordinal, status=SmartReviewStatus.REJECTED)
        return SmartReviewStatus.REJECTED if updated else "not_ready"

    def _realign_smart_review_candidate_history_batch(
        self, history: SmartReviewHistoryBatch, run_id: str, ordinal: int
    ) -> str:
        """在共用歷史批次中把候選快照對齊目前資料庫。"""
        candidate = history.get_candidate(ordinal)
        if candidate is None:
            return "missing"
        if candidate.get("status") not in SMART_REVIEW_MANUAL_EDIT_STATUSES:
            return "not_editable"
        entry_id = int(candidate["entry_id"])
        with self._lock:
            current = self._one(_smart_review_current_snapshot_sql(), (entry_id,))
            manual = self._one(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (entry_id, SRC_MANUAL),
            )
        if current is None:
            return "missing_entry"
        manual_values = tuple(manual) if manual else (None, None, None, None)
        updates = dict(zip(_SNAPSHOT_FIELDS, (*current, *manual_values), strict=True))
        if not isinstance(candidate.get("original_snapshot"), dict):
            updates["original_snapshot"] = {
                key: candidate.get(key) for key in _SNAPSHOT_FIELDS
            }
        if all(candidate.get(key) == value for key, value in updates.items()):
            return "unchanged"
        history.update_candidate(ordinal, **updates)
        return "realigned"

    def _apply_smart_review_manual_edit_history_batch(
        self,
        history: SmartReviewHistoryBatch,
        run_id: str,
        ordinal: int,
        proposed: str,
        *,
        actor: str,
    ) -> str:
        """在共用歷史批次中以人工文字覆蓋一筆候選。"""
        proposed = _validate_text(
            proposed, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
        )
        batch = uuid.uuid4().hex
        candidate = history.get_candidate(ordinal)
        if candidate is None:
            return "missing"
        if candidate.get("status") not in SMART_REVIEW_MANUAL_EDIT_STATUSES:
            return "not_editable"
        if not proposed.strip():
            return "invalid_draft"
        entry_id = int(candidate["entry_id"])
        expected = tuple(candidate.get(key) for key in _SNAPSHOT_FIELDS)
        with self._tx(bump_generation_on_noop=False) as conn:
            current = conn.execute(
                _smart_review_current_snapshot_sql(), (entry_id,)
            ).fetchone()
            current_manual = conn.execute(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (entry_id, SRC_MANUAL),
            ).fetchone()
            snapshot = (*(current or ()), *(current_manual or (None,) * 4))
            if current is None or snapshot != expected:
                return "changed"
            if current_manual is not None and current_manual[2] == "reviewed":
                return "manual_reviewed"
            if proposed == current[5]:
                return "unchanged"
            if translation_issues(
                str(current[4] or ""), proposed, kind=str(current[0]), repair=False
            ):
                return SmartReviewStatus.INVALID_FORMAT
            self._write_smart_review_manual_row(
                conn,
                entry_id,
                proposed,
                previous_manual=current_manual,
                old_zh_tw=current[5],
                batch=batch,
                actor=actor,
                note=f"run={run_id}; ordinal={ordinal}",
            )
            self._apply_smart_review_twins(
                conn,
                entry_id,
                current,
                proposed,
                candidate.get("twin_entry_ids") or (),
                batch=batch,
                actor=actor,
                run_id=run_id,
                ordinal=ordinal,
            )
        history.update_candidate(
            ordinal,
            status=SmartReviewStatus.APPLIED,
            draft_zh_tw=proposed,
            validation_error=None,
            applied_batch=batch,
            applied_at=history.store.timestamp(),
        )
        return SmartReviewStatus.APPLIED

    def _override_stale_smart_review_candidate_history_batch(
        self,
        history: SmartReviewHistoryBatch,
        candidate: Mapping[str, Any],
    ) -> tuple[str, str | None]:
        """在共用歷史批次中對齊並覆蓋一筆 stale 候選。"""
        if candidate.get("status") != SmartReviewStatus.STALE:
            return "not_stale", "候選狀態不是已過期"
        proposed = candidate.get("draft_zh_tw") or candidate.get("suggested_zh_tw")
        if not proposed or not str(proposed).strip():
            return "not_ready", "沒有可覆蓋的草稿或 AI 建議"
        run_id = str(candidate.get("run_id") or history.run_id)
        ordinal = int(candidate["ordinal"])
        aligned = self._realign_smart_review_candidate_history_batch(
            history, run_id, ordinal
        )
        if aligned not in {"realigned", "unchanged"}:
            return aligned, "無法更新 JSON 目前譯文快照"
        status = self._apply_smart_review_manual_edit_history_batch(
            history,
            run_id,
            ordinal,
            str(proposed),
            actor="智慧校對",
        )
        if status == "changed":
            return SmartReviewStatus.STALE, "對齊後資料庫再次變更"
        if status == "manual_reviewed":
            return "manual_reviewed", "目前譯文已人工審核，不進行批量覆蓋"
        return str(status), None

    def apply_smart_review_candidate_batch(
        self,
        run_id: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        action: str = "apply",
        progress_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, int, tuple[int, str, str | None]], None] | None = None,
        actor: str = "智慧校對",
    ) -> tuple[tuple[tuple[int, str, str | None], ...], bool] | None:
        """在同一 JSON 歷史鎖內處理一個 run 的多筆候選。

        回傳 ``None`` 代表該 run 是舊版 SQLite-only 批次，呼叫端應回退到
        原本的逐筆流程；其餘回傳（ordinal、狀態、錯誤）與是否取消。每筆
        SQLite 寫入仍維持獨立 CAS 交易，但 JSON 分片只在最後各寫一次。
        """
        if action not in {"apply", "reject", "override"}:
            raise ValueError(f"未知的智慧校對批量動作：{action}")
        history = SmartReviewHistoryStore(self.path)
        if not history.exists(run_id):
            return None
        results: list[tuple[int, str, str | None]] = []
        cancelled = False
        with history.batch(run_id) as history_batch:
            records = self._smart_review_apply_records(run_id)
            if records:
                history_batch.reconcile_applied(records)
            for item in candidates:
                if progress_cancel is not None and progress_cancel():
                    cancelled = True
                    break
                ordinal = int(item["ordinal"])
                try:
                    if action == "apply":
                        status = self._apply_smart_review_candidate_history_batch(
                            history_batch,
                            run_id,
                            ordinal,
                            draft_zh_tw=item.get("draft_zh_tw"),
                            actor=actor,
                        )
                        error = None
                    elif action == "reject":
                        status = self._reject_smart_review_candidate_history_batch(
                            history_batch, ordinal
                        )
                        error = None
                    else:
                        status, error = (
                            self._override_stale_smart_review_candidate_history_batch(
                                history_batch, item
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - preserve partial batch success
                    status = "error"
                    error = str(exc) or type(exc).__name__
                result = (ordinal, str(status), error)
                results.append(result)
                if progress is not None:
                    progress(len(results), len(candidates), result)
        if not cancelled and progress_cancel is not None:
            cancelled = bool(progress_cancel())
        return tuple(results), cancelled

    def _write_smart_review_manual_row(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        proposed: str,
        *,
        previous_manual: Sequence[Any] | None,
        old_zh_tw: str | None,
        batch: str,
        actor: str,
        note: str,
    ) -> None:
        """在呼叫端的交易內寫入未審核人工譯文與 ``smart_review_apply`` 歷史。"""
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
                old_zh_tw,
                proposed,
                previous_manual[0] if previous_manual else None,
                previous_manual[1] if previous_manual else None,
                previous_manual[2] if previous_manual else None,
                "",
                "unreviewed",
                previous_manual[3] if previous_manual else None,
                new_revision,
                SRC_MANUAL,
                note,
            ),
        )
        self._refresh(conn, [entry_id])

    def _apply_smart_review_twins(
        self,
        conn: sqlite3.Connection,
        entry_id: int,
        current: Sequence[Any],
        proposed: str,
        candidate_twin_ids: Sequence[Any],
        *,
        batch: str,
        actor: str,
        run_id: str,
        ordinal: int,
    ) -> int:
        """把代表條目的譯文一併寫給同文且目前狀態相同的條目，回傳寫入筆數。

        只處理候選記錄的 ``twin_entry_ids``（選取時被去重掉的條目），並再確認它們
        仍與代表條目套用前同版本、模組、種類、英文、目前譯文、生效來源與審核狀態；
        已人工審核的人工列不覆蓋。
        在呼叫端的交易內執行，每筆各自留下 ``smart_review_apply`` history。
        """
        kind, mc_version, mod_id, _key, en_us, zh_tw, source, status = current[:8]
        twin_ids = sorted(
            int(row[0])
            for row in conn.execute(
                "SELECT e.id FROM entry e JOIN effective f ON f.entry_id=e.id "
                "WHERE e.mc_version=? AND e.mod_id=? AND e.kind=? AND e.en_us=? "
                "AND e.id!=? AND f.zh_tw=? AND f.source IS ? AND ("
                + _effective_review_sql()
                + ") IS ? AND e.id IN (SELECT value FROM json_each(?))",
                (
                    mc_version,
                    mod_id,
                    kind,
                    en_us,
                    entry_id,
                    zh_tw,
                    source,
                    status,
                    json.dumps([int(value) for value in candidate_twin_ids]),
                ),
            )
        )
        written = 0
        for twin_id in twin_ids:
            twin_manual = conn.execute(
                "SELECT zh_tw, checker, review_status, revision FROM translation "
                "WHERE entry_id=? AND source=?",
                (twin_id, SRC_MANUAL),
            ).fetchone()
            if twin_manual is not None and twin_manual[2] == "reviewed":
                continue
            self._write_smart_review_manual_row(
                conn,
                twin_id,
                proposed,
                previous_manual=twin_manual,
                old_zh_tw=zh_tw,
                batch=batch,
                actor=actor,
                note=f"run={run_id}; twin_of_ordinal={ordinal}",
            )
            written += 1
        return written

    def realign_smart_review_candidate(self, run_id: str, ordinal: int) -> str:
        """把可人工編輯候選的快照對齊目前資料庫，讓審查者看到並編輯的是現況。

        只更新快照欄位，不改狀態、草稿與正式翻譯。回傳 ``realigned``、
        ``unchanged``、``missing``、``missing_entry``、``not_editable`` 或
        ``unsupported``（舊版 SQLite 批次不支援）。
        """
        history = SmartReviewHistoryStore(self.path)
        if not history.exists(run_id):
            return "unsupported"
        with history.locked():
            candidate = history.get_candidate(run_id, ordinal)
            if candidate is None:
                return "missing"
            if candidate.get("status") not in SMART_REVIEW_MANUAL_EDIT_STATUSES:
                return "not_editable"
            entry_id = int(candidate["entry_id"])
            with self._lock:
                current = self._one(_smart_review_current_snapshot_sql(), (entry_id,))
                manual = self._one(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (entry_id, SRC_MANUAL),
                )
            if current is None:
                return "missing_entry"
            manual_values = tuple(manual) if manual else (None, None, None, None)
            updates = dict(
                zip(_SNAPSHOT_FIELDS, (*current, *manual_values), strict=True)
            )
            if not isinstance(candidate.get("original_snapshot"), dict):
                updates["original_snapshot"] = {
                    key: candidate.get(key) for key in _SNAPSHOT_FIELDS
                }
            if all(candidate.get(key) == value for key, value in updates.items()):
                return "unchanged"
            history.update_candidate(run_id, ordinal, **updates)
            return "realigned"

    def apply_smart_review_manual_edit(
        self,
        run_id: str,
        ordinal: int,
        draft_zh_tw: str,
        *,
        actor: str = "智慧校對",
    ) -> str:
        """以人工編輯的譯文覆蓋套用候選，任何可編輯狀態（含已套用）都適用。

        與 ``apply_smart_review_candidate`` 共用 CAS 與格式驗證，但不要求候選是
        ``ready``，也不因快照變動把候選降成 ``stale``：呼叫端應先
        ``realign_smart_review_candidate``，拒絕時只回傳狀態並保留原候選。
        成功後候選一律標為 ``applied``。
        """
        history = SmartReviewHistoryStore(self.path)
        if not history.exists(run_id):
            return "unsupported"
        proposed = _validate_text(
            draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
        )
        batch = uuid.uuid4().hex
        with history.locked():
            candidate = history.get_candidate(run_id, ordinal)
            if candidate is None:
                return "missing"
            if candidate.get("status") not in SMART_REVIEW_MANUAL_EDIT_STATUSES:
                return "not_editable"
            if not proposed.strip():
                return "invalid_draft"
            entry_id = int(candidate["entry_id"])
            expected = tuple(candidate.get(key) for key in _SNAPSHOT_FIELDS)
            with self._tx(bump_generation_on_noop=False) as conn:
                current = conn.execute(
                    _smart_review_current_snapshot_sql(), (entry_id,)
                ).fetchone()
                current_manual = conn.execute(
                    "SELECT zh_tw, checker, review_status, revision FROM translation "
                    "WHERE entry_id=? AND source=?",
                    (entry_id, SRC_MANUAL),
                ).fetchone()
                snapshot = (*(current or ()), *(current_manual or (None,) * 4))
                if current is None or snapshot != expected:
                    return "changed"
                if current_manual is not None and current_manual[2] == "reviewed":
                    return "manual_reviewed"
                if proposed == current[5]:
                    return "unchanged"
                # 格式以交易內讀到的原文檢查：資料若已變動，上面會先回報 changed。
                if translation_issues(
                    str(current[4] or ""), proposed, kind=str(current[0]), repair=False
                ):
                    return SmartReviewStatus.INVALID_FORMAT
                self._write_smart_review_manual_row(
                    conn,
                    entry_id,
                    proposed,
                    previous_manual=current_manual,
                    old_zh_tw=current[5],
                    batch=batch,
                    actor=actor,
                    note=f"run={run_id}; ordinal={ordinal}",
                )
                self._apply_smart_review_twins(
                    conn,
                    entry_id,
                    current,
                    proposed,
                    candidate.get("twin_entry_ids") or (),
                    batch=batch,
                    actor=actor,
                    run_id=run_id,
                    ordinal=ordinal,
                )
            return self._mark_candidate_applied(
                history, run_id, ordinal, proposed=proposed, batch=batch
            )

    def _mark_candidate_applied(
        self,
        history: SmartReviewHistoryStore,
        run_id: str,
        ordinal: int,
        *,
        proposed: str,
        batch: str,
    ) -> str:
        """把已提交到 SQLite 的人工編輯回寫成 JSON 候選的 applied 狀態。

        短暫的檔案鎖（Windows 的 ``os.replace``）重試幾次即可；仍失敗時回傳
        ``state_sync_failed``：譯文與 history 已提交（不回滾），下一次讀取批次時由
        ``_reconcile_json_applied_candidates`` 依 ``applied_batch`` 對帳補回狀態。
        """
        for attempt in range(_STATE_SYNC_ATTEMPTS):
            try:
                history.update_candidate(
                    run_id,
                    ordinal,
                    status=SmartReviewStatus.APPLIED,
                    draft_zh_tw=proposed,
                    validation_error=None,
                    applied_batch=batch,
                    applied_at=history.timestamp(),
                )
                return SmartReviewStatus.APPLIED
            except Exception:
                logger.warning(
                    "Smart Review 人工編輯狀態回寫失敗（第 %d 次）：run=%s ordinal=%s",
                    attempt + 1,
                    run_id,
                    ordinal,
                    exc_info=True,
                )
                time.sleep(_STATE_SYNC_RETRY_SECONDS)
        return "state_sync_failed"

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
            if candidate[15] != SmartReviewStatus.READY or not candidate[14]:
                return "not_ready"
            proposed = candidate[16] or candidate[14]
            if draft_zh_tw is not None:
                proposed = _validate_text(
                    draft_zh_tw, "draft_zh_tw", max_length=_MAX_SUGGESTION_LENGTH
                )
                if not proposed.strip():
                    return "invalid_draft"
            rejection, current_manual = self._check_legacy_candidate_snapshot(
                conn, run_id, ordinal, candidate
            )
            if rejection is not None:
                return rejection
            if translation_issues(
                candidate[5], proposed, kind=candidate[1], repair=False
            ):
                conn.execute(
                    "UPDATE smart_review_candidate SET status='invalid_format', "
                    "validation_error='suggested_zh_tw_format_invalid', updated_at=CURRENT_TIMESTAMP "
                    "WHERE run_id=? AND ordinal=?",
                    (run_id, int(ordinal)),
                )
                return SmartReviewStatus.INVALID_FORMAT
            self._write_legacy_candidate_apply(
                conn,
                run_id,
                ordinal,
                candidate,
                proposed,
                current_manual,
                batch=batch,
                actor=actor,
                save_draft=draft_zh_tw is not None,
            )
        return SmartReviewStatus.APPLIED

    def _check_legacy_candidate_snapshot(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        ordinal: int,
        candidate: Sequence[Any],
    ) -> tuple[str | None, Sequence[Any] | None]:
        """比對舊版 SQLite 候選保存的快照與目前資料，回傳（拒絕代碼, 目前人工列）。

        快照不一致或人工列已審核時，把候選標為 stale 並回傳拒絕代碼；通過時
        拒絕代碼為 ``None``。
        """
        current = conn.execute(
            _smart_review_current_snapshot_sql(),
            (candidate[0],),
        ).fetchone()
        if current is None or tuple(current[:9]) != tuple(candidate[1:10]):
            conn.execute(
                "UPDATE smart_review_candidate SET status='stale', "
                "validation_error='snapshot_stale', updated_at=CURRENT_TIMESTAMP "
                "WHERE run_id=? AND ordinal=?",
                (run_id, int(ordinal)),
            )
            return SmartReviewStatus.STALE, None
        current_manual = conn.execute(
            "SELECT zh_tw, checker, review_status, revision FROM translation "
            "WHERE entry_id=? AND source=?",
            (candidate[0], SRC_MANUAL),
        ).fetchone()
        manual_snapshot = tuple(candidate[10:14])
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
            return SmartReviewStatus.STALE, current_manual
        if current_manual is not None and current_manual[2] == "reviewed":
            conn.execute(
                "UPDATE smart_review_candidate SET status='stale', "
                "validation_error='manual_reviewed_requires_manual_edit', "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=?",
                (run_id, int(ordinal)),
            )
            return "manual_reviewed", current_manual
        return None, current_manual

    def _write_legacy_candidate_apply(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        ordinal: int,
        candidate: Sequence[Any],
        proposed: str,
        previous_manual: Sequence[Any] | None,
        *,
        batch: str,
        actor: str,
        save_draft: bool,
    ) -> None:
        """在舊版 SQLite 候選的交易內寫入人工譯文、history 並標記候選已套用。"""
        conn.execute(
            "INSERT INTO translation (entry_id, source, zh_tw, checker, review_status) "
            "VALUES (?,?,?,'','unreviewed') ON CONFLICT(entry_id, source) DO UPDATE SET "
            "zh_tw=excluded.zh_tw, checker='', review_status='unreviewed', "
            "updated_at=CURRENT_TIMESTAMP",
            (candidate[0], SRC_MANUAL, proposed),
        )
        if save_draft:
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

    def reject_smart_review_candidate(self, run_id: str, ordinal: int) -> str:
        """保留目前翻譯並將智慧校對候選標記為拒絕。"""
        history = SmartReviewHistoryStore(self.path)
        if history.exists(run_id):
            self._reconcile_json_applied_candidates(history, run_id)
            # 讀取與狀態轉移必須在同一個檔案鎖內，避免 reject 讀到 ready
            # 後，apply 先完成並把同一候選改成 applied，最後又被覆寫成 rejected。
            with history.locked():
                candidate = history.get_candidate(run_id, ordinal)
                if (
                    candidate is None
                    or candidate.get("status") != SmartReviewStatus.READY
                ):
                    return "not_ready"
                updated = history.update_candidate(
                    run_id, ordinal, status=SmartReviewStatus.REJECTED
                )
                return SmartReviewStatus.REJECTED if updated else "not_ready"
        with self._tx(bump_generation_on_noop=False) as conn:
            updated = conn.execute(
                "UPDATE smart_review_candidate SET status='rejected', "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND ordinal=? AND status='ready'",
                (run_id, int(ordinal)),
            ).rowcount
        return SmartReviewStatus.REJECTED if updated else "not_ready"
