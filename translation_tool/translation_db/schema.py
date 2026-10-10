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
import time
from pathlib import Path

SCHEMA_VERSION = 5

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
SRC_AI_REPAIR = 7

SOURCE_NAMES: dict[int, str] = {
    SRC_AI: "AI 機翻",
    SRC_JAR_TW: "模組自帶繁中",
    SRC_JAR_CN: "簡中轉繁",
    SRC_SUBTITLE: "釘宮翻譯組",
    SRC_I18N: "i18n 轉換",
    SRC_CUSTOM: "自訂補充",
    SRC_MANUAL: "人工",
    SRC_AI_REPAIR: "AI補譯修正",
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
    review_status TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revision   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (entry_id, source)
);

CREATE TABLE IF NOT EXISTS effective (
    entry_id INTEGER PRIMARY KEY REFERENCES entry (id) ON DELETE CASCADE,
    zh_tw    TEXT NOT NULL,
    source   INTEGER NOT NULL,
    checker  TEXT NOT NULL DEFAULT '',
    review_status TEXT
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
    prev_checker TEXT,
    prev_review_status TEXT,
    new_checker TEXT,
    new_review_status TEXT,
    prev_revision INTEGER,
    new_revision INTEGER,
    source_id   INTEGER,
    repair_prev_zh_tw TEXT,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_entry ON history (entry_id);
CREATE INDEX IF NOT EXISTS idx_history_batch ON history (batch);
CREATE INDEX IF NOT EXISTS idx_history_entry_action_at ON history (entry_id, action, at);

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
    """建立資料表，遞增 migration 先備份並在單一交易中套用。"""
    conn.executescript(_DDL)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_effective_source_review "
            "ON effective (source, review_status)"
        )
        _install_schema_v3_objects(conn)
        conn.commit()
        return
    version = stored_schema_version(conn)
    while version < SCHEMA_VERSION:
        next_version = version + 1
        if next_version == 2:
            _backup_before_review_migration(conn)
        elif next_version == 3:
            _backup_before_time_migration(conn)
        elif next_version == 4:
            _backup_before_source_history_migration(conn)
        elif next_version == 5:
            _backup_before_ai_repair_source_migration(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if next_version == 2:
                _migrate_review_state_v2(conn)
            elif next_version == 3:
                _migrate_time_and_revision_v3(conn)
            elif next_version == 4:
                _migrate_source_history_v4(conn)
            elif next_version == 5:
                _migrate_ai_repair_source_v5(conn)
            conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                (str(next_version),),
            )
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        version = next_version
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_effective_source_review "
        "ON effective (source, review_status)"
    )
    _install_schema_v3_objects(conn)
    conn.commit()


def _backup_before_review_migration(conn: sqlite3.Connection) -> Path | None:
    """Write a consistent side-by-side SQLite backup before the v1→v2 change."""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v2-{time.time_ns()}.bak"
    )
    target = sqlite3.connect(backup_path)
    try:
        conn.backup(target)
    except BaseException:
        target.close()
        backup_path.unlink(missing_ok=True)
        raise
    target.close()
    return backup_path


def _migrate_review_state_v2(conn: sqlite3.Connection) -> None:
    """Add explicit review state while preserving all v1 manual provenance."""
    for table, column in (
        ("translation", "review_status"),
        ("effective", "review_status"),
        ("history", "prev_checker"),
        ("history", "prev_review_status"),
        ("history", "new_checker"),
        ("history", "new_review_status"),
    ):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    conn.execute(
        "UPDATE translation SET review_status='legacy_unknown' WHERE source=?",
        (SRC_MANUAL,),
    )
    conn.execute(
        "UPDATE effective SET review_status=(SELECT review_status FROM translation "
        "WHERE translation.entry_id=effective.entry_id "
        "AND translation.source=effective.source)"
    )
    conn.execute("DELETE FROM stat_cache")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('data_gen', '1') "
        "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
    )


