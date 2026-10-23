"""stat_summary.py

全庫統計摘要：由觸發器在每次寫入時遞增維護的小表，讓總覽統計不必掃描全部條目。

- ``stat_src``：每個 ``(版本, 生效來源, 審核狀態)`` 的條目數。沒有生效譯文的條目記為
  來源 ``-1``、審核狀態 ``''``。審核狀態的推導與全庫重算查詢相同。
- ``stat_counter``：``content``（相同內容組數）、``mods``（模組數）、``no_source``
  （沒有原文的條目數）、``diff``（同內容在不同版本譯文不一致的組數）。

``meta`` 的 ``stats_schema_version`` 記錄摘要結構版本，``stats_valid='1'`` 表示摘要可用；
沒有這兩個值的資料庫（較舊的唯讀檔案等）由讀取端改用全庫重算。

大量改寫（整批重建生效譯文等）在同一交易內設 ``stats_frozen`` 暫停觸發器，
做完後以全庫重算重新填入摘要（:func:`frozen`）。不可對 ``effective`` 使用
``INSERT OR REPLACE``：它刪除舊列時不會觸發刪除觸發器，摘要會失準。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from translation_tool.translation_db.sql_helpers import effective_review_sql

STAT_SCHEMA_VERSION = "2"
NO_SOURCE = -1  # 沒有生效譯文的條目在 stat_src 的來源值
COUNTER_NAMES = ("content", "mods", "no_source", "diff")

_FROZEN = "stats_frozen"
_NOT_FROZEN = f"NOT EXISTS (SELECT 1 FROM meta WHERE key='{_FROZEN}')"
_CONTENT_COLUMNS = ("kind", "mod_id", "key", "en_us")
_ENTRY_KEY_COLUMNS = ("mc_version", *_CONTENT_COLUMNS)

_TABLES = """
CREATE TABLE IF NOT EXISTS stat_src (
    mc_version TEXT NOT NULL,
    source     INTEGER NOT NULL,
    review     TEXT NOT NULL,
    cnt        INTEGER NOT NULL,
    PRIMARY KEY (mc_version, source, review)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS stat_counter (
    name TEXT PRIMARY KEY,
    v    INTEGER NOT NULL
) WITHOUT ROWID;
"""


def _review(alias: str) -> str:
    """生效列的審核狀態（與全庫重算的 CASE 相同）。"""
    return effective_review_sql(alias, True, else_unreviewed=True)


def _bump(version: str, source: str, review: str, guard: str = "1") -> str:
    return (
        "INSERT INTO stat_src (mc_version, source, review, cnt) "
        f"SELECT {version}, {source}, {review}, 1 WHERE {guard} "
        "ON CONFLICT(mc_version, source, review) DO UPDATE SET cnt = cnt + 1;"
    )


def _counter(name: str, delta: str) -> str:
    return f"UPDATE stat_counter SET v = v + ({delta}) WHERE name='{name}';"


def _siblings(entry_id: str) -> str:
    """同內容其他條目的生效譯文：筆數、最小值、最大值（原文空白者不計）。"""
    joins = " AND ".join(f"e2.{c}=e.{c}" for c in _CONTENT_COLUMNS)
    return (
        "(SELECT COUNT(*) n, MIN(f2.zh_tw) mn, MAX(f2.zh_tw) mx FROM entry e "
        f"JOIN entry e2 ON {joins} AND e2.id<>e.id "
        "JOIN effective f2 ON f2.entry_id=e2.id "
        f"WHERE e.id={entry_id} AND e.en_us<>'')"
    )


def _diff_if_added(zh_tw: str, entry_id: str) -> str:
    """加入一筆譯文 ``zh_tw`` 會讓同內容組從一致變成不一致時為 1。"""
    return (
        "COALESCE((SELECT CASE WHEN n>0 AND mn=mx AND mn<>"
        f"{zh_tw} THEN 1 ELSE 0 END FROM {_siblings(entry_id)}),0)"
    )


def _diff_if_replaced(old: str, new: str, entry_id: str) -> str:
    def differs(zh_tw: str) -> str:
        return f"(n>0 AND (mn<>mx OR mn<>{zh_tw}))"

    return f"COALESCE((SELECT {differs(new)} - {differs(old)} FROM {_siblings(entry_id)}),0)"


def _unique(row: str, columns: tuple[str, ...]) -> str:
    same = " AND ".join(f"x.{c}={row}.{c}" for c in columns)
    return f"NOT EXISTS (SELECT 1 FROM entry x WHERE {same} AND x.id<>{row}.id)"


def _entry_counts(row: str, sign: str) -> str:
    """條目加入（sign='+'）或移出（sign='-'）時的摘要異動。"""
    effective = f"(SELECT f.%s FROM effective f WHERE f.entry_id={row}.id)"
    source = f"COALESCE({effective % 'source'},{NO_SOURCE})"
    review = f"COALESCE((SELECT {_review('f')} FROM effective f WHERE f.entry_id={row}.id),'')"
    has_effective = f"EXISTS (SELECT 1 FROM effective WHERE entry_id={row}.id)"
    # 新條目還沒有生效譯文：先判斷存在與否，省掉同內容譯文的彙總查詢
    diff = (
        f"CASE WHEN {has_effective} "
        f"THEN {_diff_if_added(effective % 'zh_tw', row + '.id')} ELSE 0 END"
    )
    if sign == "+":
        bucket = _bump(f"{row}.mc_version", source, review)
    else:
        bucket = (
            f"UPDATE stat_src SET cnt = cnt - 1 WHERE mc_version={row}.mc_version "
            f"AND source={source} AND review={review};"
        )
    return f"""
        {bucket}
        {_counter("diff", f"{sign}{diff}")}
        {_counter("content", f"{sign}{_unique(row, _CONTENT_COLUMNS)}")}
        {_counter("mods", f"{sign}{_unique(row, ('mod_id',))}")}
        {_counter("no_source", f"{sign}({row}.en_us='')")}"""


def _entry_version(entry_id: str) -> str:
    return f"(SELECT mc_version FROM entry WHERE id={entry_id})"


def _trigger_sql() -> dict[str, str]:
    key_changed = (
        "("
        + ",".join(f"OLD.{c}" for c in _ENTRY_KEY_COLUMNS)
        + ") IS NOT ("
        + ",".join(f"NEW.{c}" for c in _ENTRY_KEY_COLUMNS)
        + ")"
    )
    entry_columns = ", ".join(_ENTRY_KEY_COLUMNS)
    review_changed = f"(OLD.source<>NEW.source OR {_review('OLD')}<>{_review('NEW')})"
    entry_exists = "EXISTS (SELECT 1 FROM entry WHERE id=OLD.entry_id)"
    return {
        "stat_entry_insert": (
            f"CREATE TRIGGER stat_entry_insert AFTER INSERT ON entry "
            f"WHEN {_NOT_FROZEN} BEGIN {_entry_counts('NEW', '+')} END"
        ),
        # 刪除條目時 SQLite 先刪條目、再串聯刪除生效列：在這裡一次扣掉條目與它的生效譯文
        "stat_entry_delete": (
            f"CREATE TRIGGER stat_entry_delete BEFORE DELETE ON entry "
            f"WHEN {_NOT_FROZEN} BEGIN {_entry_counts('OLD', '-')} END"
        ),
        "stat_entry_update_old": (
            f"CREATE TRIGGER stat_entry_update_old BEFORE UPDATE OF {entry_columns} "
            f"ON entry WHEN {_NOT_FROZEN} AND {key_changed} "
            f"BEGIN {_entry_counts('OLD', '-')} END"
        ),
        "stat_entry_update_new": (
            f"CREATE TRIGGER stat_entry_update_new AFTER UPDATE OF {entry_columns} "
            f"ON entry WHEN {_NOT_FROZEN} AND {key_changed} "
            f"BEGIN {_entry_counts('NEW', '+')} END"
        ),
        "stat_effective_insert": f"""
            CREATE TRIGGER stat_effective_insert AFTER INSERT ON effective
            WHEN {_NOT_FROZEN} BEGIN
                UPDATE stat_src SET cnt = cnt - 1 WHERE source={NO_SOURCE} AND review=''
                    AND mc_version={_entry_version("NEW.entry_id")};
                {_bump(_entry_version("NEW.entry_id"), "NEW.source", _review("NEW"), "EXISTS (SELECT 1 FROM entry WHERE id=NEW.entry_id)")}
                {_counter("diff", _diff_if_added("NEW.zh_tw", "NEW.entry_id"))}
            END""",
        # 條目本身被刪除時（串聯刪除）已由 stat_entry_delete 扣掉，這裡不重複計算
        "stat_effective_delete": f"""
            CREATE TRIGGER stat_effective_delete AFTER DELETE ON effective
            WHEN {_NOT_FROZEN} AND {entry_exists} BEGIN
                UPDATE stat_src SET cnt = cnt - 1 WHERE source=OLD.source
                    AND review={_review("OLD")}
                    AND mc_version={_entry_version("OLD.entry_id")};
                {_bump(_entry_version("OLD.entry_id"), str(NO_SOURCE), "''")}
                {_counter("diff", "-" + _diff_if_added("OLD.zh_tw", "OLD.entry_id"))}
            END""",
        "stat_effective_update": f"""
            CREATE TRIGGER stat_effective_update
            AFTER UPDATE OF zh_tw, source, review_status ON effective
            WHEN {_NOT_FROZEN} BEGIN
                UPDATE stat_src SET cnt = cnt - 1 WHERE source=OLD.source
                    AND review={_review("OLD")}
                    AND mc_version={_entry_version("OLD.entry_id")} AND {review_changed};
                {_bump(_entry_version("NEW.entry_id"), "NEW.source", _review("NEW"), review_changed)}
                {_counter("diff", _diff_if_replaced("OLD.zh_tw", "NEW.zh_tw", "NEW.entry_id"))}
            END""",
        # 程式本身不會把生效列移到別的條目；外部 SQL 這樣改時摘要無法逐項調整，
        # 標成無效，讀取改用原本的查詢，下次可寫開啟時重建
        "stat_effective_move": f"""
            CREATE TRIGGER stat_effective_move AFTER UPDATE OF entry_id ON effective
            WHEN {_NOT_FROZEN} AND OLD.entry_id IS NOT NEW.entry_id BEGIN
                INSERT INTO meta (key, value) VALUES ('stats_valid', '0')
                ON CONFLICT(key) DO UPDATE SET value = '0';
            END""",
    }


_FULL_SOURCE_ROWS = f"""
    SELECT e.mc_version, COALESCE(f.source, {NO_SOURCE}),
           CASE WHEN f.entry_id IS NULL THEN '' ELSE {_review("f")} END, COUNT(*)
    FROM entry e LEFT JOIN effective f ON f.entry_id = e.id
    GROUP BY 1, 2, 3
"""
_FULL_COUNTERS = {
    "content": "SELECT COUNT(*) FROM (SELECT 1 FROM entry GROUP BY kind, mod_id, key, en_us)",
    "mods": "SELECT COUNT(DISTINCT mod_id) FROM entry",
    "no_source": "SELECT COUNT(*) FROM entry WHERE en_us = ''",
    "diff": """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM entry e JOIN effective f ON f.entry_id = e.id
            WHERE e.en_us <> ''
            GROUP BY e.kind, e.mod_id, e.key, e.en_us
            HAVING COUNT(DISTINCT f.zh_tw) > 1
        )""",
}
_FULL_COUNTER_ROWS = " UNION ALL ".join(
    f"SELECT '{name}', ({sql})" for name, sql in _FULL_COUNTERS.items()
)


def _normalized(sql: str) -> str:
    return " ".join(sql.rstrip().rstrip(";").split())


def is_installed(conn: sqlite3.Connection) -> bool:
    """摘要表與觸發器是否已安裝為目前定義（不論內容是否有效）。"""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='stats_schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None or row[0] != STAT_SCHEMA_VERSION:
        return False
    stored = dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_schema "
            "WHERE type IN ('table','trigger') AND name LIKE 'stat%'"
        ).fetchall()
    )
    if not {"stat_src", "stat_counter"} <= stored.keys():
        return False
    return all(
        _normalized(stored.get(name) or "") == _normalized(sql)
        for name, sql in _trigger_sql().items()
    )


def is_ready(conn: sqlite3.Connection) -> bool:
    """摘要可直接讀取：已安裝、標記有效，且目前不在大量改寫的暫停狀態中。"""
    try:
        row = conn.execute(
            "SELECT "
            "(SELECT value FROM meta WHERE key='stats_schema_version'), "
            "(SELECT value FROM meta WHERE key='stats_valid'), "
            f"EXISTS (SELECT 1 FROM meta WHERE key='{_FROZEN}')"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row == (STAT_SCHEMA_VERSION, "1", 0)


def install(conn: sqlite3.Connection) -> None:
    """建立摘要表與觸發器並以全庫重算填入（已是目前版本且有效時不做任何寫入）。

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
    """（重新）建立摘要表與觸發器、重算內容並解除暫停（在呼叫端的交易內執行）。"""
    for statement in _TABLES.split(";"):
        if statement.strip():
            conn.execute(statement)
    for name, sql in _trigger_sql().items():
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(sql)
    reseed(conn)
    conn.execute(f"DELETE FROM meta WHERE key='{_FROZEN}'")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('stats_schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (STAT_SCHEMA_VERSION,),
    )


