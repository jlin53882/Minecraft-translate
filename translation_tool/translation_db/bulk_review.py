"""批量審核：依「版本 + 模組 + 目前生效來源」把一批條目標成人工已審核（生效來源不變）。

單筆審核的規則（只改審核狀態、記錄歷史、可還原、同步其他版本）完全沿用
``TranslationDB.review_manual``；這裡只負責選取範圍、預覽統計與逐筆套用。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Sequence

from translation_tool.translation_db.content_identity import (
    pack_scope_of,
    same_content_sql,
)
from translation_tool.translation_db.models import (
    BulkReviewCandidate,
    BulkReviewPlan,
    BulkReviewResult,
    EntryFilter,
    QualityFilter,
)
from translation_tool.translation_db.sql_helpers import translation_review_sql

_ID_CHUNK = 5000


class BulkReviewMixin:
    """``TranslationDB`` 的批量審核方法（使用其連線、交易與審核預覽）。"""

    def bulk_review_scope_options(
        self,
        version: str,
        *,
        quality_pass_only: bool = True,
        kind: str | None = None,
        source: int | None = None,
        pack_scope: str | None = None,
    ) -> tuple[tuple[str, ...], dict[str | None, tuple[int, ...]]]:
        """取得目前版本中真正有未審核候選的模組與來源對照。

        ``kind`` 與 ``source`` 為目前的類型與來源選擇：類型會縮小模組與來源清單，
        來源只縮小模組清單（來源清單本身不受來源選擇限制）。回傳的 ``None`` 模組鍵代表全部模組；
        查詢沿用批量審核候選的條件，因此不會把只有已審核條目的模組或沒有目前譯文的來源顯示在選單中。
        ``pack_scope`` 為模組包版本的選擇：模組與來源清單都只算該模組包的模組。
        """
        triples = self._bulk_review_pack_triples(version, quality_pass_only, pack_scope)
        in_kind = [
            (mod_value, source_value)
            for mod_value, source_value, kind_value in triples
            if kind is None or kind_value == kind
        ]
        modules = sorted(
            {
                str(mod_value)
                for mod_value, source_value in in_kind
                if source is None or source_value == source
            }
        )
        sources_by_mod: dict[str, set[int]] = {}
        all_sources: set[int] = set()
        for mod_value, source_value in in_kind:
            mod_id = str(mod_value)
            sources = sources_by_mod.setdefault(mod_id, set())
            if source_value is not None:
                sources.add(int(source_value))
                all_sources.add(int(source_value))
        return (
            tuple(modules),
            {
                None: tuple(sorted(all_sources)),
                **{
                    mod_id: tuple(sorted(sources))
                    for mod_id, sources in sources_by_mod.items()
                },
            },
        )

    def bulk_review_kind_options(
        self,
        version: str,
        *,
        quality_pass_only: bool = True,
        mod_id: str | None = None,
        source: int | None = None,
        pack_scope: str | None = None,
    ) -> tuple[str, ...]:
        """目前模組包版本、模組與來源選擇下，仍有未審核候選的類型（類型本身的選擇不限制）。"""
        return tuple(
            sorted(
                {
                    kind_value
                    for mod_value, source_value, kind_value in self._bulk_review_pack_triples(
                        version, quality_pass_only, pack_scope
                    )
                    if (mod_id is None or str(mod_value) == mod_id)
                    and (source is None or source_value == source)
                }
            )
        )

    def bulk_review_pack_options(
        self, version: str, *, quality_pass_only: bool = True
    ) -> tuple[str, ...]:
        """有未審核候選的模組包身分（KubeJS／FTB）；模組包版本選單只列這些。"""
        return tuple(
            sorted(
                {
                    scope
                    for mod_value, _source, _kind in self._bulk_review_scope_triples(
                        version, quality_pass_only
                    )
                    if (scope := pack_scope_of(str(mod_value)))
                }
            )
        )

    def _bulk_review_pack_triples(
        self, version: str, quality_pass_only: bool, pack_scope: str | None
    ) -> list[tuple[str, int | None, str]]:
        """範圍配對中屬於指定模組包身分的部分；沒有指定時為全部。"""
        triples = self._bulk_review_scope_triples(version, quality_pass_only)
        if not pack_scope:
            return triples
        return [row for row in triples if pack_scope_of(row[0]) == pack_scope]

    def _bulk_review_scope_triples(
        self,
        version: str,
        quality_pass_only: bool,
    ) -> list[tuple[str, int | None, str]]:
        """有未審核候選的（模組, 生效來源, 類型）配對；範圍選單的模組、來源與類型都由它篩出。

        只查選單所需的欄位，避免建立完整候選物件。品質旗標可用時直接沿 SQL 條件篩選；
        舊資料庫尚未完成旗標重算時，退回原本的完整候選判斷，確保結果不因最佳化而改變。
        """
        criteria = EntryFilter(
            version=version,
            review_status="unreviewed",
            quality=QualityFilter(status="consistent")
            if quality_pass_only
            else QualityFilter(),
        )
        if quality_pass_only and not self._quality_flags_ready():
            # 與 _bulk_review_candidates 相同：空白譯文不應出現在範圍選單。
            with self._lock:
                return self._cached(
                    self._facet_key(
                        "bulk-review-scope-rows", version, quality_pass_only
                    ),
                    lambda: [
                        [str(row[3]), row[7], str(row[1])]
                        for row in self._query_entry_rows(
                            criteria, batch_replace_projection=True
                        )
                        if row[6] and row[6].strip()
                    ],
                    persist=False,
                )
        condition, params = self._filter_sql(criteria)
        if quality_pass_only:
            condition += " AND " + self._quality_prefilter_sql(
                criteria.quality, flags_ready=True
            )
        # 與 _bulk_review_candidates 相同：空白譯文不應出現在範圍選單。
        condition += " AND f.zh_tw IS NOT NULL AND trim(f.zh_tw) <> ''"
        source = self._candidate_source(f"{condition} f.source")
        sql = f"SELECT DISTINCT e.mod_id, f.source, e.kind {source} WHERE {condition}"
        with self._lock:
            rows = self._cached(
                self._facet_key("bulk-review-scope", sql, params),
                lambda: [list(row) for row in self._q(sql, params)],
                persist=False,
            )
        return [(str(mod), src, str(kind)) for mod, src, kind in rows]

    def _bulk_review_candidates(
        self,
        version: str,
        source: int | None,
        quality_pass_only: bool,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
        pack_scope: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[BulkReviewCandidate, ...]:
        """所選版本／模組／類型中、來源為 ``source`` 且尚未審核的條目（有譯文者）。

        ``source=None`` 代表不限目前生效來源；``kind=None`` 代表不限類型。
        ``pack_scope`` 只列該模組包身分的模組（已選模組時以模組為準）。
        """
        criteria = EntryFilter(
            version=version,
            mod_id=mod_id or None,
            pack_mod_ids=(
                tuple(self.pack_scope_mods(version, pack_scope))  # type: ignore[attr-defined]
                if pack_scope
                else None
            ),
            kind=kind or None,
            source=int(source) if source is not None else None,
            # 審核狀態與來源各自獨立：任何來源都只列尚未審核的條目
            review_status="unreviewed",
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
        manual_review = translation_review_sql("m", self.has_review_state)
        revision = "m.revision" if self.has_translation_revision else "NULL"
        effective_review = translation_review_sql("f2", self.has_review_state)
        effective_revision = "et2.revision" if self.has_translation_revision else "NULL"
        sql = (
            "SELECT e.id, e2.id, f.zh_tw, f2.zh_tw, f2.source, m.zh_tw, "
            f"{manual_review}, {revision}, f2.checker, {effective_review}, "
            f"{effective_revision}, f.source "
            "FROM entry e JOIN effective f ON f.entry_id = e.id "
            f"JOIN entry e2 ON {same_content_sql('e', 'e2')} "
            "AND e2.mc_version <> ? "
            "LEFT JOIN effective f2 ON f2.entry_id = e2.id "
            "LEFT JOIN translation m ON m.entry_id = e2.id AND m.source = f.source "
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
                if (row[9] or "") == "reviewed" and (theirs or "") == (mine or ""):
                    continue
                if (
                    (row[9] or "") == "reviewed"
                    and _source != row[11]
                    and (theirs or "") != (mine or "")
                ):
                    continue  # 另一來源已審核的不同譯文：實際審核會保護它，不會寫入
                entry[0] += 1
                entry[1] += 0 if theirs == mine else 1
        return tuple(
            (eid, total, replaced, digest.hexdigest())
            for eid, (total, replaced, digest) in sorted(stats.items())
        )

    def preview_bulk_review(
        self,
        version: str,
        source: int | None,
        *,
        mod_id: str | None = None,
        kind: str | None = None,
        quality_pass_only: bool = True,
        propagate: bool = False,
        pack_scope: str | None = None,
    ) -> BulkReviewPlan:
        """預覽批量審核範圍（不寫入）。"""
        source = int(source) if source is not None else None
        mod_id = str(mod_id).strip() if mod_id is not None else None
        mod_id = mod_id or None
        kind = str(kind).strip() if kind is not None else None
        kind = kind or None
        pack_scope = (pack_scope or "").strip() or None
        candidates = self._bulk_review_candidates(
            version,
            source,
            quality_pass_only,
            mod_id=mod_id,
            kind=kind,
            pack_scope=pack_scope,
        )
        sibling_info = (
            self._bulk_review_sibling_info(candidates, version) if propagate else ()
        )
        return BulkReviewPlan(
            version=version,
            source=source,
            mod_id=mod_id,
            quality_pass_only=quality_pass_only,
            propagate=propagate,
            database_identity=self._database_identity(),
            candidates=candidates,
            sibling_info=sibling_info,
            kind=kind,
            pack_scope=pack_scope,
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
        with self._tx(bump_generation_on_noop=False, keep_review=True) as conn:
            if self._database_identity() != plan.database_identity:
                raise ValueError("資料庫已切換，請重新預覽批量審核")
            if progress_callback is not None:
                progress_callback("重新檢查資料庫快照", 0.02)
            current = self._bulk_review_candidates(
                plan.version,
                plan.source,
                plan.quality_pass_only,
                mod_id=plan.mod_id,
                kind=plan.kind,
                pack_scope=plan.pack_scope,
                conn=conn,
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
