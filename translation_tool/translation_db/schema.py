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

KEEP_REVIEW_FLAG = "keep_review_on_text_change"  # 交易內暫時存在的 meta 旗標
SCHEMA_VERSION = 8

KIND_LANG = "lang"
KIND_PATCHOULI = "patchouli"
KIND_GUIDE = "guide"  # 指南（.md／.txt 文字檔，依段落切成條目）
KIND_ADVANCEMENT = "advancement"  # 進度的顯示文字（data/<模組>/advancement(s)/）

# DB kind → translation cache bucket. The LM prompt is selected from this
# bucket, so all producers of DB-backed translation items must use this map.
_CACHE_TYPE_BY_KIND = {
    KIND_LANG: KIND_LANG,
    KIND_PATCHOULI: KIND_PATCHOULI,
    KIND_GUIDE: "md",
    KIND_ADVANCEMENT: KIND_PATCHOULI,
}


def cache_type_for_kind(kind: str | None) -> str:
    """回傳資料庫條目種類共用的翻譯快取分桶。"""
    return _CACHE_TYPE_BY_KIND.get(str(kind or ""), KIND_PATCHOULI)


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
-- Mod DB 已審核術語候選依模組與版本快取；最終術語上限在 Python 衝突排除後套用。
CREATE INDEX IF NOT EXISTS idx_entry_kind_mod_key ON entry (kind, mod_id, key);
CREATE INDEX IF NOT EXISTS idx_entry_mod ON entry (mc_version, mod_id);
CREATE INDEX IF NOT EXISTS idx_entry_list_order
    ON entry (mc_version, mod_id, kind, key, id);
CREATE INDEX IF NOT EXISTS idx_entry_mod_id ON entry (mod_id);
-- 版本內依編號排序（兩段式排序的第二段：沒有活動時間或更新時間的條目）
CREATE INDEX IF NOT EXISTS idx_entry_version ON entry (mc_version);
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

