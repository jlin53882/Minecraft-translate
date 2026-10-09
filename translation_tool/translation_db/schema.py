"""schema.py

Mod 翻譯資料庫（SQLite）的資料表結構、來源代碼與連線設定。

資料模型（譯文跟「內容」綁定，版本只是標示內容出現在哪個遊戲版本）：

- ``entry``：某個遊戲版本中的一個可翻譯項目 ``(類型, 版本, 模組, 鍵值) → 原文``。
  「相同內容」以 ``(類型, 模組, 鍵值, 原文)`` 判斷，可橫跨版本。
- ``translation``：掛在 entry 上的譯文，同一個 entry 可有多個來源，每個來源最多一筆。
- ``effective``：每個 entry 依來源優先序選出的「生效譯文」，寫入時維護，讀取不必重算。
- ``history``：手動更新、AI 舊譯文重翻與還原的異動紀錄。
- ``src_change``：掃描／翻譯時發現「鍵值相同但原文已變動」而略過的紀錄。
- ``scan_run``：掃描紀錄。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

KIND_LANG = "lang"
KIND_PATCHOULI = "patchouli"
KINDS = (KIND_LANG, KIND_PATCHOULI)

# 來源代碼（寫入資料庫後不可更動）
SRC_AI = 0
SRC_JAR_TW = 1
SRC_JAR_CN = 2  # 簡中經 OpenCC 轉繁
SRC_SUBTITLE = 3  # 釘宮翻譯組（舊設定別名：町宮字幕組）
SRC_I18N = 4
SRC_CUSTOM = 5
SRC_MANUAL = 6

SOURCE_NAMES: dict[int, str] = {
    SRC_AI: "AI 機翻",
    SRC_JAR_TW: "模組自帶繁中",
    SRC_JAR_CN: "簡中轉繁",
    SRC_SUBTITLE: "釘宮翻譯組",
    SRC_I18N: "i18n 轉換",
    SRC_CUSTOM: "自訂補充",
    SRC_MANUAL: "人工",
}

BUILTIN_SOURCE_NAMES: dict[int, str] = dict(SOURCE_NAMES)

# 使用者在設定「來源優先順序」輸入的新名稱會成為自訂來源：代碼從這裡開始往上配發，
# 登錄在資料庫 meta（custom_sources），寫入後不可更動、不重複使用。
CUSTOM_SOURCE_BASE = 100


def register_source_names(registry: dict[str, int]) -> None:
    """Deprecated compatibility hook; custom names belong to ``SourceCatalog``.

    ``SOURCE_NAMES`` intentionally contains built-ins only. Keeping this
    function avoids breaking downstream imports while preventing a database
    selection from changing labels in another open database or task.
    """
    del registry


# 預設優先序（先者優先）；已校驗（checker 不為空）者永遠最優先
DEFAULT_PRIORITY: tuple[int, ...] = (
    SRC_MANUAL,
    SRC_SUBTITLE,
    SRC_CUSTOM,
    SRC_JAR_TW,
    SRC_I18N,
    SRC_JAR_CN,
    SRC_AI,
)

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    mc_version TEXT NOT NULL,
    mod_id     TEXT NOT NULL,
    key        TEXT NOT NULL,
    en_us      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (kind, mc_version, mod_id, key)
);
CREATE INDEX IF NOT EXISTS idx_entry_content ON entry (kind, mod_id, key, en_us);
CREATE INDEX IF NOT EXISTS idx_entry_mod ON entry (mc_version, mod_id);
CREATE INDEX IF NOT EXISTS idx_entry_mod_id ON entry (mod_id);
CREATE INDEX IF NOT EXISTS idx_entry_en ON entry (en_us);

CREATE TABLE IF NOT EXISTS translation (
    id         INTEGER PRIMARY KEY,
    entry_id   INTEGER NOT NULL REFERENCES entry (id) ON DELETE CASCADE,
    source     INTEGER NOT NULL,
    zh_tw      TEXT NOT NULL DEFAULT '',
    zh_cn      TEXT NOT NULL DEFAULT '',
    checker    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entry_id, source)
);

CREATE TABLE IF NOT EXISTS effective (
    entry_id INTEGER PRIMARY KEY REFERENCES entry (id) ON DELETE CASCADE,
    zh_tw    TEXT NOT NULL,
    source   INTEGER NOT NULL,
    checker  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY,
    entry_id    INTEGER NOT NULL REFERENCES entry (id) ON DELETE CASCADE,
    batch       TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    old_zh_tw   TEXT NOT NULL DEFAULT '',
    new_zh_tw   TEXT NOT NULL DEFAULT '',
    prev_manual TEXT,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_entry ON history (entry_id);
CREATE INDEX IF NOT EXISTS idx_history_batch ON history (batch);

CREATE TABLE IF NOT EXISTS src_change (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    mc_version  TEXT NOT NULL,
    mod_id      TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_en      TEXT NOT NULL,
    new_en      TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (kind, mc_version, mod_id, key, new_en)
);

-- 統計快取：依 meta.data_gen（每次寫入交易遞增）判斷是否過期，跨連線、跨重啟都有效
CREATE TABLE IF NOT EXISTS stat_cache (
    key   TEXT PRIMARY KEY,
    gen   TEXT NOT NULL,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_run (
    id          INTEGER PRIMARY KEY,
    mc_version  TEXT NOT NULL,
    folder      TEXT NOT NULL,
    started_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    stats       TEXT NOT NULL DEFAULT '{}'
);
"""


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """開啟資料庫連線（可跨執行緒使用，呼叫端自行加鎖）。

    ``readonly=True`` 以唯讀 URI 開啟，不會建立檔案也不會寫入。
    """
    p = Path(path)
    if readonly:
        conn = sqlite3.connect(
            f"{p.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    # 數十萬筆條目的掃描／統計查詢：加大頁面快取、暫存放記憶體、用記憶體映射讀取
    conn.execute("PRAGMA cache_size = -131072")  # 128 MB
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")  # 256 MB
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建立資料表（已存在則略過）並記錄 schema 版本。"""
    conn.executescript(_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


DB_EMPTY = "empty"  # 沒有任何資料表（新檔案或 0 位元組的檔案）
DB_VALID = "valid"  # 本功能的資料庫
DB_FOREIGN = "foreign"  # 其他用途的 SQLite（或根本不是 SQLite）
DB_NEWER = "newer"  # 本功能的資料庫，但 schema 版本比本程式新（不認識，不能碰）

_ENTRY_COLUMNS = {"id", "kind", "mc_version", "mod_id", "key", "en_us"}


def classify_database(path: str | Path) -> str:
    """判斷既有檔案是不是本功能的資料庫。

    以**唯讀**連線檢查：不會建立資料表、不會改 journal mode，所以指到其他 SQLite 時不會污染它。
    本功能的資料庫需同時具備：``meta`` 內有 ``schema_version``、``entry`` 資料表且欄位符合。
    """
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return DB_FOREIGN
    try:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not names:
            return DB_EMPTY
        if not {"meta", "entry"} <= names:
            return DB_FOREIGN
        has_version = conn.execute(
            "SELECT 1 FROM meta WHERE key='schema_version'"
        ).fetchone()
        columns = {r[1] for r in conn.execute("PRAGMA table_info(entry)")}
        if not (has_version and _ENTRY_COLUMNS <= columns):
            return DB_FOREIGN
        # 版本也在唯讀階段判定：太新的資料庫在第一次可寫連線（會改 journal mode）之前就拒絕
        return DB_NEWER if stored_schema_version(conn) > SCHEMA_VERSION else DB_VALID
    except sqlite3.Error:  # 不是 SQLite 檔案、meta 結構不同…… 都視為其他用途
        return DB_FOREIGN
    finally:
        conn.close()


def stored_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def rank_sql(priority: tuple[int, ...], col: str = "source") -> str:
    """把來源優先序轉成 SQL ``CASE`` 排序運算式（只含整數，不接受外部字串）。"""
    parts = " ".join(f"WHEN {int(s)} THEN {i}" for i, s in enumerate(priority))
    return f"CASE {col} {parts} ELSE 99 END"