def _backup_before_time_migration(conn: sqlite3.Connection) -> Path | None:
    """Keep a consistent v2 sidecar before adding timestamps and revisions."""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v3-{time.time_ns()}.bak"
    )
    target = sqlite3.connect(backup_path)
    try:
        conn.backup(target)
    except BaseException:
        target.close()
        backup_path.unlink(missing_ok=True)
        raise
    target.close()
    return backup_path


def _migrate_time_and_revision_v3(conn: sqlite3.Connection) -> None:
    """Add unknown-preserving first-seen timestamps and monotonic row revisions."""
    translation_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(translation)")
    }
    if "created_at" not in translation_columns:
        # Old rows remain NULL: updated_at is not evidence of first import.
        conn.execute("ALTER TABLE translation ADD COLUMN created_at TEXT")
    if "revision" not in translation_columns:
        conn.execute(
            "ALTER TABLE translation ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
        )
    history_columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    for column in ("prev_revision", "new_revision"):
        if column not in history_columns:
            conn.execute(f"ALTER TABLE history ADD COLUMN {column} INTEGER")
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM translation").fetchone()[0]
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('translation_revision_seq', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER))",
        (str(max_id),),
    )
    conn.execute("DELETE FROM stat_cache")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('data_gen', '1') "
        "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
    )
    _install_schema_v3_objects(conn)


def _backup_before_source_history_migration(
    conn: sqlite3.Connection,
) -> Path | None:
    """Keep a consistent sidecar before adding source identity to history."""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v4-{time.time_ns()}.bak"
    )
    target = sqlite3.connect(backup_path)
    try:
        conn.backup(target)
    except BaseException:
        target.close()
        backup_path.unlink(missing_ok=True)
        raise
    target.close()
    return backup_path


def _migrate_source_history_v4(conn: sqlite3.Connection) -> None:
    """Record which source row a repair changed so its history can be restored."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    if "source_id" not in columns:
        conn.execute("ALTER TABLE history ADD COLUMN source_id INTEGER")


def _backup_before_ai_repair_source_migration(
    conn: sqlite3.Connection,
) -> Path | None:
    """Keep a sidecar backup before adding repair-source undo metadata."""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v5-{time.time_ns()}.bak"
    )
    target = sqlite3.connect(backup_path)
    try:
        conn.backup(target)
    except BaseException:
        target.close()
        backup_path.unlink(missing_ok=True)
        raise
    target.close()
    return backup_path


def _migrate_ai_repair_source_v5(conn: sqlite3.Connection) -> None:
    """Preserve the prior repair-source value so moving repairs can be undone."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    if "repair_prev_zh_tw" not in columns:
        conn.execute("ALTER TABLE history ADD COLUMN repair_prev_zh_tw TEXT")


def _install_schema_v3_objects(conn: sqlite3.Connection) -> None:
    """Install timestamp and revision triggers for fresh and migrated databases."""
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS translation_insert_timestamp_revision
        AFTER INSERT ON translation
        BEGIN
            INSERT INTO meta (key, value) VALUES ('translation_revision_seq', '1')
            ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1;
            UPDATE translation
            SET created_at=COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
                revision=(SELECT CAST(value AS INTEGER) FROM meta
                          WHERE key='translation_revision_seq')
            WHERE id=NEW.id;
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS translation_update_revision
        AFTER UPDATE ON translation
        WHEN NEW.revision IS OLD.revision
        BEGIN
            INSERT INTO meta (key, value) VALUES ('translation_revision_seq', '1')
            ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1;
            UPDATE translation
            SET revision=(SELECT CAST(value AS INTEGER) FROM meta
                          WHERE key='translation_revision_seq')
            WHERE id=NEW.id;
        END;
        """
    )


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
    ordered = list(priority)
    if SRC_AI_REPAIR not in ordered:
        ordered.insert(0, SRC_AI_REPAIR)
    parts = " ".join(f"WHEN {int(s)} THEN {i}" for i, s in enumerate(ordered))
    return f"CASE {col} {parts} ELSE 99 END"
