"""variants.py

同一個條目的「其他原文」（變體）與其譯文。

``entry`` 對 ``(類型, 版本, 模組, 鍵值)`` 只存一份主原文；模組更新或不同整合包讓同一個鍵值
出現不同原文時，另一份原文與它的譯文存在 ``entry_variant`` / ``variant_translation``
（與 ``entry`` / ``translation`` 同形）。決策與取捨見 ``docs/adr/0007-moddb-source-variants.md``。

本模組獨佔這兩張表的讀寫：其他模組只呼叫這裡的函式，不直接寫 SQL。變體只影響流水線的
譯文查詢（``TranslationResolver`` 讀 ``load_rows_sql``）；條目清單、統計、審核、跨版本沿用
都只看 ``entry``／``effective``，不受影響。
"""

from __future__ import annotations

import sqlite3

# 與 ``repository.load_mod`` 相同的欄位順序：(類型, 鍵值, 原文, 版本, 譯文, 來源)
_ROW_COLUMNS = (
    "ranked.kind, ranked.key, ranked.en_us, ranked.mc_version, "
    "ranked.zh_tw, ranked.source"
)


def load_rows_sql(order_by: str) -> str:
    """一個模組所有變體的生效譯文查詢（參數：``mod_id``）。

    ``order_by`` 是 ``t``（``variant_translation``）別名的來源排序（與條目的生效譯文
    使用同一套優先序）；每個變體只取排序第一的譯文。CROSS JOIN 讓 SQLite 從該模組的
    條目出發逐層探查，不先排序整張譯文表。
    """
    return f"""
        SELECT {_ROW_COLUMNS}
        FROM (
            SELECT e.kind, e.key, v.en_us, e.mc_version, t.zh_tw, t.source,
                   ROW_NUMBER() OVER (
                       PARTITION BY t.variant_id ORDER BY {order_by}
                   ) AS rn
            FROM entry e
            CROSS JOIN entry_variant v ON v.entry_id = e.id
            CROSS JOIN variant_translation t ON t.variant_id = v.id
            WHERE e.mod_id = ? AND t.zh_tw <> ''
        ) AS ranked
        WHERE ranked.rn = 1
    """


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
