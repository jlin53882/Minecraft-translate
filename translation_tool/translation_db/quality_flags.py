"""quality_flags.py

每個條目的翻譯品質旗標（``entry.qflags``），讓條目清單的品質篩選沿部分索引直接分頁，
不必對整個版本逐列執行 ``quality.matches_quality``。

- 每個位元對應一個品質狀態（:data:`BITS`），值與 ``matches_quality(en_us, 生效譯文,
  QualityFilter(status=狀態), key)`` 完全相同；「token 不一致」是方向與類別都為「全部」
  時的結果。``unknown_source``（原文為空）直接以 ``en_us=''`` 篩選，不佔位元。
- ``qflags IS NULL`` 表示「待重算」：新條目預設為 NULL；觸發器在生效譯文的新增、刪除、
  文字改變，以及條目的原文或 key 改變時把該條目設回 NULL。觸發器只做標記，品質判斷由
  Python 在寫入交易提交前執行（:func:`settle`），所以旗標與 ``quality.py`` 的定義永遠一致。
- 讀取端先確認沒有待重算的條目（:func:`is_ready`）；有的話（舊版程式等外部寫入者、
  交易中途）改用原本的逐列判斷，結果仍然正確，只是較慢。

``meta`` 的 ``quality_schema_version`` 記錄旗標定義版本（``quality.py`` 的判斷規則改變時
遞增，開啟資料庫時全部重算），``quality_valid='1'`` 表示內容可用。
"""

from __future__ import annotations

import sqlite3

from translation_tool.translation_db.models import QualityFilter
from translation_tool.translation_db.quality import matches_quality

# 旗標定義版本；``quality.py``／``guide_format.py`` 的判斷結果改變時遞增
# （``tests/test_translation_db_quality_flags.py`` 的規則快照會在規則改變時失敗提醒）。
# 6：空白分隔的多元件行與多層巢狀巨集行視為獨立元件行
# 5：獨立成行的 <link> 與含分號的顯示文字也可翻譯
# 4：<link;分類;顯示文字> 的顯示文字可翻譯
# 3：獨立成行元件不含文字、段落空行不計文字首尾的空行
# 2：指南條目的結構標記計入「格式不一致／一致」
SCHEMA_VERSION = "6"
BITS = {
    "missing_translation": 1,
    "whitespace": 2,
    "mismatch": 4,
    "consistent": 8,
    "species_name": 16,
    "species_format": 32,
}
# 每批重算的條目數：初次安裝 70 萬條目時分批提交，中斷後下次開啟從剩下的待重算條目接續
SETTLE_CHUNK = 20_000

_FILTERS = {status: QualityFilter(status=status) for status in BITS}
# 與條目清單預設排序（模組、類型、key、編號）相同的欄位順序，分頁不必另外排序
_INDEXES = {
    **{
        f"idx_qflags_{status}": (
            f"CREATE INDEX idx_qflags_{status} ON entry "
            f"(mc_version, mod_id, kind, key, id) WHERE qflags & {bit}"
        )
        for status, bit in BITS.items()
    },
    "idx_qflags_pending": (
        "CREATE INDEX idx_qflags_pending ON entry (id) WHERE qflags IS NULL"
    ),
}
_INDEX_NAMES = ",".join(f"'{name}'" for name in _INDEXES)
_PENDING = "UPDATE entry SET qflags=NULL WHERE id={} AND qflags IS NOT NULL;"
_TRIGGERS = {
    "qflags_effective_insert": f"""
        CREATE TRIGGER qflags_effective_insert AFTER INSERT ON effective BEGIN
            {_PENDING.format("NEW.entry_id")}
        END""",
    "qflags_effective_delete": f"""
        CREATE TRIGGER qflags_effective_delete AFTER DELETE ON effective BEGIN
            {_PENDING.format("OLD.entry_id")}
        END""",
    "qflags_effective_update": f"""
        CREATE TRIGGER qflags_effective_update AFTER UPDATE OF entry_id, zh_tw
        ON effective WHEN (OLD.entry_id, OLD.zh_tw) IS NOT (NEW.entry_id, NEW.zh_tw)
        BEGIN
            {_PENDING.format("OLD.entry_id")}
            {_PENDING.format("NEW.entry_id")}
        END""",
    "qflags_entry_update": f"""
        CREATE TRIGGER qflags_entry_update AFTER UPDATE OF key, en_us, kind ON entry
        WHEN (OLD.key, OLD.en_us, OLD.kind) IS NOT (NEW.key, NEW.en_us, NEW.kind) BEGIN
            {_PENDING.format("NEW.id")}
        END""",
    # 一般新增時欄位預設為 NULL；還原清理快照等帶入舊值的新增也要重算
    "qflags_entry_insert": f"""
        CREATE TRIGGER qflags_entry_insert AFTER INSERT ON entry
        WHEN NEW.qflags IS NOT NULL BEGIN
            {_PENDING.format("NEW.id")}
        END""",
}
_ROWS = (
    "SELECT e.id, e.key, e.en_us, f.zh_tw, e.kind FROM entry e "
    "LEFT JOIN effective f ON f.entry_id = e.id"
)


