"""effective_columns.py

``effective`` 的三個反正規化欄位，由觸發器維護，讓條目清單的狀態篩選與「最近更新」排序
直接沿索引讀取，不必對整個版本逐筆做關聯子查詢。

- ``mc_version``：條目的版本（``entry.mc_version``）。
- ``diff``：與條目清單「版本不同」的定義相同——原文非空，且同內容
  （``kind``、``mod_id``、``key``、``en_us``）在其他版本的條目也有生效譯文、文字不同。
  同一版本內同內容只有一個條目，所以等同「同內容各條目的生效譯文不只一種」。
- ``upd``：生效來源那一筆 ``translation.updated_at``（沒有該列時為 NULL）。

觸發器：生效列新增、改文字或來源、刪除時，重算同內容各條目的 ``diff``；條目刪除
（``BEFORE DELETE``，串聯刪除生效列時條目已不存在，所以在刪除前把它排除後重算）、
條目的內容或版本改變（變動前重算舊組、變動後重算新組）；譯文的新增、``updated_at``
變動與刪除同步 ``upd``。

``meta`` 的 ``effective_columns_schema_version`` 記錄結構版本，``effective_columns_valid='1'``
表示內容可用；沒有這兩個值的資料庫（較舊的唯讀檔案等）由讀取端改用原本的查詢。
大量改寫（整批重建生效譯文）在同一交易內設 ``effective_columns_frozen`` 暫停觸發器，
做完後全庫重算一次（:func:`frozen`）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

SCHEMA_VERSION = "1"
COLUMNS = {
    "mc_version": "TEXT",
    "diff": "INTEGER NOT NULL DEFAULT 0",
    "upd": "TEXT",
}
_FROZEN = "effective_columns_frozen"
_NOT_FROZEN = f"NOT EXISTS (SELECT 1 FROM meta WHERE key='{_FROZEN}')"
_CONTENT_COLUMNS = ("kind", "mod_id", "key", "en_us")
_ENTRY_KEY_COLUMNS = ("mc_version", *_CONTENT_COLUMNS)

_INDEXES = {
    "idx_eff_ver_diff_src": (
        "CREATE INDEX idx_eff_ver_diff_src ON effective (mc_version, diff, source)"
    ),
    "idx_eff_ver_upd": (
        "CREATE INDEX idx_eff_ver_upd ON effective (mc_version, upd DESC, entry_id)"
    ),
}


def _regroup(content: dict[str, str], exclude: str | None = None) -> str:
    """重算同內容各條目的 ``diff``；``exclude`` 是視為已不在組內的條目編號。"""
    group = " AND ".join(f"e2.{c}={content[c]}" for c in _CONTENT_COLUMNS)
    if exclude is not None:
        group += f" AND e2.id<>{exclude}"
    # 不依賴外層列的子查詢只計算一次
    value = (
        f"({content['en_us']}<>'' AND (SELECT COUNT(DISTINCT f2.zh_tw) FROM entry e2 "
        f"JOIN effective f2 ON f2.entry_id=e2.id WHERE {group}) > 1)"
    )
    return (
        f"UPDATE effective SET diff={value} "
        f"WHERE entry_id IN (SELECT e2.id FROM entry e2 WHERE {group}) "
        f"AND diff IS NOT {value};"
    )


def _content_of(entry_id: str) -> dict[str, str]:
    return {c: f"(SELECT {c} FROM entry WHERE id={entry_id})" for c in _CONTENT_COLUMNS}


def _row_content(row: str) -> dict[str, str]:
    return {c: f"{row}.{c}" for c in _CONTENT_COLUMNS}


def _upd(entry_id: str, source: str) -> str:
    return (
        "(SELECT updated_at FROM translation "
        f"WHERE entry_id={entry_id} AND source={source})"
    )


def _trigger_sql() -> dict[str, str]:
    key_changed = (
        "("
        + ",".join(f"OLD.{c}" for c in _ENTRY_KEY_COLUMNS)
        + ") IS NOT ("
        + ",".join(f"NEW.{c}" for c in _ENTRY_KEY_COLUMNS)
        + ")"
    )
    entry_columns = ", ".join(_ENTRY_KEY_COLUMNS)
    has_effective = "EXISTS (SELECT 1 FROM effective WHERE entry_id={})"
    return {
        "effcol_effective_insert": f"""
            CREATE TRIGGER effcol_effective_insert AFTER INSERT ON effective
            WHEN {_NOT_FROZEN} BEGIN
                UPDATE effective SET
                    mc_version=(SELECT mc_version FROM entry WHERE id=NEW.entry_id),
                    upd={_upd("NEW.entry_id", "NEW.source")}
                WHERE entry_id=NEW.entry_id;
                {_regroup(_content_of("NEW.entry_id"))}
            END""",
        "effcol_effective_source": f"""
            CREATE TRIGGER effcol_effective_source AFTER UPDATE OF source ON effective
            WHEN {_NOT_FROZEN} AND OLD.source IS NOT NEW.source BEGIN
                UPDATE effective SET upd={_upd("NEW.entry_id", "NEW.source")}
                WHERE entry_id=NEW.entry_id;
            END""",
        "effcol_effective_text": f"""
            CREATE TRIGGER effcol_effective_text AFTER UPDATE OF zh_tw ON effective
            WHEN {_NOT_FROZEN} AND OLD.zh_tw IS NOT NEW.zh_tw BEGIN
                {_regroup(_content_of("NEW.entry_id"))}
            END""",
        # 條目本身被刪除時（串聯刪除）條目已不存在，組內重算由 effcol_entry_delete 處理
        "effcol_effective_delete": f"""
            CREATE TRIGGER effcol_effective_delete AFTER DELETE ON effective
            WHEN {_NOT_FROZEN} BEGIN
                {_regroup(_content_of("OLD.entry_id"))}
            END""",
        "effcol_entry_delete": f"""
            CREATE TRIGGER effcol_entry_delete BEFORE DELETE ON entry
            WHEN {_NOT_FROZEN} AND {has_effective.format("OLD.id")} BEGIN
                {_regroup(_row_content("OLD"), exclude="OLD.id")}
            END""",
        "effcol_entry_update_old": f"""
            CREATE TRIGGER effcol_entry_update_old BEFORE UPDATE OF {entry_columns}
            ON entry WHEN {_NOT_FROZEN} AND {key_changed}
                AND {has_effective.format("OLD.id")} BEGIN
                {_regroup(_row_content("OLD"), exclude="OLD.id")}
            END""",
        "effcol_entry_update_new": f"""
            CREATE TRIGGER effcol_entry_update_new AFTER UPDATE OF {entry_columns}
            ON entry WHEN {_NOT_FROZEN} AND {key_changed}
                AND {has_effective.format("NEW.id")} BEGIN
                UPDATE effective SET mc_version=NEW.mc_version WHERE entry_id=NEW.id;
                {_regroup(_row_content("NEW"))}
            END""",
        "effcol_translation_insert": f"""
            CREATE TRIGGER effcol_translation_insert AFTER INSERT ON translation
            WHEN {_NOT_FROZEN} BEGIN
                UPDATE effective SET upd=NEW.updated_at
                WHERE entry_id=NEW.entry_id AND source=NEW.source;
            END""",
        "effcol_translation_update": f"""
            CREATE TRIGGER effcol_translation_update AFTER UPDATE OF updated_at
            ON translation WHEN {_NOT_FROZEN}
                AND OLD.updated_at IS NOT NEW.updated_at BEGIN
                UPDATE effective SET upd=NEW.updated_at
                WHERE entry_id=NEW.entry_id AND source=NEW.source;
            END""",
        "effcol_translation_delete": f"""
            CREATE TRIGGER effcol_translation_delete AFTER DELETE ON translation
            WHEN {_NOT_FROZEN} BEGIN
                UPDATE effective SET upd=NULL
                WHERE entry_id=OLD.entry_id AND source=OLD.source;
            END""",
    }


# 驗證用的期望值刻意沿用條目清單原本的定義（關聯子查詢），與觸發器的算法不同
_EXPECTED_ROWS = """
    SELECT f.entry_id, e.mc_version,
        EXISTS (
            SELECT 1 FROM entry e2 JOIN effective f2 ON f2.entry_id = e2.id
            WHERE e2.kind = e.kind AND e2.mod_id = e.mod_id AND e2.key = e.key
              AND e2.en_us = e.en_us AND e.en_us <> '' AND e2.mc_version <> e.mc_version
              AND f2.zh_tw <> f.zh_tw),
        t.updated_at
    FROM effective f LEFT JOIN entry e ON e.id = f.entry_id
    LEFT JOIN translation t ON t.entry_id = f.entry_id AND t.source = f.source