-- 資料庫清理批次：刪除前的整批快照（JSON），供還原
CREATE TABLE IF NOT EXISTS cleanup_batch (
    id          TEXT PRIMARY KEY,
    mc_version  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    entry_count INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    reverted_at TEXT
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
    """處理目前函數負責的資料與流程。"""
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
        _install_smart_review_objects(conn)
        _install_derived_tables(conn)
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
        elif next_version == 6:
            _backup_before_independent_review_migration(conn)
        elif next_version == 7:
            _backup_before_smart_review_migration(conn)
        elif next_version == 8:
            _backup_before_smart_review_draft_migration(conn)
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
            elif next_version == 6:
                _migrate_independent_review_v6(conn)
            elif next_version == 7:
                _migrate_smart_review_v7(conn)
            elif next_version == 8:
                _migrate_smart_review_v8(conn)
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
    _install_smart_review_objects(conn)
    _install_derived_tables(conn)


def _install_derived_tables(conn: sqlite3.Connection) -> None:
    """安裝並填入觸發器維護的衍生資料（可隨時重算，不需先備份）。

    全庫統計摘要（``stat_summary``）、條目最近人工活動（``entry_activity``）、
    ``effective`` 的衍生欄位（``effective_columns``）與條目品質旗標（``quality_flags``）。
    """
    # 這些模組引用本模組的來源代碼，延後載入以免循環匯入
    from translation_tool.translation_db import (
        change_tracker,
        effective_columns,
        entry_activity,
        quality_flags,
        stat_summary,
    )

    stat_summary.install(conn)
    entry_activity.install(conn)
    effective_columns.install(conn)
    quality_flags.install(conn)
    # 最後安裝：前面各模組的回填寫入不應被當成外部寫入
    change_tracker.install(conn)


def _backup_before_review_migration(conn: sqlite3.Connection) -> Path | None:
    """在審核狀態遷移前建立一致的 SQLite 旁側備份。"""
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
    """加入明確審核狀態並保留 v1 人工來源資訊。"""
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
    """在加入時間戳與修訂版本前保留一致的 v2 旁側備份。"""
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
    """加入可保留未知值的首次時間戳與遞增資料列修訂版。"""
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
    """在歷史資料加入來源身分前建立一致的旁側備份。"""
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
    """記錄修復變更的來源資料列，讓歷史可以還原。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    if "source_id" not in columns:
        conn.execute("ALTER TABLE history ADD COLUMN source_id INTEGER")


def _backup_before_ai_repair_source_migration(
    conn: sqlite3.Connection,
) -> Path | None:
    """在加入 AI 修復復原資訊前建立旁側備份。"""
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
    """保存先前的修復來源值，讓移動修復可以復原。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    if "repair_prev_zh_tw" not in columns:
        conn.execute("ALTER TABLE history ADD COLUMN repair_prev_zh_tw TEXT")


def _backup_before_independent_review_migration(
    conn: sqlite3.Connection,
) -> Path | None:
    """在把審核移回原始來源前建立一致的旁側備份。"""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v6-{time.time_ns()}.bak"
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


def _migrate_independent_review_v6(conn: sqlite3.Connection) -> None:
    """把人工審核資料移回提供相同文字的來源。"""
    if "review_status" not in {
        row[1] for row in conn.execute("PRAGMA table_info(translation)")
    }:
        return
    saved = conn.execute("SELECT value FROM meta WHERE key='priority'").fetchone()
    order = [
        int(part)
        for part in (saved[0] if saved else "").split(",")
        if part.strip().lstrip("-").isdigit()
    ]
    order += [code for code in DEFAULT_PRIORITY if code not in order]
    rank = {code: index for index, code in enumerate(order)}
    matches: dict[int, list[tuple[int, int, int]]] = {}
    for manual_id, entry_id, other_id, other_source in conn.execute(
        "SELECT m.id, m.entry_id, o.id, o.source FROM translation m "
        "JOIN translation o ON o.entry_id=m.entry_id AND o.source<>? "
        "AND o.zh_tw=m.zh_tw "
        "WHERE m.source=? AND m.review_status='reviewed' "
        # 只有「由審核建立」的人工列才能確定是同一份文字：從沒手動儲存過，
        # 且有一筆審核紀錄當時還沒有人工列
        # 這個條目的歷史只能有審核（任何其他操作都可能改寫過人工列），而且
        # 審核寫入時的版本就是人工列現在的版本（之後沒有被動過）
        "AND NOT EXISTS (SELECT 1 FROM history h "
        "WHERE h.entry_id=m.entry_id AND h.action<>'review') "
        "AND EXISTS (SELECT 1 FROM history h WHERE h.entry_id=m.entry_id "
        "AND h.action='review' AND h.new_zh_tw=m.zh_tw AND h.prev_manual IS NULL "
        "AND h.new_revision=m.revision)",
        (SRC_MANUAL, SRC_MANUAL),
    ):
        matches.setdefault(manual_id, []).append(
            (rank.get(other_source, len(rank)), other_source, other_id)
        )
    batch = f"migrate-v6-{time.time_ns()}"
    for manual_id, candidates in matches.items():
        _rank, target_source, target_id = min(candidates)
        manual = conn.execute(
            "SELECT entry_id, zh_tw, checker FROM translation WHERE id=?",
            (manual_id,),
        ).fetchone()
        conn.execute(
            "UPDATE translation SET review_status='reviewed' WHERE id=?",
            (target_id,),
        )
        conn.execute("DELETE FROM translation WHERE id=?", (manual_id,))
        conn.execute(
            "INSERT INTO history (entry_id, batch, actor, action, old_zh_tw, "
            "new_zh_tw, prev_manual, note, source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                manual[0],
                batch,
                manual[2] or "",
                "review_migrate",
                manual[1],
                manual[1],
                manual[1],
                "資料庫遷移：審核狀態移回原來源，原人工列已移除",
                target_source,
            ),
        )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('effective_priority_version', 'stale') "
        "ON CONFLICT(key) DO UPDATE SET value='stale'"
    )


def _backup_before_smart_review_migration(
    conn: sqlite3.Connection,
) -> Path | None:
    """在智慧校對資料庫遷移前建立一致的旁側備份。"""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v7-{time.time_ns()}.bak"
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


def _migrate_smart_review_v7(conn: sqlite3.Connection) -> None:
    """安裝智慧校對持久化結構並保留既有資料。"""
    _install_smart_review_objects(conn)


