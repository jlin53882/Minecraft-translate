"""智慧校對審查頁的候選載入：批次資訊、統計、分頁讀取，以及前後一筆的預載快取。

``load_run``、``read_position`` 與 ``read_window`` 都只做唯讀查詢（人工編輯候選的快照對齊除外），
並在**同一個唯讀連線**內完成一次導覽操作需要的所有讀取：載入位置時順便取回前後各一筆、套用後刷新
時一併取回狀態統計。使用者可見的換筆由 UI 執行緒直接呼叫 ``read_position``；``read_window`` 只作為
背景預載，讓下一筆可以直接顯示。預載的候選只由 UI 執行緒透過 ``remember``／``take`` 存取；任何寫入、
換批次或換篩選都要呼叫 ``invalidate``，資料庫內容改變（``db_identity`` 不同）時快取也會自動失效。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services_impl.moddb_smart_review_service import (
    SmartReviewCandidate,
    SmartReviewReadSession,
    moddb_smart_review_read_session,
    realign_moddb_smart_review_candidate_from_path,
)
from app.views.moddb.smart_review_evidence import is_manual_edit_row

# 預載目前位置前後各幾筆。
PRELOAD_RADIUS = 1
# 快取最多保留離目前位置幾筆內的候選（超過就丟，避免長時間瀏覽時無限成長）。
_CACHE_RADIUS = PRELOAD_RADIUS + 1
_BREAKDOWN_ERRORS = (sqlite3.Error, OSError, ValueError, RuntimeError)


@dataclass(frozen=True)
class LoadedRun:
    """批次資訊與各狀態筆數；``info`` 為 None 表示找不到這個批次。"""

    info: dict[str, Any] | None
    counts: dict[str, int]


@dataclass(frozen=True)
class LoadedPosition:
    """某個位置的讀取結果。

    ``row`` 是該位置的候選（位置超出範圍時為 None）。``window`` 是可以直接顯示的候選，
    以位置為鍵；需要對齊快照的人工編輯候選不放進來，因為顯示前必須重新讀取。
    ``counts`` 只在要求一併刷新統計時有值，統計讀取失敗時為 None。
    """

    total: int
    row: SmartReviewCandidate | None
    window: dict[int, SmartReviewCandidate] = field(default_factory=dict)
    counts: dict[str, int] | None = None


def _read_window(
    session: SmartReviewReadSession,
    run_id: str,
    status: str | None,
    position: int,
    known_total: int | None,
) -> tuple[int, dict[int, SmartReviewCandidate]]:
    """用一次查詢讀取 ``position`` 前後各 ``PRELOAD_RADIUS`` 筆，回傳總數與「位置 → 候選」。"""
    start = max(0, position - PRELOAD_RADIUS)
    total, rows = session.candidate_page(
        run_id,
        status=status,
        limit=position - start + 1 + PRELOAD_RADIUS,
        offset=start,
        known_total=known_total,
    )
    return total, {start + index: row for index, row in enumerate(rows)}


class SmartReviewCandidateLoader:
    """讀取審查頁要顯示的候選，並保管前後一筆的預載快取。"""

    def __init__(self) -> None:
        """建立空的預載快取。"""
        self._cache: dict[int, SmartReviewCandidate] = {}
        self._cache_key: tuple[str, str | None, str | None] | None = None

    # ──────────────── 背景執行緒：唯讀查詢 ────────────────

    def load_run(self, path: Path, priority: tuple[int, ...], run_id: str) -> LoadedRun:
        """在同一個連線內取得批次資訊與各狀態筆數。"""
        with moddb_smart_review_read_session(path, priority) as session:
            info = session.get_run(run_id)
            breakdown = session.run_breakdown(run_id)
        return LoadedRun(info, dict(breakdown.get("status", {})))

    def read_position(
        self,
        path: Path,
        priority: tuple[int, ...],
        run_id: str,
        *,
        status: str | None,
        position: int,
        known_total: int | None,
        with_counts: bool = False,
    ) -> LoadedPosition:
        """載入 ``position`` 的候選；可人工編輯的候選先對齊目前資料庫，讓畫面顯示現況。

        ``with_counts`` 為真時在同一個連線內一併取得狀態統計（套用或保留之後的刷新）。
        """
        with moddb_smart_review_read_session(path, priority) as session:
            requested_position = max(0, int(position))
            total, window = _read_window(
                session, run_id, status, requested_position, known_total
            )
            position = min(requested_position, total - 1) if total else 0
            row = window.get(position)
            if row is None and total:
                # The queue may have shrunk since the UI last counted it.  A clamped
                # page can be keyed by the old offset, so re-read around the actual
                # last position before declaring the candidate missing.
                total, window = _read_window(
                    session,
                    run_id,
                    status,
                    position,
                    None if known_total is not None else total,
                )
                position = min(position, total - 1) if total else 0
                row = window.get(position)
            if is_manual_edit_row(row) and (
                realign_moddb_smart_review_candidate_from_path(
                    path, priority, run_id, row.ordinal
                )
                == "realigned"
            ):
                total, window = _read_window(session, run_id, status, position, total)
                row = window.get(position)
            counts = self._read_counts(session, run_id) if with_counts else None
        return LoadedPosition(total, row, self._displayable(window), counts)

    def read_window(
        self,
        path: Path,
        priority: tuple[int, ...],
        run_id: str,
        *,
        status: str | None,
        position: int,
        known_total: int,
    ) -> dict[int, SmartReviewCandidate]:
        """只讀取 ``position`` 附近的候選供預載使用，不做快照對齊。"""
        with moddb_smart_review_read_session(path, priority) as session:
            _total, window = _read_window(
                session, run_id, status, position, known_total
            )
        return self._displayable(window)

    @staticmethod
    def _read_counts(
        session: SmartReviewReadSession, run_id: str
    ) -> dict[str, int] | None:
        """讀取狀態統計；失敗時回傳 None，讓候選本身仍能顯示。"""
        try:
            return dict(session.run_breakdown(run_id).get("status", {}))
        except _BREAKDOWN_ERRORS:
            return None

    @staticmethod
    def _displayable(
        window: dict[int, SmartReviewCandidate],
    ) -> dict[int, SmartReviewCandidate]:
        """去掉顯示前必須重新對齊快照的人工編輯候選。"""
        return {
            position: row
            for position, row in window.items()
            if not is_manual_edit_row(row)
        }

    # ──────────────── UI 執行緒：預載快取 ────────────────

    def remember(
        self,
        run_id: str,
        status: str | None,
        db_identity: str | None,
        position: int,
        window: dict[int, SmartReviewCandidate],
    ) -> None:
        """記下 ``position`` 附近可直接顯示的候選；快取來源（批次、篩選、資料庫）不同時先清空。"""
        key = (run_id, status, db_identity)
        if key != self._cache_key:
            self._cache = {}
            self._cache_key = key
        self._cache.update(window)
        self._cache = {
            index: row
            for index, row in self._cache.items()
            if abs(index - position) <= _CACHE_RADIUS
        }

    def take(
        self,
        run_id: str,
        status: str | None,
        db_identity: str | None,
        position: int,
    ) -> SmartReviewCandidate | None:
        """取得已預載的候選；來源不符或沒有預載時回傳 None（呼叫端改走同步導覽載入）。"""
        if self._cache_key != (run_id, status, db_identity):
            return None
        return self._cache.get(position)

    def missing_neighbors(
        self,
        run_id: str,
        status: str | None,
        db_identity: str | None,
        position: int,
        total: int,
    ) -> bool:
        """``position`` 前後各一筆是否還有尚未預載的（需要再讀一次）。"""
        wanted = range(
            max(0, position - PRELOAD_RADIUS), min(total, position + PRELOAD_RADIUS + 1)
        )
        return any(
            self.take(run_id, status, db_identity, index) is None
            for index in wanted
            if index != position
        )

    def invalidate(self) -> None:
        """丟棄所有預載（套用、保留、存草稿、換批次或換篩選之後）。"""
        self._cache = {}
        self._cache_key = None
