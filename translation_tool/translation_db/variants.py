"""variants.py

同一個條目的「其他原文」（變體）與其譯文。

``entry`` 對 ``(類型, 版本, 模組, 鍵值)`` 只存一份主原文；模組更新或不同整合包讓同一個鍵值
出現不同原文時，另一份原文與它的譯文存在 ``entry_variant`` / ``variant_translation``
（與 ``entry`` / ``translation`` 同形）。決策與取捨見 ``docs/adr/0007-moddb-source-variants.md``。

本模組獨佔這兩張表的讀寫：其他模組只呼叫這裡的函式，不直接寫 SQL。變體只影響流水線的
譯文查詢（``TranslationResolver`` 讀 ``load_rows``）；條目清單、統計、審核、跨版本沿用
都只看 ``entry``／``effective``，不受影響。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class VariantRow:
    """條目的一份其他原文與目前生效的譯文（還沒有譯文時 ``zh_tw`` 為空）。"""

    en_us: str
    zh_tw: str
    source: int | None
    review_status: str | None
    last_seen: str


def tables_available(conn: sqlite3.Connection) -> bool:
    """變體表是否存在（舊版 schema 的資料庫以唯讀開啟時不會升級，沒有這兩張表）。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('entry_variant', 'variant_translation')"
    ).fetchone()
    return bool(row and row[0] == 2)


def load_rows(
    conn: sqlite3.Connection, group_expr: str, rank_expr: str, mod_id: str
) -> list[tuple[str, str, str, str, str, int]]:
    """一個模組所有變體的生效譯文，欄位與 ``repository.load_mod`` 相同。

    ``(類型, 鍵值, 原文, 版本, 譯文, 來源)``。``group_expr``／``rank_expr`` 是 ``t``
    （``variant_translation``）別名的來源排序運算式（與條目的生效譯文同一套優先序）：
    SQL 只取回運算式的值、不排序（臨時排序的成本隨模組的變體數成長），每個變體取
    ``(group, rank)`` 最小的譯文，同分取先出現者。沒有變體表時回傳空串列。
    CROSS JOIN 讓 SQLite 從該模組的條目出發逐層探查，不掃整張譯文表。
    """
    if not tables_available(conn):
        return []
    rows = conn.execute(
        f"""
        SELECT v.id, e.kind, e.key, v.en_us, e.mc_version, t.zh_tw, t.source,
               {group_expr}, {rank_expr}
        FROM entry e
        CROSS JOIN entry_variant v ON v.entry_id = e.id
        CROSS JOIN variant_translation t ON t.variant_id = v.id
        WHERE e.mod_id = ? AND t.zh_tw <> ''
        """,
        (mod_id,),
    ).fetchall()
    best: dict[int, tuple[tuple[int, int], tuple[str, str, str, str, str, int]]] = {}
    for vid, kind, key, en_us, version, zh_tw, source, group, rank in rows:
        order = (group, rank)
        if vid not in best or order < best[vid][0]:
            best[vid] = (order, (kind, key, en_us, version, zh_tw, source))
    return [row for _order, row in best.values()]


def variant_id(conn: sqlite3.Connection, entry_id: int, en_us: str) -> int | None:
    """條目上原文完全相同的變體編號；沒有回傳 None（只比對完全相同的原文）。"""
    row = conn.execute(
        "SELECT id FROM entry_variant WHERE entry_id=? AND en_us=?",
        (entry_id, en_us),
    ).fetchone()
    return None if row is None else int(row[0])


def ensure_variant(conn: sqlite3.Connection, entry_id: int, en_us: str) -> int:
    """取得（沒有就建立）條目上這份原文的變體，並更新「最後被看到」時間。"""
    found = variant_id(conn, entry_id, en_us)
    if found is not None:
        conn.execute(
            "UPDATE entry_variant SET last_seen=CURRENT_TIMESTAMP WHERE id=?",
            (found,),
        )
        return found
    cursor = conn.execute(
        "INSERT INTO entry_variant (entry_id, en_us) VALUES (?, ?)",
        (entry_id, en_us),
    )
    return int(cursor.lastrowid or 0)


