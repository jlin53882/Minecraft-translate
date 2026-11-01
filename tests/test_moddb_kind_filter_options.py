"""類型篩選：下拉選單只列出目前條件下真的有條目的類型，並且真正套用到批量審核與學名掃描。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.services_impl import moddb_species_service
from translation_tool.translation_db import (
    KIND_LANG,
    KIND_PATCHOULI,
    EntryFilter,
    QualityFilter,
    ScanItem,
    TranslationDB,
)

VERSION = "1.21.1"


def _item(kind, mod, key, en, tw=""):
    return ScanItem(kind, mod, key, en, tw, "")


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "kinds.db")
    d.ingest(
        VERSION,
        [
            _item(KIND_LANG, "alpha", "a.one", "Iron Block", "鐵方塊"),
            _item(KIND_LANG, "alpha", "a.two", "Gold Block"),
            _item(KIND_LANG, "beta", "b.one", "Stone Block", "石頭方塊"),
            _item(KIND_PATCHOULI, "beta", "b.book", "Guide Page", "指南"),
            _item(KIND_PATCHOULI, "beta", "b.book2", "Guide Two", "指南二"),
        ],
    )
    # patchouli 的兩筆都已審核：未審核條件下 patchouli 沒有任何條目，不應出現在類型選單
    for row in d.list_entries(
        criteria=EntryFilter(version=VERSION, kind=KIND_PATCHOULI), limit=None
    )[0]:
        d.review_manual(row.id, expected_zh_tw=row.zh_tw or "", propagate=False)
    yield d
    d.close()


FILTERS = [
    {},
    {"review_status": "unreviewed"},
    {"review_status": "reviewed"},
    {"state": "none"},
    {"query": "block"},
    {"query": "指南", "query_field": "translation"},
    {"quality": QualityFilter(status="consistent")},
]
SELECTIONS = [
    {},
    {"mod_id": "alpha"},
    {"mod_id": "beta"},
    {"mod_id": "missing"},
    {"source": 999},
]


@pytest.mark.parametrize("filter_kwargs", FILTERS)
@pytest.mark.parametrize("selection", SELECTIONS)
def test_kind_options_match_entries_that_exist_under_the_same_filters(
    db, filter_kwargs, selection
):
    """類型選項與逐一套用類型篩選後有條目的結果完全一致（不多列、不漏列）。"""
    criteria = EntryFilter(version=VERSION, **filter_kwargs, **selection)
    options = set(db.kind_option_values(criteria))
    expected = {
        kind
        for kind in db.kinds(VERSION)
        if db.list_entries(criteria=replace(criteria, kind=kind), limit=1)[1] > 0
    }
    assert options == expected


def test_unreviewed_filter_drops_kind_without_unreviewed_entries(db):
    """未審核條件下，全部已審核的 patchouli 不應出現在類型選單（即使整個版本仍有該類型）。"""
    assert db.kinds(VERSION) == [KIND_LANG, KIND_PATCHOULI]
    unreviewed = EntryFilter(version=VERSION, review_status="unreviewed")
    assert db.kind_option_values(unreviewed) == (KIND_LANG,)

    reviewed = EntryFilter(version=VERSION, review_status="reviewed")
    assert db.kind_option_values(reviewed) == (KIND_PATCHOULI,)
    assert db.kind_option_values(replace(reviewed, mod_id="alpha")) == ()


def test_kind_selection_does_not_hide_other_kinds(db):
    """選了某個類型後，其他類型仍保留在選單中，方便切換。"""
    criteria = EntryFilter(version=VERSION, kind=KIND_LANG)
    assert db.kind_option_values(criteria) == (KIND_LANG, KIND_PATCHOULI)


def test_bulk_review_kind_options_and_preview_respect_kind(db):
    """批量審核的類型選單只列出仍有未審核候選的類型，預覽也只包含所選類型。"""
    assert db.bulk_review_kind_options(VERSION) == (KIND_LANG,)
    assert db.bulk_review_kind_options(VERSION, mod_id="beta") == (KIND_LANG,)

    modules, sources_by_mod = db.bulk_review_scope_options(VERSION, kind=KIND_PATCHOULI)
    assert modules == ()
    assert sources_by_mod == {None: ()}

    lang_plan = db.preview_bulk_review(
        VERSION, None, kind=KIND_LANG, quality_pass_only=False
    )
    assert lang_plan.kind == KIND_LANG
    assert {c.key for c in lang_plan.candidates} == {"a.one", "b.one"}

    patchouli_plan = db.preview_bulk_review(
        VERSION, None, kind=KIND_PATCHOULI, quality_pass_only=False
    )
    assert patchouli_plan.candidates == ()

    all_plan = db.preview_bulk_review(VERSION, None, quality_pass_only=False)
    assert all_plan.kind is None
    assert {c.key for c in all_plan.candidates} == {"a.one", "b.one"}


def test_species_kind_options_follow_module_and_kind_selection(tmp_path):
    db = TranslationDB(tmp_path / "species.db")
    try:
        db.ingest(
            VERSION,
            [
                _item(KIND_LANG, "plants", "item.oak.latin", "Quercus robur", "櫟樹"),
                _item(KIND_PATCHOULI, "trees", "book.pine.latin", "Pinus sylvestris"),
            ],
        )
        assert moddb_species_service.species_kind_options(db, VERSION) == (
            KIND_LANG,
            KIND_PATCHOULI,
        )
        assert moddb_species_service.species_kind_options(
            db, VERSION, mod_id="plants"
        ) == (KIND_LANG,)

        modules, _sources = moddb_species_service.species_scope_options(
            db, VERSION, kind=KIND_PATCHOULI
        )
        assert modules == ("trees",)

        result = moddb_species_service.scan_species_candidates(
            db, VERSION, kind=KIND_PATCHOULI
        )
        assert [c.key for c in result.candidates] == ["book.pine.latin"]
    finally:
        db.close()


def _drain(page):
    page._run_all_tasks()
    page._tasks.clear()


def test_bulk_review_dialog_kind_filter_offers_only_kinds_with_candidates(db):
    from app.views.moddb.bulk_review_dialog import BulkReviewDialog
    from tests.conftest import mock_page

    page = mock_page()
    page.run_thread = lambda target: target()
    dialog = BulkReviewDialog(page, lambda: db, VERSION, None)
    _drain(page)
    assert [option.key for option in dialog.kind_dd.options] == ["__all__", KIND_LANG]

    dialog.quality_check.value = False
    dialog.kind_dd.value = KIND_LANG
    dialog._on_kind()
    _drain(page)
    assert dialog.kind_dd.value == KIND_LANG
    dialog._preview()
    _drain(page)
    assert dialog.plan.kind == KIND_LANG
    assert {c.key for c in dialog.plan.candidates} == {"a.one", "b.one"}


def test_species_dialog_kind_filter_narrows_modules_and_scan(tmp_path):
    from app.views.moddb.species_dialog import SpeciesCandidateDialog
    from tests.conftest import mock_page

    db = TranslationDB(tmp_path / "species_dialog.db")
    try:
        db.ingest(
            VERSION,
            [
                _item(KIND_LANG, "plants", "item.oak.latin", "Quercus robur", "櫟樹"),
                _item(KIND_PATCHOULI, "trees", "book.pine.latin", "Pinus sylvestris"),
            ],
        )
        page = mock_page()
        page.run_thread = lambda target: target()
        dialog = SpeciesCandidateDialog(page, lambda: db, VERSION, None, None)
        _drain(page)
        assert [o.key for o in dialog.kind_dd.options] == [
            "__all__",
            KIND_LANG,
            KIND_PATCHOULI,
        ]

        dialog.kind_dd.value = KIND_PATCHOULI
        dialog._on_kind()
        _drain(page)
        assert [o.key for o in dialog.mod_dd.options] == ["__all__", "trees"]
        assert dialog.kind == KIND_PATCHOULI
    finally:
        db.close()


def test_entries_page_kind_options_follow_review_filter_and_keep_selection(db):
    from types import SimpleNamespace

    from app.views.moddb.panel_refresh import _resolve_entries_scope

    settings = SimpleNamespace(version=VERSION)
    unreviewed = EntryFilter(version=VERSION, review_status="unreviewed")
    scope = _resolve_entries_scope(
        db,
        {"criteria": unreviewed, "version": VERSION, "mod_id": None, "kind": None},
        settings,
    )
    assert scope["kinds"] == [KIND_LANG]

    # 已選的類型即使在目前條件下沒有條目，仍保留在選單中（與模組相同的規則）
    scope = _resolve_entries_scope(
        db,
        {
            "criteria": replace(unreviewed, kind=KIND_PATCHOULI),
            "version": VERSION,
            "mod_id": None,
            "kind": KIND_PATCHOULI,
        },
        settings,
    )
    assert scope["kinds"] == [KIND_LANG, KIND_PATCHOULI]
    assert scope["kind"] == KIND_PATCHOULI
