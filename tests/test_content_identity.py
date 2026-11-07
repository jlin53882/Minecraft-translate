"""跨版本「內容相同」的單一定義：KubeJS／FTB 只看命名空間，其餘仍要求模組識別相同。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    ScanItem,
    TranslationDB,
    effective_columns,
)
from translation_tool.translation_db.content_identity import (
    PACK_KINDS,
    content_mod_value,
)
from translation_tool.translation_db.models import EntryFilter
from translation_tool.translation_db.schema import SRC_JAR_TW, SRC_MANUAL

PACK_A = "actuallyadditions@All the Mods 10 8.3@40b3569cc156"
PACK_B = "actuallyadditions@All the Mons@bb60be2794c9"


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "ci.db")
    yield d
    d.close()


def _pack(mod, key, en, tw=""):
    return ScanItem(KIND_KUBEJS_LANG, mod, key, en, tw, "")


def _entry(db, version, key, mod):
    return next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )


def _diff_flags(db):
    return {
        (ver, mod): int(diff)
        for ver, mod, diff in db._q(
            "SELECT e.mc_version, e.mod_id, f.diff FROM effective f "
            "JOIN entry e ON e.id = f.entry_id"
        )
    }


def _seed(db, a_text="植物學家的夢想", b_text="植物學家之夢"):
    db.ingest("1.21.1", [_pack(PACK_A, "k", "Sonho", a_text)])
    db.ingest("26.1", [_pack(PACK_B, "k", "Sonho", b_text)])


def test_value_definition_matches_the_sql_definition(db):
    assert content_mod_value(KIND_KUBEJS_LANG, PACK_A) == "actuallyadditions"
    assert content_mod_value(KIND_LANG, "foo") == "foo"
    assert content_mod_value(KIND_LANG, "foo@bar") == "foo@bar"  # 一般內容不截斷
    assert KIND_KUBEJS_LANG in PACK_KINDS
    db.ingest("1.21.1", [_pack(PACK_A, "k", "x", "甲")])
    got = db._q("SELECT kind, mod_id FROM entry WHERE mod_id=?", (PACK_A,))[0]
    from translation_tool.translation_db.content_identity import content_mod_sql

    assert db._q(
        f"SELECT {content_mod_sql('e')} FROM entry e WHERE e.mod_id=?", (PACK_A,)
    )[0][0] == content_mod_value(*got)


def test_diff_column_and_filter_see_the_same_content_across_pack_identities(db):
    _seed(db)
    assert _diff_flags(db) == {("1.21.1", PACK_A): 1, ("26.1", PACK_B): 1}
    for version in ("1.21.1", "26.1"):
        rows = db.list_entries(
            criteria=EntryFilter(version=version, state="diff"), limit=None
        )[0]
        assert [r.key for r in rows] == ["k"]


def test_diff_clears_when_translations_become_equal_across_pack_identities(db):
    _seed(db)
    assert set(_diff_flags(db).values()) == {1}
    eid = _entry(db, "26.1", "k", PACK_B).id
    db.save_manual(eid, "植物學家的夢想", propagate=False)
    assert _diff_flags(db) == {("1.21.1", PACK_A): 0, ("26.1", PACK_B): 0}


def test_plain_lang_with_different_mod_ids_is_still_different_content(db):
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "modA", "k", "Bright", "亮")])
    db.ingest("26.1", [ScanItem(KIND_LANG, "modB", "k", "Bright", "明亮")])
    assert set(_diff_flags(db).values()) == {0}


def test_entry_detail_lists_other_versions_of_the_same_pack_content(db):
    _seed(db)
    detail = db.entry_detail(_entry(db, "1.21.1", "k", PACK_A).id)
    assert detail.versions == ["1.21.1", "26.1"]
    assert [row.mc_version for row in detail.same_key] == ["26.1"]


def test_save_preview_and_sync_reach_the_other_pack_identity(db):
    _seed(db)
    eid = _entry(db, "1.21.1", "k", PACK_A).id
    impacts = db.preview_manual(eid, "新譯文", propagate=True)
    assert {i.mc_version for i in impacts} == {"1.21.1", "26.1"}

    db.save_manual(eid, "新譯文", propagate=True)
    assert _entry(db, "26.1", "k", PACK_B).zh_tw == "新譯文"


def test_write_back_fills_blank_entries_of_the_other_pack_identity(db):
    db.ingest("1.21.1", [_pack(PACK_A, "k", "Sonho", "")])  # 沒有譯文
    from translation_tool.translation_db import WriteBackItem

    db.write_back("26.1", [WriteBackItem(KIND_KUBEJS_LANG, PACK_B, "k", "Sonho", "夢")])
    assert _entry(db, "1.21.1", "k", PACK_A).zh_tw == "夢"


def test_reuse_from_other_versions_matches_across_pack_identities(db):
    db.ingest("1.21.1", [_pack(PACK_A, "k", "Sonho", "夢")])
    db.ingest("26.1", [_pack(PACK_B, "k", "Sonho", "")])
    assert db.reuse_from_other_versions("26.1") == 1
    assert _entry(db, "26.1", "k", PACK_B).zh_tw == "夢"


def test_bulk_review_sync_reaches_the_other_pack_identity(db):
    _seed(db, "夢", "夢")
    plan = db.preview_bulk_review("1.21.1", SRC_JAR_TW, propagate=True)
    result = db.execute_bulk_review(plan) if plan.candidates else None
    assert plan.candidates
    assert result is not None and result.synced == 1
    assert _entry(db, "26.1", "k", PACK_B).review_status == "reviewed"
    assert SRC_MANUAL  # 來源不變，只標審核


def test_old_derived_columns_are_rebuilt_with_the_new_definition(tmp_path):
    """舊版（結構版本 2）建立的資料庫：開啟後觸發器與 diff 以新定義重建。"""
    path = tmp_path / "old.db"
    d = TranslationDB(path)
    d.ingest("1.21.1", [_pack(PACK_A, "k", "Sonho", "甲")])
    d.ingest("26.1", [_pack(PACK_B, "k", "Sonho", "乙")])
    with d._tx() as conn:
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='effective_columns_schema_version'"
        )
        conn.execute("UPDATE effective SET diff=0")
    d.close()

    d = TranslationDB(path)
    try:
        assert d._q(
            "SELECT value FROM meta WHERE key='effective_columns_schema_version'"
        ) == [(effective_columns.SCHEMA_VERSION,)]
        assert _diff_flags(d) == {("1.21.1", PACK_A): 1, ("26.1", PACK_B): 1}
    finally:
        d.close()


def test_overview_counters_use_the_same_content_definition(db):
    """總覽的「相同內容組數」「版本不同組數」：觸發器逐項維護的值要與全庫重算一致。"""
    _seed(db)
    overview = db.overview()
    assert (overview["content"], overview["diff"]) == (1, 1)
    full = db._overview_full()
    assert (overview["content"], overview["diff"]) == (full["content"], full["diff"])

    eid = _entry(db, "26.1", "k", PACK_B).id
    db.save_manual(eid, "植物學家的夢想", propagate=False)  # 兩個版本的譯文一致
    overview = db.overview()
    full = db._overview_full()
    assert (overview["content"], overview["diff"]) == (1, 0)
    assert (overview["content"], overview["diff"]) == (full["content"], full["diff"])