def add_translation(
    conn: sqlite3.Connection,
    entry_id: int,
    en_us: str,
    source: int,
    zh_tw: str,
    zh_cn: str = "",
) -> bool:
    """把譯文寫成條目某份其他原文的變體譯文（只新增、不覆蓋）。

    回傳是否真的新增了一列；同一變體同一來源已有譯文時回傳 False。
    """
    return add_variant_translation(
        conn, ensure_variant(conn, entry_id, en_us), source, zh_tw, zh_cn
    )


def add_variant_translation(
    conn: sqlite3.Connection, vid: int, source: int, zh_tw: str, zh_cn: str = ""
) -> bool:
    """在已存在的變體上新增一個來源的譯文（只新增、不覆蓋）；回傳是否新增了一列。"""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO variant_translation "
        "(variant_id, source, zh_tw, zh_cn) VALUES (?, ?, ?, ?)",
        (vid, source, zh_tw, zh_cn),
    )
    return bool(cursor.rowcount)


def entry_variants(
    conn: sqlite3.Connection, entry_id: int, group_expr: str, rank_expr: str
) -> list[VariantRow]:
    """條目的所有其他原文，新的在前；每份取優先序最高的譯文（沒有譯文則為空）。

    ``group_expr``／``rank_expr`` 同 ``load_rows``。沒有變體表（舊 schema 唯讀開啟）回傳空串列。
    """
    if not tables_available(conn):
        return []
    rows = conn.execute(
        f"""
        SELECT v.id, v.en_us, v.last_seen, t.zh_tw, t.source, t.review_status,
               {group_expr}, {rank_expr}
        FROM entry_variant v
        LEFT JOIN variant_translation t ON t.variant_id = v.id AND t.zh_tw <> ''
        WHERE v.entry_id = ?
        """,
        (entry_id,),
    ).fetchall()
    best: dict[int, tuple[tuple[int, int], VariantRow]] = {}
    for vid, en_us, last_seen, zh_tw, source, status, group, rank in rows:
        # LEFT JOIN 沒有譯文時 group／rank 為 NULL：排在任何有譯文的列之後
        order = (group, rank) if zh_tw is not None else (99, 99)
        if vid not in best or order < best[vid][0]:
            best[vid] = (
                order,
                VariantRow(en_us, zh_tw or "", source, status, last_seen),
            )
    ordered = sorted(
        best.items(), key=lambda kv: (kv[1][1].last_seen, kv[0]), reverse=True
    )
    return [row for _vid, (_order, row) in ordered]


def backfill_from_src_change(conn: sqlite3.Connection) -> tuple[int, int]:
    """把既有 ``src_change``（原文變動紀錄）登記成條目的其他原文，回傳（新增, 略過）筆數。

    只建立沒有譯文的變體（不連網、不改既有譯文）；找不到對應條目、或新原文與主原文相同的
    紀錄略過。冪等：已登記的原文不重複建立。
    """
    if not tables_available(conn):
        return 0, 0
    total = int(conn.execute("SELECT COUNT(*) FROM src_change").fetchone()[0])
    before = int(conn.execute("SELECT COUNT(*) FROM entry_variant").fetchone()[0])
    conn.execute(
        "INSERT OR IGNORE INTO entry_variant (entry_id, en_us, first_seen, last_seen) "
        "SELECT e.id, sc.new_en, sc.detected_at, sc.detected_at FROM src_change sc "
        "JOIN entry e ON e.kind = sc.kind AND e.mc_version = sc.mc_version "
        "AND e.mod_id = sc.mod_id AND e.key = sc.key "
        "WHERE sc.new_en <> '' AND sc.new_en <> e.en_us"
    )
    after = int(conn.execute("SELECT COUNT(*) FROM entry_variant").fetchone()[0])
    created = after - before
    return created, max(total - created, 0)
