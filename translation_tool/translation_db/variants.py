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
) -> bool:
    """把譯文寫成條目某份其他原文的變體譯文（只新增、不覆蓋）。

    回傳是否真的新增了一列；同一變體同一來源已有譯文時回傳 False。
    """
    vid = ensure_variant(conn, entry_id, en_us)
    cursor = conn.execute(
        "INSERT OR IGNORE INTO variant_translation (variant_id, source, zh_tw) "
        "VALUES (?, ?, ?)",
        (vid, source, zh_tw),
    )
    return bool(cursor.rowcount)
