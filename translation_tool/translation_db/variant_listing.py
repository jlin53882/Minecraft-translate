"""其他原文（變體）清單：依版本、有無譯文與關鍵字分頁瀏覽。

只讀。SQL 沒有臨時排序：變體驅動（``CROSS JOIN`` 固定連接順序）、以主鍵倒序取當頁編號，
再用編號讀完整欄位並在記憶體選每份其他原文的生效譯文，與條目詳情
（``variants.entry_variants``）同一套優先序。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from translation_tool.translation_db.variants import UNTRANSLATED_SQL, tables_available

STATUS_ALL = "all"
STATUS_UNTRANSLATED = "untranslated"
STATUS_TRANSLATED = "translated"
STATUSES = (STATUS_ALL, STATUS_UNTRANSLATED, STATUS_TRANSLATED)

_ID_CHUNK = 500


@dataclass(frozen=True)
class VariantListRow:
    """清單的一份其他原文，連同它所屬條目的主原文與目前生效譯文。"""

    variant_id: int
    entry_id: int
    kind: str
    mod_id: str
    key: str
    main_en: str  # 條目主原文
    main_zh: str  # 條目目前生效譯文（沒有為空字串）
    en_us: str  # 這份其他原文
    zh_tw: str  # 這份其他原文的生效譯文（沒有為空字串）
    source: int | None
    review_status: str | None
    last_seen: str


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(
    version: str, status: str, search: str, mod_id: str | None
) -> tuple[str, list]:
    where = "e.mc_version = ? AND v.en_us <> ''"
    params: list = [version]
    if mod_id:
        where += " AND e.mod_id = ?"
        params.append(mod_id)
    if status == STATUS_UNTRANSLATED:
        where += f" AND {UNTRANSLATED_SQL}"
    elif status == STATUS_TRANSLATED:
        where += f" AND NOT {UNTRANSLATED_SQL}"
    text = search.strip()
    if text:
        pattern = _like(text)
        where += (
            " AND (e.key LIKE ? ESCAPE '\\' OR e.mod_id LIKE ? ESCAPE '\\'"
            " OR v.en_us LIKE ? ESCAPE '\\' OR e.en_us LIKE ? ESCAPE '\\')"
        )
        params += [pattern] * 4
    return where, params


def versions_with_variants(conn: sqlite3.Connection) -> list[str]:
    """有其他原文的遊戲版本（其他原文多者在前）；沒有變體表回傳空串列。"""
    if not tables_available(conn):
        return []
    counted = conn.execute(
        "SELECT e.mc_version, COUNT(*) FROM entry_variant v "
        "CROSS JOIN entry e ON e.id = v.entry_id "
        "WHERE v.en_us <> '' GROUP BY e.mc_version"
    ).fetchall()
    # 版本只有幾個：排序在記憶體做，SQL 不排序
    return [version for version, _n in sorted(counted, key=lambda r: (-r[1], r[0]))]


def mods_with_variants(conn: sqlite3.Connection, version: str) -> list[str]:
    """某版本有其他原文的模組。"""
    if not tables_available(conn):
        return []
    return sorted(
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT e.mod_id FROM entry_variant v "
            "CROSS JOIN entry e ON e.id = v.entry_id "
            "WHERE e.mc_version = ? AND v.en_us <> ''",
            (version,),
        )
    )


def list_page(
    conn: sqlite3.Connection,
    version: str,
    group_expr: str,
    rank_expr: str,
    *,
    status: str = STATUS_ALL,
    search: str = "",
    mod_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[VariantListRow], int]:
    """一頁其他原文（新建立的在前）與符合條件的總筆數。

    ``group_expr``／``rank_expr`` 是生效來源排序的兩個運算式（見
    ``TranslationDB._priority_parts``）。沒有變體表（舊 schema 唯讀開啟）回傳 ``([], 0)``。
    """
    if not tables_available(conn):
        return [], 0
    where, params = _where(version, status, search, mod_id)
    base = f"FROM entry_variant v CROSS JOIN entry e ON e.id = v.entry_id WHERE {where}"
    total = int(conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0])
    ids = [
        int(row[0])
        for row in conn.execute(
            f"SELECT v.id {base} ORDER BY v.id DESC LIMIT ? OFFSET ?",
            [*params, max(1, int(limit)), max(0, int(offset))],
        )
    ]
    return _load_rows(conn, ids, group_expr, rank_expr), total


def _load_rows(
    conn: sqlite3.Connection, ids: list[int], group_expr: str, rank_expr: str
) -> list[VariantListRow]:
    best: dict[int, tuple[tuple[int, int], VariantListRow]] = {}
    for start in range(0, len(ids), _ID_CHUNK):
        chunk = ids[start : start + _ID_CHUNK]
        rows = conn.execute(
            f"""
            SELECT v.id, v.entry_id, e.kind, e.mod_id, e.key, e.en_us, f.zh_tw,
                   v.en_us, v.last_seen, t.zh_tw, t.source, t.review_status,
                   {group_expr}, {rank_expr}
            FROM entry_variant v
            CROSS JOIN entry e ON e.id = v.entry_id
            LEFT JOIN effective f ON f.entry_id = e.id
            LEFT JOIN variant_translation t ON t.variant_id = v.id AND t.zh_tw <> ''
            WHERE v.id IN ({",".join("?" * len(chunk))})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            vid, zh = int(row[0]), row[9]
            # LEFT JOIN 沒有譯文時 group／rank 為 NULL：排在任何有譯文的列之後
            order = (row[12], row[13]) if zh is not None else (99, 99)
            if vid in best and order >= best[vid][0]:
                continue
            best[vid] = (
                order,
                VariantListRow(
                    variant_id=vid,
                    entry_id=int(row[1]),
                    kind=row[2],
                    mod_id=row[3],
                    key=row[4],
                    main_en=row[5] or "",
                    main_zh=row[6] or "",
                    en_us=row[7],
                    zh_tw=zh or "",
                    source=row[10],
                    review_status=row[11],
                    last_seen=row[8] or "",
                ),
            )
    return [best[vid][1] for vid in ids if vid in best]
