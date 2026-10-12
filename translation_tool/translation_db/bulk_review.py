"""批量審核：依「版本 + 目前生效來源」把一批條目標成人工已審核。

單筆審核的規則（寫入來源 6、記錄歷史、可還原、同步其他版本）完全沿用
``TranslationDB.review_manual``；這裡只負責選取範圍、預覽統計與逐筆套用。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Sequence

from translation_tool.translation_db.models import (
    BulkReviewCandidate,
    BulkReviewPlan,
    BulkReviewResult,
    EntryFilter,
    QualityFilter,
)
from translation_tool.translation_db.schema import SRC_MANUAL

_ID_CHUNK = 5000


class BulkReviewMixin:
    """``TranslationDB`` 的批量審核方法（使用其連線、交易與審核預覽）。"""

    def _bulk_review_candidates(
        self,
        version: str,
        source: int,
        quality_pass_only: bool,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[BulkReviewCandidate, ...]:
        """所選版本中、目前生效來源為 ``source`` 且尚未人工審核的條目（有譯文者）。"""
        criteria = EntryFilter(
            version=version,
            source=int(source),
            # 來源 6 本身只列「人工-未審核」；其他來源的生效列不會是已審核人工
            review_status="unreviewed" if int(source) == SRC_MANUAL else None,
            quality=QualityFilter(status="consistent")
            if quality_pass_only
            else QualityFilter(),
        )
        rows = self._query_entry_rows(
            criteria, conn=conn, batch_replace_projection=True
        )
        return tuple(
            BulkReviewCandidate(
                entry_id=int(row[0]),
                mod_id=row[3],
                key=row[4],
                en_us=row[5] or "",
                zh_tw=row[6],
                source=row[7],
                checker=row[8],
                review_status=row[9],
                revision=row[20],
                manual_zh_tw=row[15],
                manual_checker=row[16],
                manual_review_status=row[17],
                manual_revision=row[18],
            )
            for row in rows
            if row[6] and row[6].strip()
        )

    def _bulk_review_sibling_info(
        self,
        candidates: Sequence[BulkReviewCandidate],
        version: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[tuple[int, int, int, str], ...]:
        """開啟同步時每個候選條目的其他版本快照（SQL 分批查詢，不逐筆預覽）。

        回傳 ``(候選 id, 會被寫入筆數, 其中譯文不同筆數, 狀態摘要)``；摘要涵蓋所有原文相同的
        其他版本的生效譯文、來源與人工審核狀態，確認時重算並逐筆比對。
        """
        manual_review = (
            "m.review_status"
            if self.has_review_state
            else f"CASE WHEN m.source={SRC_MANUAL} THEN 'legacy_unknown' END"
        )
        revision = "m.revision" if self.has_translation_revision else "NULL"
        effective_review = (
            "f2.review_status"
            if self.has_review_state
            else f"CASE WHEN f2.source={SRC_MANUAL} THEN 'legacy_unknown' END"
        )
        effective_revision = "et2.revision" if self.has_translation_revision else "NULL"
        sql = (
            "SELECT e.id, e2.id, f.zh_tw, f2.zh_tw, f2.source, m.zh_tw, "
            f"{manual_review}, {revision}, f2.checker, {effective_review}, "
            f"{effective_revision} "
            "FROM entry e JOIN effective f ON f.entry_id = e.id "
            "JOIN entry e2 INDEXED BY idx_entry_content "
            "ON e2.kind = e.kind AND e2.mod_id = e.mod_id AND e2.key = e.key "
            "AND e2.en_us = e.en_us AND e2.mc_version <> ? "
            "LEFT JOIN effective f2 ON f2.entry_id = e2.id "
            f"LEFT JOIN translation m ON m.entry_id = e2.id AND m.source = {SRC_MANUAL} "
            "LEFT JOIN translation et2 ON et2.entry_id = e2.id AND et2.source = f2.source "
            "WHERE e.id IN (SELECT value FROM json_each(?)) ORDER BY e.id, e2.id"
        )
        stats: dict[int, list] = {}
        ids = [c.entry_id for c in candidates if c.en_us]
        for start in range(0, len(ids), _ID_CHUNK):
            params = (version, json.dumps(ids[start : start + _ID_CHUNK]))
            rows = (
                conn.execute(sql, params).fetchall()
                if conn is not None
                else self._q(sql, params)
            )
            for row in rows:
                eid, _sibling, mine, theirs, _source, manual, status = row[:7]
                entry = stats.setdefault(int(eid), [0, 0, hashlib.sha256()])
                entry[2].update(repr(tuple(row[1:])).encode("utf-8"))
                if (status or "") == "reviewed" and (manual or "") == mine:
                    continue  # 已審核相同譯文：不會被寫入
                entry[0] += 1
                entry[1] += 0 if theirs == mine else 1
        return tuple(
            (eid, total, replaced, digest.hexdigest())
            for eid, (total, replaced, digest) in sorted(stats.items())
        )

    def preview_bulk_review(
        self,
        version: str,
        source: int,
        *,
        quality_pass_only: bool = True,
        propagate: bool = False,
    ) -> BulkReviewPlan:
        """預覽批量審核範圍（不寫入）。"""
        candidates = self._bulk_review_candidates(version, source, quality_pass_only)
        sibling_info = (
            self._bulk_review_sibling_info(candidates, version) if propagate else ()
        )
        return BulkReviewPlan(
            version=version,
            source=int(source),
            quality_pass_only=quality_pass_only,
            propagate=propagate,
            database_identity=self._database_identity(),
            candidates=candidates,
            sibling_info=sibling_info,
        )

    def execute_bulk_review(
        self,
        plan: BulkReviewPlan,
        *,
        excluded_ids: Sequence[int] = (),
        actor: str = "",
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> BulkReviewResult:
        """在單一交易內審核預覽範圍（扣掉使用者排除的條目）。

        先重算一次範圍，與預覽不完全相同就拒絕（要求重新預覽）；逐筆沿用單筆審核的
        預覽與寫入，單筆在確認當下已被改動者還原該筆並計入 ``stale_skipped``。
        """
        excluded = {int(i) for i in excluded_ids}
        todo = [c for c in plan.candidates if c.entry_id not in excluded]
        batch = uuid.uuid4().hex
        reviewed = synced = stale = 0
        touched: list[int] = []
        with self._tx(bump_generation_on_noop=False) as conn:
            if self._database_identity() != plan.database_identity:
                raise ValueError("資料庫已切換，請重新預覽批量審核")
            if progress_callback is not None:
                progress_callback("重新檢查資料庫快照", 0.02)
            current = self._bulk_review_candidates(
                plan.version, plan.source, plan.quality_pass_only, conn=conn
            )
            if current != plan.candidates:
                raise ValueError("預覽後條目已變動，請重新產生批量審核預覽")
            if plan.propagate and not self._bulk_review_siblings_unchanged(
                plan, todo, conn
            ):
                raise ValueError(
                    "預覽後其他版本的譯文或審核狀態已變動，請重新產生批量審核預覽"
                )
            total = max(1, len(todo))
            for index, candidate in enumerate(todo, 1):
                if progress_callback is not None and index % 100 == 0:
                    progress_callback(
                        f"審核中（{index:,}/{len(todo):,}）",
                        0.05 + 0.9 * index / total,
                    )
                conn.execute("SAVEPOINT bulk_review_entry")
                try:
                    preview = self._review_preview_rows(
                        conn,
                        candidate.entry_id,
                        expected_zh_tw=candidate.zh_tw,
                        expected_source=candidate.source,
                        expected_review_status=candidate.review_status,
                        expected_checker=candidate.checker,
                        propagate=plan.propagate,
                    )
                    done = self._apply_review_preview(
                        conn,
                        candidate.entry_id,
                        preview,
                        candidate.zh_tw,
                        actor,
                        batch,
                    )
                except ValueError:
                    conn.execute("ROLLBACK TO bulk_review_entry")
                    conn.execute("RELEASE bulk_review_entry")
                    stale += 1
                    continue
                conn.execute("RELEASE bulk_review_entry")
                reviewed += sum(1 for change in done if change.is_self)
                synced += sum(1 for change in done if not change.is_self)
                touched.extend(change.entry_id for change in done)
            self._refresh(conn, touched)
        if progress_callback is not None:
            progress_callback("批量審核已提交", 1.0)
        return BulkReviewResult(batch, reviewed, synced, stale)

    def _bulk_review_siblings_unchanged(
        self,
        plan: BulkReviewPlan,
        todo: Sequence[BulkReviewCandidate],
        conn: sqlite3.Connection,
    ) -> bool:
        """納入的條目，其他版本的譯文、來源與審核狀態是否與預覽時完全相同。"""
        wanted = {c.entry_id for c in todo}
        current = {
            info[0]: info
            for info in self._bulk_review_sibling_info(todo, plan.version, conn=conn)
        }
        previewed = {info[0]: info for info in plan.sibling_info if info[0] in wanted}
        return current == previewed

    def revert_review_batch(self, batch_id: str) -> int:
        """還原一次批量審核（沿用單筆還原的 CAS 檢查；之後又被改過的條目不覆蓋）。"""
        row = self._one(
            "SELECT id FROM history WHERE batch=? AND action='review' LIMIT 1",
            (batch_id,),
        )
        if row is None:
            return 0
        return self.revert(int(row[0]), whole_batch=True)
