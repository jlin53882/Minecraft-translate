"""「原文已變動」不把只差大小寫或空白的原文算進去。"""

from __future__ import annotations

import sqlite3

from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.schema import KIND_LANG


def _src_changes(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM src_change").fetchone()[0]
    finally:
        conn.close()


def test_case_or_whitespace_only_change_is_not_a_source_change(tmp_path):
    path = tmp_path / "t.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "End Automata Core")])
    report = db.ingest(
        "1.21.1", [ScanItem(KIND_LANG, "m", "k", "End  automata core ", "終界核心")]
    )
    assert _src_changes(path) == 0
    assert report.en_changed == 0
    # 沿用既有原文，譯文照常補上
    entry, _ = db.list_entries("1.21.1", query="k", query_field="key")
    assert entry[0].en_us == "End Automata Core"
    assert entry[0].zh_tw == "終界核心"
    db.close()


def test_real_source_change_is_still_recorded(tmp_path):
    path = tmp_path / "t.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "End Automata Core")])
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "Ender Automata Core")])
    assert _src_changes(path) == 1
    db.close()


def test_old_trivial_source_changes_are_purged_once_on_open(tmp_path):
    path = tmp_path / "t.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "k", "A")])
    db.close()
    conn = sqlite3.connect(path)
    for old, new in (("End Core", "End core"), ("Real", "Changed")):
        conn.execute(
            "INSERT INTO src_change (kind, mc_version, mod_id, key, old_en, new_en) "
            "VALUES ('lang','1.21.1','m',?,?,?)",
            (old, old, new),
        )
    conn.commit()
    conn.close()

    TranslationDB(path).close()
    assert _src_changes(path) == 1  # 只剩真的改過的那筆
