"""已匯入資料庫的升級：舊資料不需要重新匯入就能套用新的內容相同定義，中文來源條目重掃後補上繁中。"""

from __future__ import annotations

import json
import sqlite3

from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.content_identity import INDEX_NAME
from translation_tool.translation_db.scanner import ScanOptions, scan_folder_generator
from translation_tool.translation_db.schema import KIND_FTB_QUEST_LANG

OLD = "ftbquests@All the Mods 10 8.2@14cb3797fa50"
NEW = "ftbquests@All the Mods 10 8.5@39fa0c9d7170"


def _ftb(mod, rel, en, tw=""):
    return ScanItem(KIND_FTB_QUEST_LANG, mod, f"{rel}|lang:quest.A.title", en, tw)


def _downgrade_to_old_definitions(path) -> None:
    """還原成舊版的內容相同索引（鍵值未正規化）與舊的衍生資料結構版本。"""
    conn = sqlite3.connect(path)
    conn.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    conn.execute(
        f"CREATE INDEX {INDEX_NAME} ON entry (kind, "
        "(CASE WHEN kind IN ('kubejs_lang','ftb_quest_lang') AND instr(mod_id, '@') > 0 "
        "THEN substr(mod_id, 1, instr(mod_id, '@') - 1) ELSE mod_id END), key, en_us)"
    )
    conn.execute(
        "UPDATE meta SET value='4' WHERE key='effective_columns_schema_version'"
    )
    conn.commit()
    conn.close()


def test_existing_database_gets_the_new_content_definition_on_open(tmp_path):
    path = tmp_path / "old.db"
    db = TranslationDB(path)
    db.ingest("1.21.1", [_ftb(OLD, "__root__.snbt", "Hi", "舊譯")])
    db.review_manual(
        db._one("SELECT id FROM entry WHERE mod_id=?", (OLD,))[0],
        expected_zh_tw="舊譯",
        propagate=False,
    )
    db.ingest("1.21.1", [_ftb(NEW, "chapter.snbt", "Hi")])
    db.close()

    _downgrade_to_old_definitions(path)

    db = TranslationDB(path)  # 只開啟，不重新匯入
    try:
        stored = db._one("SELECT sql FROM sqlite_master WHERE name=?", (INDEX_NAME,))[0]
        assert "|lang:" in stored  # 舊索引已被換成新定義
        rows = db.list_cross_version_differences("1.21.1")
        assert [(r.mod_id, r.reference.mod_id) for r in rows] == [(NEW, OLD)]
    finally:
        db.close()


def test_rescan_fills_zh_tw_for_chinese_only_entries_already_in_the_database(tmp_path):
    lang = tmp_path / "pack" / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    (lang / "zh_cn.json").write_text(
        json.dumps({"hurt": "幽灵受伤", "plain": "Untranslated"}), encoding="utf-8"
    )
    db = TranslationDB(tmp_path / "t.db")
    try:
        opts = ScanOptions("1.20.1", include_pack_content=True)
        list(scan_folder_generator(db, tmp_path / "pack", opts, workers=1))
        # 模擬舊版匯入的結果：條目與簡中原文都在，但沒有任何譯文
        with db._tx() as conn:
            conn.execute("DELETE FROM translation")
            conn.execute("DELETE FROM effective")
        assert db._one("SELECT COUNT(*) FROM effective")[0] == 0

        list(scan_folder_generator(db, tmp_path / "pack", opts, workers=1))
        rows = dict(
            db._conn.execute(
                "SELECT e.key, ef.zh_tw FROM entry e "
                "LEFT JOIN effective ef ON ef.entry_id = e.id "
                "WHERE e.kind='kubejs_lang'"
            ).fetchall()
        )
        entries = db._one("SELECT COUNT(*) FROM entry WHERE kind='kubejs_lang'")[0]
    finally:
        db.close()
    assert entries == 2  # 重掃沿用既有條目，沒有產生重複
    assert rows["hurt"] == "幽靈受傷"
    assert rows["plain"] is None  # 沒翻的英文值不會被當成譯文