"""


def _normalized(sql: str) -> str:
    return " ".join(sql.rstrip().rstrip(";").split())


def is_installed(conn: sqlite3.Connection) -> bool:
    """欄位、索引與觸發器是否已安裝為目前定義（不論內容是否有效）。"""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='effective_columns_schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None or row[0] != SCHEMA_VERSION or not has_columns(conn):
        return False
    stored = dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_schema WHERE (type='trigger' "
            "AND name LIKE 'effcol%') OR (type='index' AND name LIKE 'idx_eff_ver%')"
        ).fetchall()
    )
    expected = {**_trigger_sql(), **_INDEXES}
    return all(
        _normalized(stored.get(name) or "") == _normalized(sql)
        for name, sql in expected.items()
    )


def is_ready(conn: sqlite3.Connection) -> bool:
    """欄位可直接讀取：結構版本相符、標記有效，且不在大量改寫的暫停狀態中。"""
    try:
        row = conn.execute(
            "SELECT "
            "(SELECT value FROM meta WHERE key='effective_columns_schema_version'), "
            "(SELECT value FROM meta WHERE key='effective_columns_valid'), "
            f"EXISTS (SELECT 1 FROM meta WHERE key='{_FROZEN}')"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row == (SCHEMA_VERSION, "1", 0)


def install(conn: sqlite3.Connection) -> None:
    """加入欄位、索引與觸發器並全庫重算（已是目前版本且有效時不做任何寫入）。

    呼叫時不可有進行中的交易；整個安裝在單一交易內完成。
    """
    if is_installed(conn) and is_ready(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        rebuild(conn)
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def rebuild(conn: sqlite3.Connection) -> None:
    """（重新）建立欄位、索引與觸發器、全庫重算並解除暫停（在呼叫端的交易內執行）。"""
    present = {row[1] for row in conn.execute("PRAGMA table_info(effective)")}
    for column, declaration in COLUMNS.items():
        if column not in present:
            conn.execute(f"ALTER TABLE effective ADD COLUMN {column} {declaration}")
    for name, sql in {**_INDEXES, **_trigger_sql()}.items():
        kind = "INDEX" if name in _INDEXES else "TRIGGER"
        conn.execute(f"DROP {kind} IF EXISTS {name}")
        conn.execute(sql)
    recompute(conn)
    conn.execute(f"DELETE FROM meta WHERE key='{_FROZEN}'")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('effective_columns_schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )


# 同內容各條目的生效譯文不只一種的組：組內每個有生效譯文的條目都是「版本不同」
_DIFF_ENTRY_IDS = """
    SELECT e.id FROM (
        SELECT e.kind, e.mod_id, e.key, e.en_us
        FROM entry e JOIN effective f ON f.entry_id = e.id
        WHERE e.en_us <> ''
        GROUP BY e.kind, e.mod_id, e.key, e.en_us
        HAVING COUNT(DISTINCT f.zh_tw) > 1
    ) g JOIN entry e ON e.kind = g.kind AND e.mod_id = g.mod_id
        AND e.key = g.key AND e.en_us = g.en_us
