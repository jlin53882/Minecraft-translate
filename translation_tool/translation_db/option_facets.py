"""option_facets.py

條目篩選的「模組 × 生效來源」筆數分面（facet）。

同一組篩選條件（拿掉模組與來源選擇後）算一次分面，就能同時得到：

- 模組與來源下拉選單的可用選項（:func:`options`）；
- 任一模組／來源選擇下的符合總筆數（:func:`total`），所以翻頁、換模組、換來源都不必重新計數。

分面是 ``[(模組, 總數, 有生效譯文的數量, 各來源代碼的數量…)]``，來源代碼清單隨結果保存。
條件只涉及版本、類型、審核狀態或「沒有譯文」時，直接由觸發器維護的 ``stat_src`` 彙總；
其他條件（搜尋、狀態、時間…）以單次沿 ``idx_entry_list_order`` 的串流掃描計算。
兩條路徑的結果形狀相同，由呼叫端依資料世代快取。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from translation_tool.translation_db.models import EntryFilter
from translation_tool.translation_db.stat_summary import NO_SOURCE

Facets = dict[str, Any]


def summary_eligible(criteria: EntryFilter) -> bool:
    """篩選只剩 ``stat_src`` 能回答的條件（版本、類型、審核狀態、無譯文）。"""
    return (
        criteria.state in ("all", "none")
        and not (criteria.state == "none" and criteria.review_status is not None)
        and not criteria.query.strip()
        and criteria.entry_ids is None
        and criteria.include_ids is None
        and not criteria.exclude_ids
        and criteria.time is None
        and criteria.smart_review is None
        and not criteria.quality.active
    )


def _source_sums(codes: Sequence[int], column: str, weight: str) -> str:
    """各來源代碼的條件加總欄位（每個代碼一個 ``?`` 參數，順序同 ``codes``）。"""
    return "".join(
        f", SUM(CASE WHEN {column}=? THEN {weight} ELSE 0 END)" for _ in codes
    )


def summary_sql(criteria: EntryFilter, codes: Sequence[int]) -> tuple[str, list]:
    """由 ``stat_src`` 計算分面的 SQL 與參數（需先確認 :func:`summary_eligible`）。"""
    where = ["mc_version = ?"]
    params: list = [criteria.version]
    if criteria.kind:
        where.append("kind = ?")
        params.append(criteria.kind)
    if criteria.state == "none":
        where.append("source = ?")
        params.append(NO_SOURCE)
    if criteria.review_status == "reviewed":
        # 舊資料（歷史狀態待確認）畫面上同樣顯示「人工已審核」，篩選一併納入
        where.append("review IN ('reviewed', 'legacy_unknown')")
    elif criteria.review_status is not None:
        where.append("review = ?")
        params.append(criteria.review_status)
    sql = (
        "SELECT mod_id, SUM(cnt), "
        f"SUM(CASE WHEN source <> {NO_SOURCE} THEN cnt ELSE 0 END){_source_sums(codes, 'source', 'cnt')} "
        f"FROM stat_src WHERE {' AND '.join(where)} "
        "GROUP BY mod_id HAVING SUM(cnt) > 0"
    )
    return sql, [*codes, *params]


def scan_sql(candidate_source: str, condition: str, codes: Sequence[int]) -> str:
    """條目掃描版分面的 SQL（``candidate_source`` 為已連接所需資料表的 FROM 子句）。

    ``GROUP BY e.mod_id`` 沿 ``idx_entry_list_order`` 的模組順序串流彙總，
    不需要暫存排序；各來源的數量用條件加總，不另外分組。
    """
    return (
        "SELECT e.mod_id, COUNT(*), SUM(f.source IS NOT NULL)"
        f"{_source_sums(codes, 'f.source', '1')} "
        f"{candidate_source} WHERE {condition} GROUP BY e.mod_id"
    )


def build(rows: Sequence[Sequence], codes: Sequence[int]) -> Facets:
    """把查詢列整理成可快取（JSON）的分面；來源加總與有譯文數不符時標記為不完整。"""
    normalized = [
        [str(row[0]), *(int(value or 0) for value in row[1:])] for row in rows
    ]
    complete = all(row[2] == sum(row[3:]) for row in normalized)
    return {"codes": list(codes), "rows": normalized, "complete": complete}


def _count(row: Sequence[int], codes: Sequence[int], source: int | None) -> int:
    if source is None:
        return row[1]
    return row[3 + codes.index(source)] if source in codes else 0


def total(facets: Facets, mod_id: str | None, source: int | None) -> int:
    """指定模組（``None`` 為全部）與來源（``None`` 為全部）下的符合筆數。"""
    codes = facets["codes"]
    return sum(
        _count(row, codes, source)
        for row in facets["rows"]
        if mod_id is None or row[0] == mod_id
    )


def options(
    facets: Facets, mod_id: str | None, source: int | None
) -> tuple[list[str], tuple[int, ...]]:
    """目前選擇下可用的模組與生效來源；各自忽略自己的選擇。"""
    codes = facets["codes"]
    mods = sorted(row[0] for row in facets["rows"] if _count(row, codes, source) > 0)
    sources = tuple(
        sorted(
            code
            for index, code in enumerate(codes)
            if any(
                row[3 + index] > 0
                for row in facets["rows"]
                if mod_id is None or row[0] == mod_id
            )
        )
    )
    return mods, sources