def reseed(conn: sqlite3.Connection) -> None:
    """以全庫重算覆寫摘要並標記有效（在呼叫端的交易內執行）。"""
    conn.execute("DELETE FROM stat_src")
    conn.execute("DELETE FROM stat_counter")
    conn.execute(
        f"INSERT INTO stat_src (mc_version, source, review, cnt) {_FULL_SOURCE_ROWS}"
    )
    conn.execute(f"INSERT INTO stat_counter (name, v) {_FULL_COUNTER_ROWS}")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('stats_valid', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = '1'"
    )


@contextmanager
def frozen(conn: sqlite3.Connection) -> Iterator[None]:
    """大量改寫期間暫停觸發器，結束時重算摘要；必須在呼叫端的寫入交易內使用。

    沒有安裝摘要的資料庫（遷移途中等）直接執行，不做任何額外寫入。例外時不清旗標，
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
    reseed(conn)
    conn.execute(f"DELETE FROM meta WHERE key='{_FROZEN}'")


def source_rows(conn: sqlite3.Connection) -> list[list]:
    """``(版本, 來源或 None, 審核狀態或 None, 筆數)``，排序與全庫重算查詢相同。"""
    return [
        list(row)
        for row in conn.execute(
            f"SELECT mc_version, NULLIF(source, {NO_SOURCE}), NULLIF(review, ''), cnt "
            "FROM stat_src WHERE cnt <> 0 ORDER BY mc_version, source, review"
        )
    ]


def counters(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        name: int(v) for name, v in conn.execute("SELECT name, v FROM stat_counter")
    }


def verify(conn: sqlite3.Connection) -> list[tuple]:
    """比對摘要與全庫重算（約 1.4 秒／70 萬條目，只供測試與維護）；空清單表示一致。"""
    stored = "SELECT mc_version, source, review, cnt FROM stat_src WHERE cnt <> 0"
    problems = [
        ("stored", *row)
        for row in conn.execute(f"{stored} EXCEPT SELECT * FROM ({_FULL_SOURCE_ROWS})")
    ]
    problems += [
        ("expected", *row)
        for row in conn.execute(f"SELECT * FROM ({_FULL_SOURCE_ROWS}) EXCEPT {stored}")
    ]
    expected = dict(conn.execute(_FULL_COUNTER_ROWS).fetchall())
    actual = counters(conn)
    problems += [
        ("counter", name, actual.get(name), value)
        for name, value in expected.items()
        if actual.get(name) != value
    ]
    return problems
