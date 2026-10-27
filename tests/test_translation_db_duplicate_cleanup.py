"""掃描迭代造成的指南路徑別名：正規化、預覽、合併、衝突與復原。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.identity import canonical_entry_key
from translation_tool.translation_db.schema import SRC_AI, SRC_MANUAL

VERSION = "1.21.1"
LEGACY_KEY = "lang/bestiary/_en_us/alchemy_0.txt#0"
CURRENT_KEY = "lang/bestiary/alchemy_0.txt#0"
ENGLISH = "After harvesting dragon blood by using a glass."


def _db(tmp_path):
    return TranslationDB(tmp_path / "duplicate.db")


def _insert_alias(db: TranslationDB, key: str, *, translation: str = "") -> int:
    with db._tx() as conn:
        entry_id = conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES ('guide', ?, 'bestiary', ?, ?)",
            (VERSION, key, ENGLISH),
        ).lastrowid
        if translation:
            conn.execute(
                "INSERT INTO translation (entry_id, source, zh_tw, review_status) "
                "VALUES (?, ?, ?, 'reviewed')",
                (entry_id, SRC_MANUAL, translation),
            )
        db._refresh(conn, [entry_id])
    return int(entry_id)


def test_canonical_key_matches_legacy_locale_directory_aliases():
    assert canonical_entry_key("guide", LEGACY_KEY) == canonical_entry_key(
        "guide", CURRENT_KEY
    )
    assert canonical_entry_key("lang", "item.foo.name") == "item.foo.name"


def test_scan_reuses_a_unique_legacy_alias_instead_of_creating_a_third_entry(tmp_path):
    db = _db(tmp_path)
    try:
        db.ingest(VERSION, [ScanItem("guide", "bestiary", LEGACY_KEY, ENGLISH)])
        result = db.ingest(
            VERSION,
            [ScanItem("guide", "bestiary", CURRENT_KEY, ENGLISH, zh_tw="龍血")],
        )
        assert result.new_entries == 0
        assert db._one(
            "SELECT COUNT(*) FROM entry WHERE mc_version=? AND mod_id=?",
            (VERSION, "bestiary"),
        ) == (1,)
        assert db._one(
            "SELECT zh_tw FROM effective WHERE entry_id=(SELECT id FROM entry)"
        ) == ("龍血",)
    finally:
        db.close()


def test_scan_reuses_one_entry_across_multiple_aliases_in_one_batch(tmp_path):
    db = _db(tmp_path)
    try:
        result = db.ingest(
            VERSION,
            [
                ScanItem("guide", "bestiary", LEGACY_KEY, ENGLISH),
                ScanItem("guide", "bestiary", CURRENT_KEY, ENGLISH),
                ScanItem(
                    "guide",
                    "bestiary",
                    "lang/bestiary/en_us_0/alchemy_0.txt#0",
                    ENGLISH,
                ),
            ],
        )
        assert result.new_entries == 1
        assert db._one(
            "SELECT COUNT(*) FROM entry WHERE mc_version=? AND mod_id=?",
            (VERSION, "bestiary"),
        ) == (1,)
    finally:
        db.close()


def test_safe_duplicate_merge_preserves_translation_and_can_be_reverted(tmp_path):
    db = _db(tmp_path)
    try:
        old_id = _insert_alias(db, LEGACY_KEY)
        current_id = _insert_alias(db, CURRENT_KEY, translation="龍血")
        candidates = db.duplicate_candidates(VERSION)
        assert len(candidates) == 1
        assert candidates[0].safe is True
        assert {entry.entry_id for entry in candidates[0].entries} == {
            old_id,
            current_id,
        }

        preview = db.preview_duplicate_cleanup(VERSION)
        assert (preview.group_count, preview.duplicate_count) == (1, 1)
        applied = db.apply_duplicate_cleanup(VERSION, preview.token)
        assert applied.batch_id and applied.duplicate_count == 1
        assert db._one("SELECT COUNT(*) FROM entry WHERE mc_version=?", (VERSION,)) == (
            1,
        )
        assert db._one("SELECT zh_tw FROM effective") == ("龍血",)

        result = db.revert_duplicate_cleanup(applied.batch_id)
        assert (result.restored_groups, result.pending_groups, result.complete) == (
            1,
            0,
            True,
        )
        assert db._one("SELECT COUNT(*) FROM entry WHERE mc_version=?", (VERSION,)) == (
            2,
        )
        assert db._one("SELECT COUNT(*) FROM translation") == (1,)
    finally:
        db.close()


def test_merge_retain_all_content_and_revert_restores_exact_rows(tmp_path):
    db = _db(tmp_path)
    try:
        old_id = _insert_alias(db, LEGACY_KEY)
        _insert_alias(db, CURRENT_KEY, translation="龍血")
        db.save_manual(old_id, "龍血", propagate=False)
        with db._tx() as conn:
            conn.execute(
                "INSERT INTO translation "
                "(entry_id, source, zh_tw, review_status) VALUES (?, ?, ?, 'reviewed')",
                (old_id, SRC_AI, "Dragon blood"),
            )
            db._refresh(conn, [old_id])
        before = {
            table: [list(row) for row in db._q(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("entry", "translation", "effective", "history", "src_change")
        }

        applied = db.apply_duplicate_cleanup(
            VERSION, db.preview_duplicate_cleanup(VERSION).token
        )
        assert db._one("SELECT COUNT(*) FROM entry") == (1,)
        assert db._one("SELECT COUNT(*) FROM translation") == (2,)
        assert db._one("SELECT COUNT(*) FROM history") == (1,)
        assert db._one("SELECT zh_tw FROM effective") == ("龍血",)
        survivor_id = db._one("SELECT id FROM entry")[0]
        assert {row[0] for row in db._q("SELECT entry_id FROM translation")} == {
            survivor_id
        }

        result = db.revert_duplicate_cleanup(applied.batch_id)
        assert result.complete and result.conflicts == []
        after = {
            table: [list(row) for row in db._q(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("entry", "translation", "effective", "history", "src_change")
        }
        assert after == before
    finally:
        db.close()


def test_revert_refuses_to_remove_a_survivor_changed_after_merge(tmp_path):
    db = _db(tmp_path)
    try:
        _insert_alias(db, LEGACY_KEY)
        _insert_alias(db, CURRENT_KEY, translation="龍血")
        applied = db.apply_duplicate_cleanup(
            VERSION, db.preview_duplicate_cleanup(VERSION).token
        )
        survivor_id = db._one("SELECT id FROM entry WHERE mc_version=?", (VERSION,))[0]
        db.save_manual(survivor_id, "後續人工修改", propagate=False)

        result = db.revert_duplicate_cleanup(applied.batch_id)
        assert result.restored_groups == 0
        assert result.pending_groups == 1
        assert result.complete is False
        assert len(result.conflicts) == 1
        assert db._one("SELECT COUNT(*) FROM entry WHERE mc_version=?", (VERSION,)) == (
            1,
        )
        assert db._one("SELECT zh_tw FROM effective") == ("後續人工修改",)
        assert db._one(
            "SELECT reverted_at FROM duplicate_merge_batch WHERE id=?",
            (applied.batch_id,),
        ) == (None,)
    finally:
        db.close()


def test_translation_conflict_is_reported_and_not_merged(tmp_path):
    db = _db(tmp_path)
    try:
        _insert_alias(db, LEGACY_KEY, translation="龍血")
        _insert_alias(db, CURRENT_KEY, translation="龍之血")
        preview = db.preview_duplicate_cleanup(VERSION)
        assert preview.group_count == 0
        assert preview.skipped["translation_conflict"] == 1
        assert db._one("SELECT COUNT(*) FROM entry") == (2,)
    finally:
        db.close()


def test_different_original_is_reported_and_not_merged(tmp_path):
    db = _db(tmp_path)
    try:
        _insert_alias(db, LEGACY_KEY)
        _insert_alias(db, CURRENT_KEY)
        with db._tx() as conn:
            conn.execute(
                "UPDATE entry SET en_us=? WHERE key=?",
                ("A different source paragraph.", CURRENT_KEY),
            )
        preview = db.preview_duplicate_cleanup(VERSION)
        assert preview.group_count == 0
        assert preview.skipped["different_original"] == 1
        assert db._one("SELECT COUNT(*) FROM entry") == (2,)
    finally:
        db.close()


def test_preview_token_rejects_a_new_duplicate_before_apply(tmp_path):
    db = _db(tmp_path)
    try:
        _insert_alias(db, LEGACY_KEY)
        _insert_alias(db, CURRENT_KEY)
        preview = db.preview_duplicate_cleanup(VERSION)
        _insert_alias(db, "lang/bestiary/en_us_0/alchemy_0.txt#0")
        with pytest.raises(ValueError, match="重新掃描與預覽"):
            db.apply_duplicate_cleanup(VERSION, preview.token)
        assert db._one("SELECT COUNT(*) FROM entry") == (3,)
    finally:
        db.close()
