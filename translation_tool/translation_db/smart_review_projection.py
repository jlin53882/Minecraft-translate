"""智慧校對 JSON 候選與 SQLite 套用紀錄的對帳（reconcile）。

套用候選時 SQLite 先提交、JSON 後回寫；兩個儲存之間的失敗以 ``history`` 表
中尚未完成投影的 ``smart_review_apply`` audit marker 為準，於下一次讀取時補回
JSON 狀態。pending marker 在 JSON 確認前不受一般 history 五筆保留政策刪除。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from translation_tool.translation_db.models import (
    SMART_REVIEW_FILTER_GROUPS,
    SmartReviewMark,
)
from translation_tool.translation_db.smart_review_history import (
    SmartReviewHistoryStore,
)
from translation_tool.translation_db.smart_review_status import (
    ProjectionState,
    SmartReviewStatus,
)
from translation_tool.translation_db.smart_review_validation import (
    SMART_REVIEW_MANUAL_EDIT_STATUSES,
)

logger = logging.getLogger(__name__)

# (資料庫路徑, run_id) -> 上次對帳時 history 的最大 id
_RECONCILED_HISTORY_HEAD: dict[tuple[str, str], int] = {}


def clear_smart_review_caches(db_path: str | None = None) -> None:
    """清除對帳進度快取（只影響效能，下一次會重新對帳）；指定 ``db_path`` 時只清該資料庫。"""
    for key in [key for key in _RECONCILED_HISTORY_HEAD if db_path in (None, key[0])]:
        del _RECONCILED_HISTORY_HEAD[key]


class SmartReviewProjectionMixin:
    """提供給 TranslationDB 使用的智慧校對 JSON／SQLite 對帳與標籤投影。"""

    def _reconcile_json_applied_candidates(
        self,
        history: SmartReviewHistoryStore,
        run_id: str,
        entry_ids: Collection[int] | None = None,
    ) -> bool:
        """以 SQLite audit marker 對帳已提交但尚未回寫 JSON 的候選。

        SQLite 的 history id 是跨連線、跨 UI 面板的單調版本；同一個批次在
        版本沒有變化時不需要再次掃描 JSON 分片。回傳 ``True`` 表示本次真的
        執行了對帳，``False`` 表示命中快取或指定條目沒有需要對帳的紀錄。

        清單上的智慧校對標籤只需要目前頁面的條目；傳入 ``entry_ids`` 時只
        對帳這些條目，避免模組切換為了顯示 100 筆清單而掃描整個數萬筆批次。
        不傳入時維持完整對帳，供批次篩選與歷史查詢使用。
        """
        key = (str(self.path), run_id)
        head = self._one("SELECT MAX(id) FROM history")
        head_id = int(head[0] or 0) if head else 0
        if _RECONCILED_HISTORY_HEAD.get(key) == head_id:
            logger.debug(
                "Smart Review 對帳快取命中：run=%s history_head=%s",
                run_id,
                head_id,
            )
            return False

        started = time.perf_counter()
        records = self._smart_review_apply_records(run_id)
        if entry_ids is not None:
            wanted = {int(entry_id) for entry_id in entry_ids}
            records = {
                ordinal: record
                for ordinal, record in records.items()
                if int(record.get("entry_id") or 0) in wanted
            }
            if not records:
                logger.debug(
                    "Smart Review 頁面條目不需對帳：run=%s entries=%s elapsed_ms=%.1f",
                    run_id,
                    len(wanted),
                    (time.perf_counter() - started) * 1000,
                )
                return False
        confirmed_records: dict[int, dict[str, Any]] = {}
        marker_update_succeeded = True
        if records:
            history.reconcile_applied(
                run_id,
                records,
                confirmed_records=confirmed_records,
            )
            marker_update_succeeded = (
                self._mark_smart_review_projection_confirmed(run_id, confirmed_records)
                is not None
            )
        if entry_ids is None and marker_update_succeeded:
            _RECONCILED_HISTORY_HEAD[key] = head_id
        logger.debug(
            "Smart Review 對帳完成：run=%s history_head=%s records=%s elapsed_ms=%.1f",
            run_id,
            head_id,
            len(records),
            (time.perf_counter() - started) * 1000,
        )
        return True

    def _mark_smart_review_projection_confirmed(
        self,
        run_id: str,
        records: Mapping[int, Mapping[str, Any]],
    ) -> int | None:
        """把已確認回寫 JSON 的 apply marker 標成可受保留政策清理。"""
        updates = []
        for ordinal, record in records.items():
            batch = str(record.get("batch") or "")
            if not batch:
                continue
            updates.append(
                (
                    int(record.get("entry_id") or 0),
                    batch,
                    f"run={run_id}; ordinal={int(ordinal)}",
                )
            )
        if not updates:
            return 0
        try:
            with self._tx(bump_generation_on_noop=False) as conn:
                before = conn.total_changes
                conn.executemany(
                    "UPDATE history SET projection_state='projected' "
                    "WHERE action='smart_review_apply' "
                    "AND projection_state='pending' AND entry_id=? AND batch=? AND note=?",
                    updates,
                )
                return conn.total_changes - before
        except (OSError, sqlite3.Error):
            # JSON 已經確認；狀態欄位更新失敗時保留 pending，下一次讀取可重試。
            logger.warning(
                "Smart Review projection marker 更新失敗，將於下次讀取重試：run=%s",
                run_id,
                exc_info=True,
            )
            return None

    def _smart_review_apply_records(self, run_id: str) -> dict[int, dict[str, Any]]:
        """讀取某批次每個序號最新的套用 audit（``ordinal -> entry_id／batch／applied_at``）。"""
        note_prefix = f"run={run_id}; ordinal="
        # 以 note 範圍（不用 LIKE）走 idx_history_smart_review_apply；
        # 最新的套用在 Python 端依 id 排序，避免臨時排序。
        upper = note_prefix[:-1] + chr(ord(note_prefix[-1]) + 1)
        rows = sorted(
            self._q(
                "SELECT id, note, entry_id, batch, at FROM history "
                "WHERE action='smart_review_apply' AND note >= ? AND note < ?",
                (note_prefix, upper),
            ),
            key=lambda row: row[0],
            reverse=True,
        )
        records: dict[int, dict[str, Any]] = {}
        for _id, note, entry_id, batch, applied_at in rows:
            value = str(note or "")
            if not value.startswith(note_prefix):
                continue
            try:
                ordinal = int(value[len(note_prefix) :])
            except ValueError:
                continue
            records.setdefault(
                ordinal,
                {
                    "entry_id": int(entry_id),
                    "batch": batch,
                    "applied_at": applied_at,
                },
            )
        return records

    def smart_review_projection_report(self, run_id: str) -> dict[str, Any]:
        """比對 SQLite 套用 audit 與 JSON 候選，回報投影同步狀態（唯讀，不修改任何資料）。

        ``pending_ordinals`` 是 audit 已提交、但 JSON 尚未標成同一次套用的候選序號；
        有任何一筆就是 ``pending_projection``，下一次讀取標籤時會由對帳補回。
        沒有待補時回傳 JSON 內記錄的狀態（沒有紀錄的舊批次視為 ``projected``）。
        """
        history = SmartReviewHistoryStore(self.path)
        run = history.get_run(run_id)
        info = (run or {}).get("projection")
        info = info if isinstance(info, dict) else {}
        records = self._smart_review_apply_records(run_id)
        applied = {
            candidate.ordinal: candidate
            for candidate in history.list_candidates(run_id)
            if candidate.status == SmartReviewStatus.APPLIED
        }
        pending = sorted(
            ordinal
            for ordinal, record in records.items()
            if ordinal not in applied
            or applied[ordinal].applied_batch != (record["batch"] or None)
        )
        state = (
            ProjectionState.PENDING_PROJECTION
            if pending
            else info.get("state") or ProjectionState.PROJECTED
        )
        return {
            "run_id": run_id,
            "state": str(state),
            "revision": int(info.get("revision") or 0),
            "pending_ordinals": pending,
        }

    def _reconciled_latest_marks(
        self,
        entry_ids: Collection[int] | None = None,
    ) -> tuple[str | None, dict[int, tuple[str, str | None]]]:
        """取得最近批次標記；SQLite 套用紀錄有新增時先對帳，換頁不重複掃描。

        對帳只在 ``history`` 的最大 id 比上次對帳時大才執行（查最大 id 為常數成本），
        對帳失敗時不記錄，下次再試。
        """
        history = SmartReviewHistoryStore(self.path)
        run_id, marks = history.latest_run_marks()
        if run_id is None:
            return run_id, marks
        key = (str(self.path), run_id)
        head = self._one("SELECT MAX(id) FROM history")
        head_id = int(head[0] or 0) if head else 0
        if _RECONCILED_HISTORY_HEAD.get(key) == head_id:
            return run_id, marks
        try:
            reconciled = self._reconcile_json_applied_candidates(
                history, run_id, entry_ids
            )
        except (OSError, ValueError):
            logger.warning(
                "Smart Review 標籤對帳失敗，下次再試：run=%s", run_id, exc_info=True
            )
            return run_id, marks
        if not reconciled:
            return run_id, marks
        if entry_ids is None:
            _RECONCILED_HISTORY_HEAD[key] = head_id
        return history.latest_run_marks()

    def smart_review_marks(
        self, entry_ids: Sequence[int]
    ) -> dict[int, SmartReviewMark]:
        """回傳指定條目在最近一次智慧校對批次的處理狀態。

        只列出已有處理結果的狀態（已套用、已保留原譯文、不用改、需補 context、
        無效、格式不符、已過期）；待審查的 ``ready`` 與未完成的候選不標記。
        """
        wanted = {int(entry_id) for entry_id in entry_ids}
        if not wanted:
            return {}
        run_id, marks = self._reconciled_latest_marks(wanted)
        if run_id is None:
            return {}
        result: dict[int, SmartReviewMark] = {}
        for entry_id in wanted:
            found = marks.get(entry_id)
            if found is not None and found[0] in SMART_REVIEW_MANUAL_EDIT_STATUSES:
                result[entry_id] = SmartReviewMark(run_id, found[0], found[1])
        return result

    def smart_review_entry_ids(self, group: str) -> tuple[int, ...]:
        """回傳最近一次批次中處於指定狀態群組的條目 id（鍵見 SMART_REVIEW_FILTER_GROUPS）。"""
        statuses = set(SMART_REVIEW_FILTER_GROUPS[group])
        _run_id, marks = self._reconciled_latest_marks()
        return tuple(
            sorted(entry_id for entry_id, mark in marks.items() if mark[0] in statuses)
        )
