"""change_tracker.py

外部 SQL 寫入的變更序號：``meta.ext_change_seq``。

快取世代要能偵測所有資料變更。程式自己的寫入都經過 ``TranslationDB._tx()``，由它遞增
``data_gen``；但外部 SQLite 程式（或舊版 app）直接改資料時不會遞增，持久化的統計快取就會
沿用舊結果。本模組在會影響統計與清單的資料表上安裝觸發器：當寫入不在 ``_tx()`` 內
（``meta`` 沒有 ``tx_active``）時，遞增 ``ext_change_seq``。``_tx()`` 內的寫入由 ``data_gen``
負責，觸發器只多做一次 ``meta`` 主鍵查詢。

追蹤的資料表：``entry``、``translation``、``effective``、``history``、``src_change``；
衍生欄位（``qflags``、``effective.diff`` 等）的更新不算，因為它們隨來源資料一起變。
"""

from __future__ import annotations

import sqlite3

CHANGE_TRACKER_VERSION = "1"
TX_ACTIVE = "tx_active"  # _tx() 內暫時存在的 meta 旗標
SEQ_KEY = "ext_change_seq"

_NOT_IN_TX = f"NOT EXISTS (SELECT 1 FROM meta WHERE key='{TX_ACTIVE}')"
_BUMP = (
    f"INSERT INTO meta (key, value) VALUES ('{SEQ_KEY}', '1') "
    "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1;"
)

# 資料表 -> 會觸發 UPDATE 觸發器的欄位（None＝任何欄位）
_TRACKED = {
    "entry": "mc_version, kind, mod_id, key, en_us, created_at",
    "translation": None,
    "effective": "entry_id, zh_tw, source, checker, review_status",
    "history": None,
    "src_change": None,
}


def _trigger_sql() -> dict[str, str]:
    triggers = {}
    for table, columns in _TRACKED.items():
        update = f"UPDATE OF {columns}" if columns else "UPDATE"
        for name, event in (
            ("insert", "INSERT"),
            ("update", update),
            ("delete", "DELETE"),
        ):
            trigger = f"chg_{table}_{name}"
            triggers[trigger] = (
                f"CREATE TRIGGER {trigger} AFTER {event} ON {table} "
                f"WHEN {_NOT_IN_TX} BEGIN {_BUMP} END"
            )
    return triggers


_TRIGGERS = _trigger_sql()


def _normalized(sql: str) -> str:
    return " ".join(sql.rstrip().rstrip(";").split())


def is_installed(conn: sqlite3.Connection) -> bool:
    """觸發器是否已安裝為目前定義。"""
    try:
        row = conn.execute(
            "SELECT (SELECT value FROM meta WHERE key='change_tracker_version'), "
            f"(SELECT value FROM meta WHERE key='{SEQ_KEY}')"
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None or row[0] != CHANGE_TRACKER_VERSION or row[1] is None:
        return False
    stored = dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_schema "
            "WHERE type='trigger' AND name GLOB 'chg_*'"
        ).fetchall()
    )
    return all(
        _normalized(stored.get(name) or "") == _normalized(sql)
        for name, sql in _TRIGGERS.items()
    )


def install(conn: sqlite3.Connection) -> None:
    """安裝觸發器與序號（已是目前定義時不做任何寫入）；呼叫時不可有進行中的交易。"""
    if is_installed(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for name, sql in _TRIGGERS.items():
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute(sql)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("change_tracker_version", CHANGE_TRACKER_VERSION),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, '0') ON CONFLICT(key) DO NOTHING",
            (SEQ_KEY,),
        )
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