"""


def has_columns(conn: sqlite3.Connection) -> bool:
    """``effective`` 是否已有三個衍生欄位（整批寫入時可直接一併填入）。"""
    present = {row[1] for row in conn.execute("PRAGMA table_info(effective)")}
    return COLUMNS.keys() <= present


def recompute(conn: sqlite3.Connection) -> None:
    """全庫重算三個欄位並標記有效（在呼叫端的交易內執行）。

    只改寫值不同的列：整批寫入時已一併填入版本與更新時間的話，這裡只需讀取比對
    （70 萬條目約 1 秒）。
    """
    version = "(SELECT mc_version FROM entry WHERE id=effective.entry_id)"
    upd = _upd("effective.entry_id", "effective.source")
    conn.execute(
        f"UPDATE effective SET mc_version={version}, upd={upd} "
        f"WHERE mc_version IS NOT {version} OR upd IS NOT {upd}"
    )
    # 不依賴外層列的 IN 子查詢只計算一次
    in_group = f"(entry_id IN ({_DIFF_ENTRY_IDS}))"
    conn.execute(f"UPDATE effective SET diff={in_group} WHERE diff IS NOT {in_group}")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('effective_columns_valid', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = '1'"
    )


@contextmanager
def frozen(conn: sqlite3.Connection) -> Iterator[None]:
    """大量改寫期間暫停觸發器，結束時全庫重算；必須在呼叫端的寫入交易內使用。

    沒有安裝欄位的資料庫（遷移途中等）直接執行，不做任何額外寫入。例外時不清旗標，
    交由呼叫端的交易回復一併還原。
    """
    if not is_installed(conn):
        yield
        return
    conn.execute(
        f"INSERT INTO meta (key, value) VALUES ('{_FROZEN}', '1') "
        "ON CONFLICT(key) DO NOTHING"
    )
    yield
    recompute(conn)
    conn.execute(f"DELETE FROM meta WHERE key='{_FROZEN}'")


def verify(conn: sqlite3.Connection) -> list[tuple]:
    """比對三個欄位與依原本定義的重算；空清單表示一致（只供測試與維護）。"""
    stored = "SELECT entry_id, mc_version, diff, upd FROM effective"
    problems = [
        ("stored", *row)
        for row in conn.execute(f"{stored} EXCEPT SELECT * FROM ({_EXPECTED_ROWS})")
    ]
    problems += [
        ("expected", *row)
        for row in conn.execute(f"SELECT * FROM ({_EXPECTED_ROWS}) EXCEPT {stored}")
    ]
    return problems