def compute(
    key: str, en_us: str | None, zh_tw: str | None, kind: str | None = None
) -> int:
    """一個條目的品質旗標（原文、生效譯文、key 與種類決定）。"""
    source, translated = en_us or "", zh_tw or ""
    flags = 0
    for status in ("missing_translation", "whitespace"):
        if matches_quality(source, translated, _FILTERS[status], key, kind=kind):
            flags |= BITS[status]
    # 兩者都要求原文與譯文非空，且 token 計數不同即不一致、相同即一致：互斥
    if matches_quality(source, translated, _FILTERS["mismatch"], key, kind=kind):
        flags |= BITS["mismatch"]
    elif matches_quality(source, translated, _FILTERS["consistent"], key, kind=kind):
        flags |= BITS["consistent"]
    if source:
        for status in ("species_name", "species_format"):
            if matches_quality(source, translated, _FILTERS[status], key, kind=kind):
                flags |= BITS[status]
    return flags


def _reference(
    key: str, en_us: str | None, zh_tw: str | None, kind: str | None = None
) -> int:
    """驗證用：逐一呼叫每個狀態的判斷，不依賴 :func:`compute` 的互斥與略過。"""
    return sum(
        bit
        for status, bit in BITS.items()
        if matches_quality(en_us or "", zh_tw or "", _FILTERS[status], key, kind=kind)
    )


def filter_sql(quality: QualityFilter) -> str | None:
    """品質篩選對應的旗標條件；沒有對應位元的狀態（原文為空）回傳 None。"""
    status = "mismatch" if quality.status == "all" else quality.status
    bit = BITS.get(status)
    return None if bit is None else f"e.qflags & {bit}"


def is_exact(quality: QualityFilter) -> bool:
    """旗標條件就是完整結果（不必再逐列判斷）：方向與類別都是「全部」的狀態。"""
    return (
        filter_sql(quality) is not None
        and quality.direction == "all"
        and quality.token_category == "all"
    )


def _normalized(sql: str) -> str:
    return " ".join(sql.rstrip().rstrip(";").split())


def has_column(conn: sqlite3.Connection) -> bool:
    return "qflags" in {row[1] for row in conn.execute("PRAGMA table_info(entry)")}


def is_installed(conn: sqlite3.Connection) -> bool:
    """欄位、索引與觸發器是否已安裝為目前定義（不論內容是否有效）。"""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='quality_schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None or row[0] != SCHEMA_VERSION or not has_column(conn):
        return False
    stored = dict(
        conn.execute(
            "SELECT name, sql FROM sqlite_schema WHERE (type='trigger' "
            "AND name LIKE 'qflags%') OR (type='index' AND name LIKE 'idx_qflags%')"
        ).fetchall()
    )
    expected = {**_TRIGGERS, **_INDEXES}
    return all(
        _normalized(stored.get(name) or "") == _normalized(sql)
        for name, sql in expected.items()
    )


def _valid(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT (SELECT value FROM meta WHERE key='quality_schema_version'), "
        "(SELECT value FROM meta WHERE key='quality_valid')"
    ).fetchone()
    return row == (SCHEMA_VERSION, "1")