_SMART_REVIEW_DDL = (
    """
    CREATE TABLE IF NOT EXISTS smart_review_run (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        scope_version TEXT,
        requested_limit INTEGER NOT NULL,
        batch_size INTEGER NOT NULL,
        scope_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        selected_count INTEGER NOT NULL DEFAULT 0,
        processed_count INTEGER NOT NULL DEFAULT 0,
        valid_count INTEGER NOT NULL DEFAULT 0,
        invalid_count INTEGER NOT NULL DEFAULT 0,
        request_error_count INTEGER NOT NULL DEFAULT 0,
        metrics TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_run_status
        ON smart_review_run (status, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_run_order
        ON smart_review_run (started_at DESC, id DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS smart_review_candidate (
        run_id TEXT NOT NULL REFERENCES smart_review_run (id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        entry_id INTEGER NOT NULL REFERENCES entry (id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        mc_version TEXT NOT NULL,
        mod_id TEXT NOT NULL,
        entry_key TEXT NOT NULL,
        en_us TEXT NOT NULL,
        current_zh_tw TEXT NOT NULL,
        effective_source INTEGER,
        review_status TEXT,
        effective_revision INTEGER,
        manual_zh_tw TEXT,
        manual_checker TEXT,
        manual_review_status TEXT,
        manual_revision INTEGER,
        context_json TEXT NOT NULL DEFAULT '[]',
        decision TEXT,
        suggested_zh_tw TEXT,
        issue_types_json TEXT NOT NULL DEFAULT '[]',
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL,
        response_json TEXT,
        draft_zh_tw TEXT,
        validation_error TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        applied_batch TEXT,
        applied_at TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, ordinal),
        UNIQUE (run_id, entry_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_candidate_status
        ON smart_review_candidate (run_id, status, ordinal)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_candidate_entry
        ON smart_review_candidate (entry_id, run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_context_key
        ON entry (kind, mod_id, key, mc_version, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_named_order
        ON entry (
            (CASE WHEN kind='lang' AND (
                key LIKE 'item.%' OR key LIKE 'block.%' OR
                key LIKE 'entity.%' OR key LIKE 'biome.%' OR
                key LIKE 'effect.%' OR key LIKE 'fluid.%' OR
                key LIKE 'enchantment.%' OR key LIKE 'dimension.%' OR
                key LIKE 'painting.%' OR key LIKE 'trim_pattern.%' OR
                key LIKE 'instrument.%' OR key LIKE 'banner.%'
            ) THEN 0 ELSE 1 END),
            mc_version, mod_id, kind, key, id
        )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_glossary_order
        ON entry (kind, mod_id, mc_version, id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_smart_review_same_text_order
        ON entry (en_us, mod_id, kind, key, id)
    """,
)


def _install_smart_review_objects(conn: sqlite3.Connection) -> None:
    """在新舊資料庫中安裝智慧校對執行與候選資料表。"""
    # 每條 DDL 都交給目前交易執行，避免 executescript 隱式提交外層 migration。
    for statement in _SMART_REVIEW_DDL:
        conn.execute(statement)


def _backup_before_smart_review_draft_migration(
    conn: sqlite3.Connection,
) -> Path | None:
    """在加入智慧校對草稿欄位前建立一致的旁側備份。"""
    main = next(
        (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
        "",
    )
    if not main:
        return None
    source_path = Path(main)
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-schema-v8-{time.time_ns()}.bak"
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


def _migrate_smart_review_v8(conn: sqlite3.Connection) -> None:
    """在 v7 候選上加入範圍與可編輯草稿保存。"""
    run_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(smart_review_run)")
    }
    if "scope_json" not in run_columns:
        conn.execute(
            "ALTER TABLE smart_review_run ADD COLUMN scope_json TEXT NOT NULL DEFAULT '{}'"
        )
    candidate_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(smart_review_candidate)")
    }
    if "draft_zh_tw" not in candidate_columns:
        conn.execute("ALTER TABLE smart_review_candidate ADD COLUMN draft_zh_tw TEXT")


def _install_schema_v3_objects(conn: sqlite3.Connection) -> None:
    """為新建與遷移資料庫安裝時間戳及修訂版本觸發器。"""
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
    # 非人工來源的譯文文字被改掉、審核狀態卻沒有一起改時，舊的「已審核」不再成立
    # 審核、修復與還原流程（_tx(keep_review=True)）自己決定審核狀態並寫進歷史快照，不受此觸發器影響
    review_trigger = f"""
        CREATE TRIGGER translation_text_change_clears_review
        AFTER UPDATE OF zh_tw ON translation
        WHEN NEW.source <> 6 AND NEW.zh_tw IS NOT OLD.zh_tw
             AND OLD.review_status = 'reviewed'
             AND NEW.review_status IS OLD.review_status
             AND NOT EXISTS (SELECT 1 FROM meta WHERE key='{KEEP_REVIEW_FLAG}')
        BEGIN
            UPDATE translation SET review_status=NULL WHERE id=NEW.id;
        END;
        """
    existing = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='trigger' "
        "AND name='translation_text_change_clears_review'"
    ).fetchone()

    def normalize(sql: str) -> str:
        """正規化 trigger SQL 以比較既有結構是否相同。"""
        return " ".join(sql.rstrip().rstrip(";").split())

    if existing is not None and normalize(existing[0]) == normalize(review_trigger):
        return
    conn.execute("DROP TRIGGER IF EXISTS translation_text_change_clears_review")
    conn.execute(review_trigger)


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
        # AI 修復是暫存的修復來源：沒有人工譯文時要蓋過原 AI，
        # 但不能蓋過使用者剛儲存的人工修改，否則畫面會看起來像儲存失敗。
        try:
            manual_index = ordered.index(SRC_MANUAL)
        except ValueError:
            manual_index = -1
        ordered.insert(manual_index + 1 if manual_index >= 0 else 0, SRC_AI_REPAIR)
    parts = " ".join(f"WHEN {int(s)} THEN {i}" for i, s in enumerate(ordered))
    return f"CASE {col} {parts} ELSE 99 END"
