"""entry_activity.py

每個條目最近一次人工活動的時間：由 ``history`` 的插入觸發器維護，讓「最近人工活動」排序
不必對整個版本逐筆彙總歷史。

- ``entry_activity``：``(條目, 版本, 最近活動時間)``；只收 :data:`ACTIVITY_ACTIONS`
  的歷史紀錄，時間取 ``MAX(history.at)``，與排序原本的定義相同。沒有這些紀錄的條目不在表內。
- 條目刪除時由外鍵串聯刪除；條目改版本時同步版本欄。``history`` 正常只會新增
  （條目刪除時串聯刪除）；被修改或刪除單筆紀錄時（例如外部 SQL 修復），
  更新與刪除觸發器會依剩餘紀錄重算該條目的最近活動時間。

``meta`` 的 ``activity_schema_version`` 記錄結構版本，``activity_valid='1'`` 表示內容可用；
沒有這兩個值的資料庫（較舊的唯讀檔案等）由讀取端改用原本的歷史彙總查詢。
"""

from __future__ import annotations

import sqlite3

ACTIVITY_SCHEMA_VERSION = "1"
# 「人工活動」包含的歷史動作（排序、清單欄位與時間篩選的「全部」共用）
ACTIVITY_ACTIONS = ("manual", "review", "batch_replace", "revert", "batch_revert")
ACTIONS_SQL = "(" + ",".join(f"'{action}'" for action in ACTIVITY_ACTIONS) + ")"

_TABLES = """
CREATE TABLE IF NOT EXISTS entry_activity (
    entry_id   INTEGER PRIMARY KEY REFERENCES entry (id) ON DELETE CASCADE,
    mc_version TEXT NOT NULL,
    last_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_ver_at
    ON entry_activity (mc_version, last_at DESC, entry_id);
"""


def _recompute(entry_id: str) -> str:
    """依目前的歷史重算單一條目的最近活動時間（沒有符合的紀錄就移除該列）。"""
    return f"""
            DELETE FROM entry_activity WHERE entry_id={entry_id};
            INSERT INTO entry_activity (entry_id, mc_version, last_at)
            SELECT e.id, e.mc_version, MAX(h.at) FROM entry e
            JOIN history h ON h.entry_id=e.id
            WHERE e.id={entry_id} AND h.action IN {ACTIONS_SQL}
            HAVING MAX(h.at) IS NOT NULL;"""


_TRIGGERS = {
    "activity_history_insert": f"""
        CREATE TRIGGER activity_history_insert AFTER INSERT ON history
        WHEN NEW.action IN {ACTIONS_SQL} BEGIN
            INSERT INTO entry_activity (entry_id, mc_version, last_at)
            SELECT id, mc_version, NEW.at FROM entry WHERE id=NEW.entry_id
            ON CONFLICT(entry_id) DO UPDATE SET last_at=max(last_at, excluded.last_at);
        END""",
    "activity_history_delete": f"""
        CREATE TRIGGER activity_history_delete AFTER DELETE ON history
        WHEN OLD.action IN {ACTIONS_SQL} BEGIN{_recompute("OLD.entry_id")}
        END""",
    "activity_history_update": f"""
        CREATE TRIGGER activity_history_update
        AFTER UPDATE OF entry_id, action, at ON history
        WHEN OLD.action IN {ACTIONS_SQL} OR NEW.action IN {ACTIONS_SQL} BEGIN{_recompute("OLD.entry_id")}{_recompute("NEW.entry_id")}
        END""",
    "activity_entry_version": """
        CREATE TRIGGER activity_entry_version AFTER UPDATE OF mc_version ON entry
        WHEN OLD.mc_version IS NOT NEW.mc_version BEGIN
            UPDATE entry_activity SET mc_version=NEW.mc_version WHERE entry_id=NEW.id;
        END""",
}

_FULL_ROWS = f"""
    SELECT h.entry_id, e.mc_version, MAX(h.at) FROM history h
    JOIN entry e ON e.id = h.entry_id
    WHERE h.action IN {ACTIONS_SQL}
    GROUP BY h.entry_id
"""


def _normalized(sql: str) -> str:
    return " ".join(sql.rstrip().rstrip(";").split())


def is_installed(conn: sqlite3.Connection) -> bool:
    """資料表與觸發器是否已安裝為目前定義（不論內容是否有效）。"""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='activity_schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None or row[0] != ACTIVITY_SCHEMA_VERSION:
        return False
    stored = dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_schema WHERE name='entry_activity' "
            "OR (type='trigger' AND name LIKE 'activity%')"
        ).fetchall()
    )
    if "entry_activity" not in stored:
        return False
    return all(
        _normalized(stored.get(name) or "") == _normalized(sql)
        for name, sql in _TRIGGERS.items()
    )


def is_ready(conn: sqlite3.Connection) -> bool:
    """可直接用於排序：結構版本相符且內容標記有效。"""
    try:
        row = conn.execute(
            "SELECT "
            "(SELECT value FROM meta WHERE key='activity_schema_version'), "
            "(SELECT value FROM meta WHERE key='activity_valid')"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row == (ACTIVITY_SCHEMA_VERSION, "1")


def install(conn: sqlite3.Connection) -> None:
    """建立資料表與觸發器並由歷史填入（已是目前版本且有效時不做任何寫入）。

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
    """（重新）建立資料表與觸發器並由歷史重新填入（在呼叫端的交易內執行）。"""
    # executescript 會先提交交易，所以逐句執行
    for statement in _TABLES.split(";"):
        if statement.strip():
            conn.execute(statement)
    for name, sql in _TRIGGERS.items():
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(sql)
    conn.execute("DELETE FROM entry_activity")
    conn.execute(
        f"INSERT INTO entry_activity (entry_id, mc_version, last_at) {_FULL_ROWS}"
    )
    for key, value in (
        ("activity_schema_version", ACTIVITY_SCHEMA_VERSION),
        ("activity_valid", "1"),
    ):
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def verify(conn: sqlite3.Connection) -> list[tuple]:
    """比對資料表與由歷史重算的結果；空清單表示一致（只供測試與維護）。"""
    stored = "SELECT entry_id, mc_version, last_at FROM entry_activity"
    problems = [
        ("stored", *row)
        for row in conn.execute(f"{stored} EXCEPT SELECT * FROM ({_FULL_ROWS})")
    ]
    problems += [
        ("expected", *row)
        for row in conn.execute(f"SELECT * FROM ({_FULL_ROWS}) EXCEPT {stored}")
    ]
    return problems