def is_ready(conn: sqlite3.Connection) -> bool:
    """旗標可直接篩選：定義版本相符、標記有效，且沒有待重算的條目。

    在寫入交易中呼叫時，同一交易內改動、尚未重算的條目也算待重算。
    """
    try:
        row = conn.execute(
            "SELECT "
            "(SELECT value FROM meta WHERE key='quality_schema_version'), "
            "(SELECT value FROM meta WHERE key='quality_valid'), "
            "EXISTS (SELECT 1 FROM entry WHERE qflags IS NULL), "
            "(SELECT COUNT(*) FROM sqlite_schema WHERE type='index' "
            f"AND name IN ({_INDEX_NAMES}))"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row == (SCHEMA_VERSION, "1", 0, len(_INDEXES))


def install(conn: sqlite3.Connection) -> None:
    """加入欄位、索引與觸發器並重算（已是目前版本且有效時只補算待重算的條目）。

    呼叫時不可有進行中的交易。結構在一個交易內安裝；重算分批提交，中斷後下次開啟
    從剩下的待重算條目接續，讀取端在重算完成前改用逐列判斷。
    """
    if not (is_installed(conn) and _valid(conn)):
        conn.execute("BEGIN IMMEDIATE")
        try:
            _install_objects(conn)
            conn.execute("UPDATE entry SET qflags=NULL WHERE qflags IS NOT NULL")
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
    while conn.execute("SELECT 1 FROM entry WHERE qflags IS NULL LIMIT 1").fetchone():
        conn.execute("BEGIN IMMEDIATE")
        try:
            settle(conn, limit=SETTLE_CHUNK)
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


def _install_objects(conn: sqlite3.Connection) -> None:
    if not has_column(conn):
        conn.execute("ALTER TABLE entry ADD COLUMN qflags INTEGER")
    for name, sql in {**_INDEXES, **_TRIGGERS}.items():
        kind = "INDEX" if name in _INDEXES else "TRIGGER"
        conn.execute(f"DROP {kind} IF EXISTS {name}")
        conn.execute(sql)
    for key, value in (
        ("quality_schema_version", SCHEMA_VERSION),
        ("quality_valid", "1"),
    ):
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def rebuild(conn: sqlite3.Connection) -> None:
    """（重新）建立欄位、索引與觸發器並全部重算（在呼叫端的交易內執行）。"""
    _install_objects(conn)
    conn.execute("UPDATE entry SET qflags=NULL WHERE qflags IS NOT NULL")
    settle(conn)


def settle(conn: sqlite3.Connection, *, limit: int | None = None) -> int:
    """重算待重算條目的旗標，回傳處理筆數（在呼叫端的寫入交易內執行）。

    ``limit`` 是本次最多處理的筆數；None 表示處理到沒有待重算的條目為止。
    """
    done = 0
    while limit is None or done < limit:
        size = SETTLE_CHUNK if limit is None else min(SETTLE_CHUNK, limit - done)
        rows = conn.execute(
            f"{_ROWS} WHERE e.qflags IS NULL LIMIT {int(size)}"
        ).fetchall()
        if not rows:
            break
        conn.executemany(
            "UPDATE entry SET qflags=? WHERE id=?",
            [
                (compute(key, en_us, zh_tw, kind), entry_id)
                for entry_id, key, en_us, zh_tw, kind in rows
            ],
        )
        done += len(rows)
    return done


def verify(conn: sqlite3.Connection) -> list[tuple]:
    """比對旗標與逐一判斷的重算，回傳 ``(條目, 儲存值, 期望值)``；空清單表示一致。

    待重算（NULL）的條目也列為不一致。只供測試與維護（70 萬條目約數秒）。
    """
    cursor = conn.execute(_ROWS.replace("e.id,", "e.id, e.qflags,", 1))
    return [
        (entry_id, stored, expected)
        for entry_id, stored, key, en_us, zh_tw, kind in cursor
        if stored != (expected := _reference(key, en_us, zh_tw, kind))
    ]
