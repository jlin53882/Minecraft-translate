"""條目頁的模組包版本篩選：選了遊戲版本才有清單，篩選後清單、筆數與下拉選項一致。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb.panel_refresh import _resolve_entries_scope
from translation_tool.translation_db.models import EntryFilter, ScanItem
from translation_tool.translation_db.repository import TranslationDB

PACK_83 = "Pack 8.3@aaaaaaaaaaaa"
PACK_85 = "Pack 8.5@bbbbbbbbbbbb"


def _pack_item(namespace: str, scope: str, key: str, kind: str = "kubejs_lang"):
    return ScanItem(
        kind=kind,
        mod_id=f"{namespace}@{scope}",
        key=key,
        en_us=f"text {key}",
        source_path=f"assets/{namespace}/lang/en_us.json",
        source_selector=f"lang:en_us:{key}",
        source_hash="H",
        pack_scope=scope,
        source_locale="en_us",
    )


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    database.ingest(
        "1.21.1",
        [
            _pack_item("alpha", PACK_83, "a"),
            _pack_item("alpha", PACK_85, "a"),
            _pack_item("alpha", PACK_85, "b"),
            _pack_item("beta", PACK_85, "c", kind="ftb_quest_lang"),
            ScanItem(kind="lang", mod_id="jarmod", key="j", en_us="From a jar"),
        ],
    )
    database.ingest(
        "1.20.1", [ScanItem(kind="lang", mod_id="jarmod", key="j", en_us="From a jar")]
    )
    return database


def _scope(db, **request):
    criteria = request.pop("criteria", EntryFilter(version=request.get("version", "")))
    return _resolve_entries_scope(
        db, {"criteria": criteria, **request}, SimpleNamespace(version="")
    )


def test_pack_options_follow_the_selected_version(db):
    assert _scope(db, version="1.21.1")["packs"] == [PACK_83, PACK_85]
    # 只有 jar 內容的版本沒有模組包版本可選：維持「全部」
    without_packs = _scope(db, version="1.20.1")
    assert without_packs["packs"] == [] and without_packs["pack"] is None
    assert without_packs["criteria"].pack_mod_ids is None


def test_pack_narrows_entries_counts_and_options(db):
    scope = _scope(db, version="1.21.1", pack=PACK_85)
    assert scope["pack"] == PACK_85
    assert scope["mods"] == [f"alpha@{PACK_85}", f"beta@{PACK_85}"]
    assert scope["kinds"] == ["ftb_quest_lang", "kubejs_lang"]

    rows, total = db.list_entries(criteria=scope["criteria"], limit=50)
    assert total == 3
    assert {row.mod_id for row in rows} == {f"alpha@{PACK_85}", f"beta@{PACK_85}"}

    all_rows, all_total = db.list_entries(
        criteria=_scope(db, version="1.21.1")["criteria"], limit=50
    )
    assert all_total == 5 and len(all_rows) == 5


def test_search_inside_a_pack_stays_inside_it(db):
    criteria = EntryFilter(version="1.21.1", query="text a")
    scope = _scope(db, version="1.21.1", pack=PACK_83, criteria=criteria)
    rows, total = db.list_entries(criteria=scope["criteria"], limit=50)
    assert total == 1 and rows[0].mod_id == f"alpha@{PACK_83}"
    assert scope["mods"] == [f"alpha@{PACK_83}"]


def test_mod_from_another_pack_is_dropped_when_the_pack_changes(db):
    scope = _scope(db, version="1.21.1", pack=PACK_83, mod_id=f"alpha@{PACK_85}")
    assert scope["mod_id"] is None

    kept = _scope(db, version="1.21.1", pack=PACK_85, mod_id=f"beta@{PACK_85}")
    assert kept["mod_id"] == f"beta@{PACK_85}"
    _rows, total = db.list_entries(criteria=kept["criteria"], limit=50)
    assert total == 1


def test_unknown_pack_falls_back_to_all(db):
    scope = _scope(db, version="1.21.1", pack="Pack 9.9")
    assert scope["pack"] is None and scope["criteria"].pack_mod_ids is None


def test_same_folder_name_in_two_places_stays_two_options(db):
    """不同位置的同名資料夾是兩個選項，名稱後面加上路徑雜湊前六碼來區分。"""
    from app.services_impl.moddb_service import pack_scope_options

    other = "Pack 8.5@cccccccccccc"
    db.ingest("1.21.1", [_pack_item("gamma", other, "z")])
    scope = _scope(db, version="1.21.1")
    assert scope["packs"] == [PACK_83, PACK_85, other]
    assert pack_scope_options(scope["packs"]) == [
        (PACK_83, "Pack 8.3"),
        (PACK_85, "Pack 8.5（bbbbbb）"),
        (other, "Pack 8.5（cccccc）"),
    ]
    picked = _scope(db, version="1.21.1", pack=other)
    assert picked["mods"] == [f"gamma@{other}"]


# ------------------------------------------------------------------ 批次機翻頁
def _translate_panel():
    from app.views.moddb import translate_panel
    from tests.conftest import mock_page

    page = mock_page()
    page.run_thread = lambda target: target()
    page.run_task = lambda *_a, **_k: None
    return translate_panel, translate_panel.TranslatePanel(
        page, lambda: None, request_refresh=lambda: None
    )


def _translate_snapshot(**extra):
    mods = [f"alpha@{PACK_83}", f"alpha@{PACK_85}", f"beta@{PACK_85}", "jarmod"]
    return {
        "identity": None,
        "versions": ["1.21.1"],
        "version": "1.21.1",
        "mods": mods,
        "mod": "__all__",
        **extra,
    }


def test_translate_page_pack_limits_the_mod_list_and_scope():
    module, panel = _translate_panel()
    panel.apply_refresh_snapshot(_translate_snapshot())
    assert [o.key for o in panel.pack_dd.options] == [
        module.ALL_PACKS,
        PACK_83,
        PACK_85,
    ]
    assert not panel.pack_dd.disabled and panel.mod_ids() == ()

    panel.pack_dd.value = PACK_85
    panel._on_scope_changed(SimpleNamespace(control=panel.pack_dd))

    # 畫面端立刻換算：模組選單只剩該模組包的模組，範圍是它的所有模組
    assert [o.key for o in panel.mod_dd.options] == [
        module.ALL_MODS,
        f"alpha@{PACK_85}",
        f"beta@{PACK_85}",
    ]
    assert panel.mod_ids() == (f"alpha@{PACK_85}", f"beta@{PACK_85}")

    panel.mod_dd.value = f"beta@{PACK_85}"
    assert panel.mod_ids() == (f"beta@{PACK_85}",)

    panel.version_dd.value = "1.21.1"
    panel._on_scope_changed(SimpleNamespace(control=panel.version_dd))
    assert panel.pack_dd.value == module.ALL_PACKS and panel.mod_ids() == ()


def test_translate_page_without_pack_content_keeps_all(db):
    module, panel = _translate_panel()
    panel.apply_refresh_snapshot(_translate_snapshot(mods=["jarmod"]))
    assert panel.pack_dd.disabled
    assert [o.key for o in panel.pack_dd.options] == [module.ALL_PACKS]


def test_translate_snapshot_counts_only_the_selected_pack(db):
    from app.views.moddb.panel_refresh import _load_translate_snapshot

    snapshot = _load_translate_snapshot(
        db, {"version": "1.21.1", "pack": PACK_85, "mod": "__all__"}, {}
    )
    assert snapshot["pack"] == PACK_85 and snapshot["missing"] == 3
    everything = _load_translate_snapshot(
        db, {"version": "1.21.1", "mod": "__all__"}, {}
    )
    assert everything["pack"] is None and everything["missing"] == 5
    # 選的模組不屬於這個模組包：退回全部模組，範圍仍是該模組包
    mismatch = _load_translate_snapshot(
        db, {"version": "1.21.1", "pack": PACK_83, "mod": f"beta@{PACK_85}"}, {}
    )
    assert mismatch["mod"] == "__all__" and mismatch["missing"] == 1


# ------------------------------------------------------------------ 批量審核
def _add_translations(db):
    """每個模組包條目都補一筆未審核的譯文，才會成為批量審核的候選。"""
    items = [
        ScanItem(kind=kind, mod_id=mod_id, key=key, en_us="", zh_tw=f"譯 {key}")
        for kind, mod_id, key in db._q(
            "SELECT kind, mod_id, key FROM entry WHERE mc_version='1.21.1'"
        )
    ]
    db.ingest("1.21.1", items)


def test_bulk_review_scope_follows_the_pack(db):
    _add_translations(db)
    assert db.bulk_review_pack_options("1.21.1", quality_pass_only=False) == (
        PACK_83,
        PACK_85,
    )
    modules, _sources = db.bulk_review_scope_options(
        "1.21.1", quality_pass_only=False, pack_scope=PACK_85
    )
    assert modules == (f"alpha@{PACK_85}", f"beta@{PACK_85}")
    assert db.bulk_review_kind_options(
        "1.21.1", quality_pass_only=False, pack_scope=PACK_83
    ) == ("kubejs_lang",)

    plan = db.preview_bulk_review(
        "1.21.1", None, quality_pass_only=False, pack_scope=PACK_85
    )
    assert plan.pack_scope == PACK_85
    assert {c.mod_id for c in plan.candidates} == {
        f"alpha@{PACK_85}",
        f"beta@{PACK_85}",
    }
    assert len(plan.candidates) == 3

    # 執行前會用同一個模組包範圍重查；範圍一致才審核，別的模組包不受影響
    result = db.execute_bulk_review(plan, actor="tester")
    assert result.reviewed == 3
    left = db.preview_bulk_review("1.21.1", None, quality_pass_only=False)
    assert {c.mod_id for c in left.candidates} == {f"alpha@{PACK_83}", "jarmod"}


def test_bulk_review_dialog_lists_packs_and_passes_the_scope(db):
    from app.views.moddb.bulk_review_dialog import ALL_PACKS, BulkReviewDialog
    from tests.conftest import mock_page
    from tests.test_bulk_review import _drain

    _add_translations(db)
    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(page, lambda: db, "1.21.1", None, pack_scope=PACK_85)
    dialog.quality_check.value = False
    dialog._start_scope_options_load()
    _drain(page)

    assert [o.key for o in dialog.pack_dd.options] == [ALL_PACKS, PACK_83, PACK_85]
    assert dialog.pack_dd.value == PACK_85 and not dialog.pack_dd.disabled
    assert [o.key for o in dialog.mod_dd.options][1:] == [
        f"alpha@{PACK_85}",
        f"beta@{PACK_85}",
    ]

    dialog._preview()
    _drain(page)
    assert dialog.plan is not None and dialog.plan.pack_scope == PACK_85
    assert len(dialog.plan.candidates) == 3
