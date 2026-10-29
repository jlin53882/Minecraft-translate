"""智慧校對候選的選取、篩選、同文去重與分頁。

``_build_smart_review_selection`` 產生列表與數量查詢共用的權威範圍；
同版本、模組、種類且英文與目前譯文狀態相同的條目只保留第一筆，其餘記為
twin，套用時一併寫入。候選的 local context 由 ``smart_review_context`` 提供。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from translation_tool.translation_db.models import SmartReviewSnapshot
from translation_tool.translation_db.schema import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    SRC_MANUAL,
)
from translation_tool.translation_db.sql_helpers import (
    effective_review_sql,
    like_pattern,
)

_SMART_REVIEW_TWIN_GROUP_CHUNK_SIZE = 128


def _effective_review_sql(alias: str = "f") -> str:
    """回傳智慧校對查詢共用的生效審核狀態條件。"""
    return effective_review_sql(alias, True)


def _smart_review_row_group(row: Sequence[Any]) -> tuple[Any, ...]:
    """回傳候選列用來判斷同文的版本、內容與生效狀態。"""
    return (row[2], row[3], row[1], row[5], row[6], row[7], row[8])


def _dedupe_same_text_rows(
    rows: Sequence[tuple[Any, ...]],
) -> list[tuple[tuple[Any, ...], tuple[int, ...]]]:
    """同版本、模組、種類且英文與目前譯文狀態都相同的條目只保留第一筆。"""
    twins: dict[tuple[Any, ...], list[int]] = {}
    kept: list[tuple[tuple[Any, ...], tuple[Any, ...]]] = []
    for row in rows:
        # row: id, kind, mc_version, mod_id, key, en_us, zh_tw, source, status, ...
        group = _smart_review_row_group(row)
        if group in twins:
            twins[group].append(int(row[0]))
            continue
        twins[group] = []
        kept.append((row, group))
    return [(row, tuple(twins[group])) for row, group in kept]


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


_SMART_REVIEW_DEDUPE_COLUMNS = (
    "mc_version",
    "mod_id",
    "kind",
    "en_us",
    "current_zh_tw",
    "effective_source",
    "effective_review",
)


def _smart_review_group_sql(alias: str) -> str:
    """回傳智慧校對同文去重所使用的欄位清單。"""
    return ", ".join(f"{alias}.{column}" for column in _SMART_REVIEW_DEDUPE_COLUMNS)


def _smart_review_candidate_cte(
    selection: _SmartReviewSelection,
    entry_source: str,
) -> str:
    """建立未分頁的候選 CTE，讓列表與數量查詢共用同一份資料範圍。"""
    cte_prefix = selection.reference_cte.rstrip()
    if cte_prefix:
        cte_prefix += ", "
    else:
        cte_prefix = "WITH "
    return (
        cte_prefix
        + "candidate_rows AS ("
        + "SELECT e.id AS entry_id, e.kind AS kind, e.mc_version AS mc_version, "
        "e.mod_id AS mod_id, e.key AS entry_key, e.en_us AS en_us, "
        "f.zh_tw AS current_zh_tw, f.source AS effective_source, "
        f"({selection.effective_review}) AS effective_review, "
        "t.revision AS effective_revision, tm.zh_tw AS manual_zh_tw, "
        "tm.checker AS manual_checker, tm.review_status AS manual_review_status, "
        "tm.revision AS manual_revision "
        "FROM "
        + entry_source
        + " JOIN effective f ON f.entry_id=e.id "
        + selection.reference_join
        + "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
        + f"LEFT JOIN translation tm ON tm.entry_id=e.id AND tm.source={SRC_MANUAL} "
        + f"WHERE {selection.where_sql})"
    )


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
            f"CASE WHEN e.kind IN ('{KIND_LANG}', '{KIND_KUBEJS_LANG}') AND ("
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


class SmartReviewCandidateSelectionMixin:
    """提供給 TranslationDB 使用的智慧校對候選選取與計數。"""

    def smart_review_scope_options(
        self,
        *,
        version: str | None = None,
        kind: str | None = None,
        review_status: str = "unreviewed",
    ) -> tuple[tuple[str, ...], dict[str | None, tuple[int, ...]]]:
        """取得有智慧校對候選的模組與來源對照。

        查詢沿用智慧校對的「目前生效、原文與譯文非空、指定審核狀態」條件，
        回傳的 ``None`` 模組鍵代表全部模組，讓 UI 不必逐一模組計數。
        """
        selection = _build_smart_review_selection(
            version=version,
            mod_id=None,
            kind=kind,
            effective_source=None,
            review_status=review_status,
            query=None,
            require_reviewed_reference=False,
        )
        rows = self._scope_pairs(
            version,
            "FROM entry e JOIN effective f ON f.entry_id=e.id",
            selection.where_sql,
            selection.params,
        )
        modules: list[str] = []
        sources_by_mod: dict[str, set[int]] = {}
        all_sources: set[int] = set()
        for row in rows:
            mod_id = str(row[0])
            if mod_id not in sources_by_mod:
                modules.append(mod_id)
                sources_by_mod[mod_id] = set()
            if row[1] is not None:
                source = int(row[1])
                sources_by_mod[mod_id].add(source)
                all_sources.add(source)
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

    def _complete_smart_review_twin_ids(
        self,
        selection: _SmartReviewSelection,
        selected_rows: Sequence[tuple[tuple[Any, ...], tuple[int, ...]]],
    ) -> list[tuple[tuple[Any, ...], tuple[int, ...]]]:
        """補齊目前頁面代表候選在完整篩選範圍內的同文條目 id。

        候選列表為了保留索引排序，只會先讀取足夠的 raw page；同文條目可能排在
        該 page 之後，因此不能只依賴 raw page 內的去重結果。補查使用相同篩選條件
        與同一組去重欄位，但不排序、不改變代表候選的分頁結果。
        """
        if not selected_rows:
            return []

        groups = list(
            dict.fromkeys(_smart_review_row_group(row) for row, _ in selected_rows)
        )
        group_ids: dict[tuple[Any, ...], set[int]] = {group: set() for group in groups}
        twin_entry_source = "entry e"
        if self._one(
            "SELECT 1 FROM sqlite_schema WHERE type='index' AND name=?",
            ("idx_entry_en",),
        ):
            twin_entry_source = "entry e INDEXED BY idx_entry_en"
        for start in range(0, len(groups), _SMART_REVIEW_TWIN_GROUP_CHUNK_SIZE):
            chunk = groups[start : start + _SMART_REVIEW_TWIN_GROUP_CHUNK_SIZE]
            cte_prefix = selection.reference_cte.rstrip()
            if cte_prefix:
                cte_prefix += ", "
            else:
                cte_prefix = "WITH "
            query = (
                cte_prefix + "selected_groups AS (SELECT "
                "json_extract(value, '$[0]') AS mc_version, "
                "json_extract(value, '$[1]') AS mod_id, "
                "json_extract(value, '$[2]') AS kind, "
                "json_extract(value, '$[3]') AS en_us, "
                "json_extract(value, '$[4]') AS current_zh_tw, "
                "json_extract(value, '$[5]') AS effective_source, "
                "json_extract(value, '$[6]') AS effective_review "
                "FROM json_each(?)), candidate_rows AS ("
                "SELECT e.id, e.mc_version, e.mod_id, e.kind, e.en_us, "
                "f.zh_tw AS f_zh_tw, f.source AS f_source, "
                f"({selection.effective_review}) AS effective_review FROM "
                # CROSS JOIN 固定由頁面群組出發：讓規劃器自選順序時會從 f.source 的
                # 數萬列起頭、每列重掃 json_each，頁面越多越慢（數秒到數十秒）
                + "selected_groups g CROSS JOIN "
                + twin_entry_source
                + " ON e.mc_version IS g.mc_version AND e.mod_id IS g.mod_id "
                "AND e.kind IS g.kind AND e.en_us IS g.en_us "
                + "CROSS JOIN effective f ON f.entry_id=e.id "
                + "AND f.zh_tw IS g.current_zh_tw "
                "AND f.source IS g.effective_source "
                f"AND ({selection.effective_review}) IS g.effective_review "
                + selection.reference_join
                + f"WHERE {selection.where_sql}) "
                + "SELECT id, mc_version, mod_id, kind, en_us, f_zh_tw, "
                "f_source, effective_review FROM candidate_rows"
            )
            # json_each 將有界頁面的群組值當成小型查詢輸入，避免為每個群組
            # 展開大量 OR 條件或在補查階段重新排序整個候選集。
            params = (json.dumps(chunk, ensure_ascii=False), *selection.params)
            for row in self._q(query, tuple(params)):
                group = tuple(row[1:])
                group_ids.setdefault(group, set()).add(int(row[0]))

        completed: list[tuple[tuple[Any, ...], tuple[int, ...]]] = []
        for row, existing_twin_ids in selected_rows:
            ids = group_ids.get(_smart_review_row_group(row), set())
            if not ids:
                ids = {int(row[0]), *(int(value) for value in existing_twin_ids)}
            completed.append(
                (
                    row,
                    tuple(
                        sorted(entry_id for entry_id in ids if entry_id != int(row[0]))
                    ),
                )
            )
        return completed

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
        include_context: bool = True,
    ) -> list[SmartReviewSnapshot]:
        """依穩定條件選出目前智慧校對候選；``limit=0`` 代表不限。

        ``include_context=False`` 僅供不會送出模型請求的範圍／歷史預估使用；
        真正執行智慧校對時維持預設值，以保存完整 local context。
        """
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
            + f"f.zh_tw, f.source, ({selection.effective_review}), t.revision, "
            + "tm.zh_tw, tm.checker, tm.review_status, tm.revision FROM "
            + entry_source
            + " JOIN effective f ON f.entry_id=e.id "
            + selection.reference_join
            + "LEFT JOIN translation t ON t.entry_id=e.id AND t.source=f.source "
            + f"LEFT JOIN translation tm ON tm.entry_id=e.id AND tm.source={SRC_MANUAL} "
            + f"WHERE {selection.where_sql} ORDER BY {selection.order_sql}"
            + " "
        )
        absolute_offset = max(0, int(offset))
        selected_rows, selection_exhausted = self._page_deduped_candidate_rows(
            query_sql, selection.params, absolute_offset, bounded_limit
        )
        if not selection_exhausted:
            selected_rows = self._complete_smart_review_twin_ids(
                selection, selected_rows
            )
        return [
            self._smart_review_snapshot_from_row(
                row,
                twin_ids,
                absolute_offset + row_index,
                include_context=include_context,
            )
            for row_index, (row, twin_ids) in enumerate(selected_rows, 1)
        ]

    def _page_deduped_candidate_rows(
        self,
        query_sql: str,
        params: tuple[Any, ...],
        absolute_offset: int,
        bounded_limit: int,
    ) -> tuple[list[tuple[tuple[Any, ...], tuple[int, ...]]], bool]:
        """讀出去重後的代表候選頁面，回傳（資料列, 是否已讀完整個範圍）。

        為保留索引排序，raw 查詢以倍增的 LIMIT 重讀，直到去重後足夠填滿頁面。
        """
        target_count = absolute_offset + bounded_limit
        if bounded_limit == 0:
            raw_rows = self._q(query_sql, params)
            deduped_rows = _dedupe_same_text_rows(raw_rows)
            return deduped_rows[absolute_offset:], True
        selection_exhausted = False
        raw_fetch_limit = max(1, target_count)
        while True:
            raw_rows = self._q(
                query_sql + "LIMIT ? OFFSET ?",
                (*params, raw_fetch_limit, 0),
            )
            deduped_rows = _dedupe_same_text_rows(raw_rows)
            if len(deduped_rows) >= target_count or len(raw_rows) < raw_fetch_limit:
                selection_exhausted = len(raw_rows) < raw_fetch_limit
                break
            next_limit = raw_fetch_limit * 2
            if next_limit <= raw_fetch_limit:
                break
            raw_fetch_limit = next_limit
        return deduped_rows[absolute_offset:target_count], selection_exhausted

    def _smart_review_snapshot_from_row(
        self,
        row: tuple[Any, ...],
        twin_ids: tuple[int, ...],
        ordinal: int,
        *,
        include_context: bool = True,
    ) -> SmartReviewSnapshot:
        """把候選資料列與其 local context 組成不可變快照。"""
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
        context = (
            self._smart_review_context(
                entry_id,
                kind,
                mc_version,
                mod_id,
                key,
                en_us,
            )
            if include_context
            else []
        )
        return SmartReviewSnapshot(
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
            manual_revision=(None if manual_revision is None else int(manual_revision)),
            twin_entry_ids=twin_ids,
        )

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
        candidate_cte = _smart_review_candidate_cte(selection, "entry e")
        group_sql = _smart_review_group_sql("c")
        # 同文去重要對所有候選列分組（數十萬筆時 1 秒以上）；候選範圍只取決於條目、
        # 生效譯文與審核狀態，所以依資料世代快取，面板每次重新整理不重算
        return int(
            self._cached_count(
                candidate_cte
                + " SELECT COUNT(*) FROM (SELECT 1 FROM candidate_rows c GROUP BY "
                + group_sql
                + ")",
                list(selection.params),
            )
        )
