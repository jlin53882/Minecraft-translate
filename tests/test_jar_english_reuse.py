"""KubeJS 語系條目：原文（整合包自帶的非英文翻譯）不同，但 jar 英文原文相同時視為同一句。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb import version_diff_dialog as vdd
from translation_tool.translation_db import (
    KIND_KUBEJS_LANG,
    KIND_LANG,
    ScanItem,
    TranslationDB,
)

PACK_OLD = "actuallyadditions@All the Mods 10 8.3@aaaaaaaaaaaa"
PACK_NEW = "actuallyadditions@All the Mods 10 8.6@bbbbbbbbbbbb"
PACK_OTHER = "actuallyadditions@Another Pack@cccccccccccc"
KEY = "booklet.actuallyadditions.chapter.craftingIngs"
OLD_PT = "Ingredientes de Criação"
NEW_PT = "Ingredientes de Fabricação"


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "jar_en.db")
    yield database
    database.close()


def _jar(db, version, en, key=KEY, mod="actuallyadditions"):
    db.ingest(version, [ScanItem(KIND_LANG, mod, key, en, "", "", None)])


def _pack(mod, en, tw="", key=KEY, locale="pt_br"):
    return ScanItem(
        KIND_KUBEJS_LANG,
        mod,
        key,
        en,
        tw,
        "",
        None,
        f"assets/actuallyadditions/lang/{locale}.json",
        f"lang:{locale}:{key}",
        "h",
        "lang-json",
        "pack",
        locale,
    )


def _entry(db, version, mod, key=KEY):
    return next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )


def _reviewed_pack(db, version, mod, en, text):
    db.ingest(version, [_pack(mod, en, text)])
    db.review_manual(_entry(db, version, mod).id, expected_zh_tw=text, propagate=False)


def _import(db, version, items):
    ids: set[int] = set()
    db.ingest(version, items, entry_ids_out=ids)
    return ids


def test_import_reuses_when_only_the_pack_translation_changed(db):
    """使用者情境：8.3 → 8.6 只把葡萄牙文 Criação 改成 Fabricação，jar 英文都是同一句。"""
    _jar(db, "1.21.1", "Crafting Ingredients")
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, NEW_PT)])

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)

    assert (result.applied, result.jar_english, result.source_changed) == (1, 1, 0)
    row = _entry(db, "1.21.1", PACK_NEW)
    assert (row.zh_tw, row.review_status) == ("合成材料", "reviewed")
    note = db._conn.execute(
        "SELECT note FROM history WHERE id = ?", (result.history_id,)
    ).fetchone()[0]
    assert note == "依 jar 英文相同，跨版本沿用自 1.21.1"
    assert db.revert(result.history_id) == 1
    assert _entry(db, "1.21.1", PACK_NEW).zh_tw == ""


def test_jar_english_is_compared_across_game_versions(db):
    _jar(db, "1.20.1", "Crafting Ingredients")
    _jar(db, "1.21.1", "crafting  ingredients")  # 只差大小寫與空白
    _reviewed_pack(db, "1.20.1", PACK_OLD, OLD_PT, "合成材料")
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, NEW_PT)])

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.jar_english) == (1, 1)


@pytest.mark.parametrize(
    "jar_versions",
    [
        {},  # jar 沒有同鍵值的英文
        {"1.20.1": "Crafting Ingredients"},  # 目標版本沒有 jar 英文
        {"1.20.1": "Crafting Ingredients", "1.21.1": "Machine Parts"},  # 英文不同
    ],
    ids=["no-jar", "target-missing", "english-differs"],
)
def test_no_trusted_jar_english_keeps_the_source_change_rule(db, jar_versions):
    for version, en in jar_versions.items():
        _jar(db, version, en)
    _reviewed_pack(db, "1.20.1", PACK_OLD, OLD_PT, "合成材料")
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, NEW_PT)])

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.jar_english, result.source_changed) == (0, 0, 1)
    assert _entry(db, "1.21.1", PACK_NEW).zh_tw == ""


def test_pending_jar_source_change_blocks_reuse(db):
    """jar 原文變動時資料庫不改寫既有原文，只記在 src_change：這時英文不可信。"""
    _jar(db, "1.21.1", "Crafting Ingredients")
    _jar(db, "1.21.1", "Crafting Components")  # 記錄原文變動，條目仍是舊英文
    assert db._conn.execute("SELECT COUNT(*) FROM src_change").fetchone()[0] == 1
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, NEW_PT)])

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.source_changed) == (0, 1)


def test_plain_mod_lang_entries_are_not_affected(db):
    """只有 KubeJS 語系條目會參考 jar 英文；一般模組條目原文不同仍不沿用。"""
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", "k", "Old words", "", "", None)])
    eid = _entry(db, "1.20.1", "foo", "k").id
    db.save_manual(eid, "舊譯文", propagate=False)
    db.review_manual(eid, expected_zh_tw="舊譯文", propagate=False)
    ids = _import(
        db, "1.21.1", [ScanItem(KIND_LANG, "foo", "k", "Old wording", "", "", None)]
    )
    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.jar_english) == (0, 0)


def test_conflicting_matched_references_are_not_applied(db):
    _jar(db, "1.21.1", "Crafting Ingredients")
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    _reviewed_pack(db, "1.21.1", PACK_OTHER, "Ingredientes da Criação", "合成原料")
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, NEW_PT)])

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.conflicts) == (0, 1)


def test_reviewed_target_is_kept(db):
    _jar(db, "1.21.1", "Crafting Ingredients")
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    _reviewed_pack(db, "1.21.1", PACK_NEW, NEW_PT, "製作材料")
    ids = {_entry(db, "1.21.1", PACK_NEW).id}

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.skipped_reviewed) == (0, 1)
    assert _entry(db, "1.21.1", PACK_NEW).zh_tw == "製作材料"


def test_differences_mark_the_row_and_keep_only_matching_references(db):
    """差異審核：jar 英文相同的參考才留下，預設參考與批次套用都用它。"""
    _jar(db, "1.20.1", "Crafting Materials")  # 1.20.1 的英文不同
    _jar(db, "1.21.1", "Crafting Ingredients")
    _reviewed_pack(db, "1.20.1", PACK_OTHER, OLD_PT, "合成原料")
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    db.ingest("1.21.1", [_pack(PACK_NEW, NEW_PT)])

    rows = [
        r for r in db.list_cross_version_differences("1.21.1") if r.mod_id == PACK_NEW
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row.source_changed and row.jar_english_same and not row.conflict
    assert [(ref.version, ref.zh_tw) for ref in row.refs] == [("1.21.1", "合成材料")]


def test_dialog_selects_only_jar_english_rows(db):
    _jar(db, "1.21.1", "Crafting Ingredients")
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    db.ingest("1.21.1", [_pack(PACK_OLD, "Peça antiga", "舊零件", key="other")])
    db.review_manual(
        _entry(db, "1.21.1", PACK_OLD, "other").id,
        expected_zh_tw="舊零件",
        propagate=False,
    )
    db.ingest(
        "1.21.1",
        [_pack(PACK_NEW, NEW_PT), _pack(PACK_NEW, "Peça antigas", key="other")],
    )
    rows = db.list_cross_version_differences("1.21.1")
    changed = vdd.source_changed_rows(rows)
    assert {(r.key, r.jar_english_same) for r in changed} == {
        (KEY, True),
        ("other", False),  # jar 沒有這個鍵值：仍是原文不同
    }

    dialog = object.__new__(vdd.VersionDiffDialog)
    dialog.rows = rows
    dialog.tab = vdd.TAB_SOURCE
    dialog.selected = set()
    dialog.include_reviewed = SimpleNamespace(value=False)
    dialog._confirm_overwrite = False
    dialog._render = lambda: None

    dialog._select_jar_english()

    assert dialog.selected == {r.entry_id for r in changed if r.key == KEY}


def test_jar_english_bridges_a_source_locale_change(db):
    """使用者情境：8.3 的 KubeJS 以 ru_ru 當原文、8.6 換成 pt_br；jar 英文相同就沿用。"""
    _jar(db, "1.21.1", "Energy: %s/%s FE")
    db.ingest(
        "1.21.1",
        [_pack(PACK_OLD, "Энергия: %s/%s FE", "能量：%s/%s FE", locale="ru_ru")],
    )
    db.review_manual(
        _entry(db, "1.21.1", PACK_OLD).id,
        expected_zh_tw="能量：%s/%s FE",
        propagate=False,
    )
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, "Energia: %s/%s FE")])

    rows = db.list_cross_version_differences("1.21.1")
    assert [(r.jar_english_same, r.source_changed) for r in rows] == [(True, True)]
    assert db.explain_cross_version_exclusions("1.21.1").locale_incompatible == 0

    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.jar_english) == (1, 1)
    assert _entry(db, "1.21.1", PACK_NEW).zh_tw == "能量：%s/%s FE"


def test_same_text_in_another_locale_is_reused_with_jar_english(db):
    """原文相同但來源語系紀錄不同（例如專有名詞）：jar 英文相同時沿用，列在「差異」而非「原文不同」。"""
    _jar(db, "1.21.1", "Charging Station")
    db.ingest("1.21.1", [_pack(PACK_OLD, "Charging Station", "充電站", locale="ru_ru")])
    db.review_manual(
        _entry(db, "1.21.1", PACK_OLD).id, expected_zh_tw="充電站", propagate=False
    )
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, "Charging Station")])

    rows = db.list_cross_version_differences("1.21.1")
    assert [(r.jar_english_same, r.source_changed) for r in rows] == [(True, False)]
    assert db.reuse_reviewed_translations("1.21.1", entry_ids=ids).applied == 1


def test_locale_change_without_jar_english_is_still_not_reused(db):
    db.ingest("1.21.1", [_pack(PACK_OLD, "Charging Station", "充電站", locale="ru_ru")])
    db.review_manual(
        _entry(db, "1.21.1", PACK_OLD).id, expected_zh_tw="充電站", propagate=False
    )
    ids = _import(db, "1.21.1", [_pack(PACK_NEW, "Charging Station")])

    assert db.list_cross_version_differences("1.21.1") == []
    assert db.explain_cross_version_exclusions("1.21.1").locale_incompatible == 1
    result = db.reuse_reviewed_translations("1.21.1", entry_ids=ids)
    assert (result.applied, result.jar_english) == (0, 0)


@pytest.mark.parametrize(
    ("old_text", "locale"),
    [(OLD_PT, "pt_br"), ("Ингредиенты для крафта", "ru_ru")],
    ids=["same-locale", "locale-changed"],
)
def test_dialog_apply_writes_jar_english_rows(db, old_text, locale):
    """差異審核套用時重新確認也要用同一套規則，否則畫面上的列在寫入時全部被當成過期。"""
    _jar(db, "1.21.1", "Crafting Ingredients")
    db.ingest("1.21.1", [_pack(PACK_OLD, old_text, "合成材料", locale=locale)])
    db.review_manual(
        _entry(db, "1.21.1", PACK_OLD).id, expected_zh_tw="合成材料", propagate=False
    )
    db.ingest("1.21.1", [_pack(PACK_NEW, NEW_PT)])
    rows = db.list_cross_version_differences("1.21.1")
    items = vdd.selected_items(rows, {r.entry_id for r in rows}, False)
    assert len(items) == 1

    result = db.apply_cross_version_references(items)

    assert (result.applied, result.stale) == (1, 0)
    assert _entry(db, "1.21.1", PACK_NEW).zh_tw == "合成材料"
    note = db._conn.execute(
        "SELECT note FROM history WHERE id = ?", (result.history_id,)
    ).fetchone()[0]
    assert note.startswith("依 jar 英文相同")


@pytest.mark.parametrize(
    "jar", [True, False], ids=["jar-english", "plain-source-change"]
)
def test_apply_rechecks_only_the_pinned_reference_pack(db, jar):
    """參考固定在 8.3 時，套用的重新確認不能被 8.5（原文與目標相同）的參考改變分類而判定過期。"""
    pack_mid = "actuallyadditions@All the Mods 10 8.5@dddddddddddd"
    if jar:
        _jar(db, "1.21.1", "Crafting Ingredients")
    _reviewed_pack(db, "1.21.1", PACK_OLD, OLD_PT, "合成材料")
    _reviewed_pack(db, "1.21.1", pack_mid, NEW_PT, "製作材料")
    db.ingest("1.21.1", [_pack(PACK_NEW, NEW_PT)])
    rows = db.list_cross_version_differences(
        "1.21.1",
        ref_version="1.21.1",
        ref_pack="All the Mods 10 8.3",
        target_pack="All the Mods 10 8.6",
    )
    assert [(r.source_changed, r.jar_english_same) for r in rows] == [(True, jar)]
    items = vdd.selected_items(rows, {rows[0].entry_id}, False)

    result = db.apply_cross_version_references(items)

    assert (result.applied, result.stale) == (1, 0)
    assert _entry(db, "1.21.1", PACK_NEW).zh_tw == "合成材料"


def test_same_game_version_as_reference_uses_the_other_packs(db):
    """參考選「固定 1.21.1」而目標也是 1.21.1：參考是同版本其他整合包，不含目標自己的整合包。"""
    _reviewed_pack(db, "1.21.1", PACK_OLD, NEW_PT, "合成材料")
    db.ingest("1.21.1", [_pack(PACK_NEW, NEW_PT)])
    rows = db.list_cross_version_differences(
        "1.21.1", ref_version="1.21.1", target_pack="All the Mods 10 8.6"
    )
    assert [(r.mod_id, r.reference.mod_id) for r in rows] == [(PACK_NEW, PACK_OLD)]
    items = vdd.selected_items(rows, {rows[0].entry_id}, False)
    assert db.apply_cross_version_references(items).applied == 1
