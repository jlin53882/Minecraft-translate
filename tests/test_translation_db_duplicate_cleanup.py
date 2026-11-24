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


def _insert_alias(
    db: TranslationDB,
    key: str,
    *,
    translation: str = "",
    mod_id: str = "bestiary",
    kind: str = "guide",
    english: str = ENGLISH,
) -> int:
    with db._tx() as conn:
        entry_id = conn.execute(
            "INSERT INTO entry (kind, mc_version, mod_id, key, en_us) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, VERSION, mod_id, key, english),
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


def test_duplicate_preview_scope_filters_module_and_kind(tmp_path):
    db = _db(tmp_path)
    try:
        _insert_alias(db, LEGACY_KEY)
        _insert_alias(db, CURRENT_KEY)
        _insert_alias(
            db,
            "lang/other/_en_us/intro.txt#0",
            mod_id="other-mod",
            kind="patchouli",
        )
        _insert_alias(
            db,
            "lang/other/intro.txt#0",
            mod_id="other-mod",
            kind="patchouli",
        )

        all_plan = db.preview_duplicate_cleanup(VERSION)
        scoped = db.preview_duplicate_cleanup(
            VERSION,
            mod_id="other-mod",
            kind="patchouli",
        )
        assert len(all_plan.candidates) == 2
        assert len(scoped.candidates) == 1
        assert scoped.candidates[0].mod_id == "other-mod"
        assert scoped.candidates[0].kind == "patchouli"
        assert scoped.mod_id == "other-mod"
        assert scoped.kind == "patchouli"
        assert scoped.token != all_plan.token

        applied = db.apply_duplicate_cleanup(
            VERSION,
            scoped.token,
            mod_id="other-mod",
            kind="patchouli",
        )
        assert applied.duplicate_count == 1
        assert db._one("SELECT COUNT(*) FROM entry") == (3,)
        assert len(db.preview_duplicate_cleanup(VERSION).candidates) == 1
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


def test_duplicate_merge_moves_other_originals_to_the_survivor(tmp_path):
    """路徑別名合併：被合併條目的其他原文與譯文併進保留的條目，不重複、不覆蓋。"""
    from translation_tool.translation_db import variants

    db = _db(tmp_path)
    try:
        old_id = _insert_alias(db, LEGACY_KEY)
        current_id = _insert_alias(db, CURRENT_KEY, translation="龍血")
        with db._tx() as conn:
            variants.add_translation(conn, old_id, "Dragon blood v2", SRC_AI, "龍血二")
            variants.add_translation(conn, old_id, "Shared wording", SRC_AI, "共用甲")
            variants.add_translation(
                conn, current_id, "Shared wording", SRC_MANUAL, "共用乙"
            )
            variants.add_translation(conn, old_id, ENGLISH, SRC_AI, "與主原文相同")
        db.apply_duplicate_cleanup(VERSION, db.preview_duplicate_cleanup(VERSION).token)
        survivor = db._one("SELECT id FROM entry")[0]
        rows = db._q(
            "SELECT v.en_us, t.source, t.zh_tw FROM entry_variant v "
            "JOIN variant_translation t ON t.variant_id = v.id "
            "WHERE v.entry_id = ? ORDER BY v.en_us, t.source",
            (survivor,),
        )
        assert rows == [
            ("Dragon blood v2", SRC_AI, "龍血二"),
            ("Shared wording", SRC_AI, "共用甲"),
            ("Shared wording", SRC_MANUAL, "共用乙"),
        ]  # 與主原文相同的不搬；同一份其他原文把缺的來源補上
    finally:
        db.close()


def test_merge_fingerprint_matches_the_legacy_one_without_other_originals(tmp_path):
    """沒有其他原文時，指紋與還沒有變體表時完全相同（舊批次照常可還原）。"""
    db = _db(tmp_path)
    try:
        current_id = _insert_alias(db, CURRENT_KEY, translation="龍血")
        with_tables = db._duplicate_states([current_id])
        with db._tx() as conn:
            conn.execute("DROP TABLE variant_translation")
            conn.execute("DROP TABLE entry_variant")
        assert db._duplicate_states([current_id]) == with_tables
    finally:
        db.close()


def test_rescan_does_not_block_revert_but_post_merge_variant_translation_does(tmp_path):
    """合併後重掃（只更新 last_seen）不阻擋還原；合併後才寫入的其他原文譯文
    （例如按「沿用目前譯文」）要被視為保留條目已改動，不能還原時靜默丟掉。"""
    from translation_tool.translation_db import variants

    def merged(path):
        db = _db(path)
        old_id = _insert_alias(db, LEGACY_KEY)
        _insert_alias(db, CURRENT_KEY, translation="龍血")
        with db._tx() as conn:
            variants.ensure_variant(conn, old_id, "New wording")
        applied = db.apply_duplicate_cleanup(
            VERSION, db.preview_duplicate_cleanup(VERSION).token
        )
        return db, applied, db._one("SELECT id FROM entry")[0]

    (tmp_path / "a").mkdir()
    db, applied, survivor = merged(tmp_path / "a")
    try:
        with db._tx() as conn:  # 重掃：last_seen 更新
            conn.execute(
                "UPDATE entry_variant SET last_seen='2000-01-01 00:00:00' WHERE entry_id=?",
                (survivor,),
            )
            variants.ensure_variant(conn, survivor, "New wording")
        result = db.revert_duplicate_cleanup(applied.batch_id)
        assert result.complete and result.conflicts == []
    finally:
        db.close()

    (tmp_path / "b").mkdir()
    db, applied, survivor = merged(tmp_path / "b")
    try:
        ok, _reason = db.adopt_current_translation_for_variant(
            survivor, "New wording", "龍血"
        )
        assert ok
        result = db.revert_duplicate_cleanup(applied.batch_id)
        assert not result.complete and result.conflicts  # 保留條目已被改動：不還原
        assert db._q("SELECT zh_tw FROM variant_translation") == [("龍血",)]  # 譯文還在
    finally:
        db.close()


def test_merge_keeps_the_survivors_variant_translation_on_same_source_conflict(
    tmp_path,
):
    """同一份其他原文、同一來源、譯文不同：保留條目優先；還原合併後被合併條目拿回自己的。"""
    from translation_tool.translation_db import variants

    db = _db(tmp_path)
    try:
        old_id = _insert_alias(db, LEGACY_KEY)
        current_id = _insert_alias(db, CURRENT_KEY, translation="龍血")
        with db._tx() as conn:
            variants.add_translation(conn, old_id, "Shared", SRC_AI, "甲")
            variants.add_translation(conn, current_id, "Shared", SRC_AI, "乙")
        applied = db.apply_duplicate_cleanup(
            VERSION, db.preview_duplicate_cleanup(VERSION).token
        )
        assert db._q("SELECT zh_tw FROM variant_translation") == [("乙",)]
        assert db.revert_duplicate_cleanup(applied.batch_id).complete
        rows = db._q(
            "SELECT v.entry_id, t.zh_tw FROM entry_variant v "
            "JOIN variant_translation t ON t.variant_id = v.id ORDER BY t.zh_tw"
        )
        assert (old_id, "甲") in rows
    finally:
        db.close()
