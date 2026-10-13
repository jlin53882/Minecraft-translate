"""Mod DB 學名候選掃描的服務與 UI 控制測試。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import requests

from app.services_impl import moddb_species_service
from app.views.moddb import species_dialog as species_dialog_module
from app.views.moddb.entries_panel import EntriesPanel
from app.views.moddb.species_dialog import SpeciesCandidateDialog
from tests.conftest import mock_page
from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB
from translation_tool.utils import species_cache


def _seed(path: Path) -> TranslationDB:
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "plants", "item.agave.latin", "Agave tequilana"),
            ScanItem(KIND_LANG, "plants", "item.akebia.latin", "Akebia quinata"),
            ScanItem(KIND_LANG, "plants", "item.cucumber.latin", "Cucumis sativus"),
            ScanItem(KIND_LANG, "plants", "item.steel", "Steel Casing"),
        ],
    )
    return db


def test_scan_species_candidates_keeps_entry_scope_and_filters_values(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        result = moddb_species_service.scan_species_candidates(
            db, "1.21.1", mod_id="plants", kind=KIND_LANG
        )
    finally:
        db.close()

    assert result.scanned_entries == 4
    assert result.total_candidates == 3
    assert [candidate.value for candidate in result.candidates] == [
        "Agave tequilana",
        "Akebia quinata",
        "Cucumis sativus",
    ]
    assert all(candidate.mod_id == "plants" for candidate in result.candidates)
    assert result.truncated is False


def test_scan_species_candidates_reports_limit_without_writing(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        result = moddb_species_service.scan_species_candidates(db, "1.21.1", limit=2)
        assert result.total_candidates == 2
        assert result.truncated is True
        assert db.count_entries() == 4
    finally:
        db.close()


def test_species_dialog_exposes_scan_and_lookup_actions(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        dialog = SpeciesCandidateDialog(
            mock_page(), lambda: db, "1.21.1", "plants", KIND_LANG
        )
        assert dialog.scan_btn.content == "掃描候選"
        assert dialog.query_all_btn.content == "查詢全部候選"
        assert dialog.query_all_btn.disabled is True
    finally:
        db.close()


def test_entries_panel_adds_species_scan_button(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        panel = EntriesPanel(mock_page(), lambda: db)
        assert panel.species_scan_btn.content == "掃描學名"
    finally:
        db.close()


# ---------------------------------------------------------------- 查詢結果分類與快取


@pytest.fixture
def isolated_species_cache(monkeypatch):
    monkeypatch.setattr(species_cache, "_initialized", True)
    monkeypatch.setattr(species_cache, "_species_cache_data", {})
    monkeypatch.setattr(species_cache, "_CACHE_FILE", None)
    monkeypatch.setattr(species_cache, "_name_locks", {})
    return species_cache


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (_http_error(429), species_cache.STATUS_RATE_LIMITED),
        (_http_error(503), species_cache.STATUS_ERROR),
        (requests.Timeout(), species_cache.STATUS_ERROR),
        (requests.ConnectionError(), species_cache.STATUS_ERROR),
    ],
)
def test_lookup_species_classifies_failures_and_does_not_cache(
    isolated_species_cache, monkeypatch, raised, status
):
    def fail(_name):
        raise raised

    monkeypatch.setattr(species_cache, "_query_wikipedia_title", fail)
    lookup = species_cache.lookup_species("Olea europaea")
    assert lookup.status == status
    assert lookup.definitive is False
    assert species_cache._species_cache_data == {}


def test_lookup_species_distinguishes_not_found_from_found(
    isolated_species_cache, monkeypatch
):
    titles = {"Olea europaea": "油橄欖", "Zzzz qqqq": None}
    monkeypatch.setattr(
        species_cache, "_query_wikipedia_title", lambda name: titles[name]
    )
    found = species_cache.lookup_species("Olea europaea")
    missing = species_cache.lookup_species("Zzzz qqqq")
    assert (found.status, found.value) == (species_cache.STATUS_FOUND, "油橄欖")
    assert missing.status == species_cache.STATUS_NOT_FOUND and missing.definitive
    assert species_cache.lookup_species_name("Olea europaea") == "油橄欖"


def test_lookup_species_merges_concurrent_requests_for_same_name(
    isolated_species_cache, monkeypatch
):
    calls = []
    release = threading.Event()

    def slow_fetch(name):
        calls.append(name)
        release.wait(2)
        return "油橄欖"

    monkeypatch.setattr(species_cache, "_query_wikipedia_title", slow_fetch)
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(species_cache.lookup_species("Olea europaea"))
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(calls) == 1
    assert [r.value for r in results] == ["油橄欖"] * 4


# ---------------------------------------------------------------- 對話框生命週期
def _make_dialog(tmp_path, monkeypatch, lookups):
    """建立同步執行背景工作的對話框；``lookups`` 依序回傳每筆查詢結果。"""
    db = _seed(tmp_path / "mod.db")
    dialog = SpeciesCandidateDialog(mock_page(), lambda: db, "1.21.1", None, None)
    dialog._post = lambda callback, *args: callback(*args)
    dialog._launch = lambda work, name, *, cancellable=False: (work(), True)[1]
    results = iter(lookups)
    monkeypatch.setattr(
        species_dialog_module, "_safe_lookup", lambda _value: next(results)
    )
    dialog._scan()
    return db, dialog


FOUND = species_cache.SpeciesLookup(species_cache.STATUS_FOUND, "名稱")
MISSING = species_cache.SpeciesLookup(species_cache.STATUS_NOT_FOUND)
FAILED = species_cache.SpeciesLookup(species_cache.STATUS_ERROR, None, "連線逾時")
LIMITED = species_cache.SpeciesLookup(
    species_cache.STATUS_RATE_LIMITED, None, "HTTP 429"
)


def test_dialog_bulk_lookup_can_be_cancelled_and_keeps_finished_results(
    tmp_path, monkeypatch
):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND, FOUND, FOUND])
    try:
        original = species_dialog_module._safe_lookup

        def stop_after_first(value):
            result = original(value)
            dialog._stop()
            return result

        monkeypatch.setattr(species_dialog_module, "_safe_lookup", stop_after_first)
        dialog._query_all()
        states = list(dialog._states.values())
        assert states[0].done is True
        assert all(state.lookup is None for state in states[1:])
        assert dialog._busy is False and dialog._bulk_running is False
        assert "已停止" in dialog.status.value
    finally:
        db.close()


def test_dialog_failed_lookup_can_be_retried_and_failures_stop_bulk(
    tmp_path, monkeypatch
):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FAILED, FAILED, FAILED])
    try:
        dialog._query_all()
        states = list(dialog._states.values())
        assert all(state.lookup is FAILED for state in states)
        assert not any(state.done for state in states)
        assert "連續查詢失敗" in dialog.status.value
        assert dialog.query_all_btn.disabled is False
    finally:
        db.close()


def test_dialog_rate_limit_stops_bulk_without_marking_not_found(tmp_path, monkeypatch):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [LIMITED])
    try:
        dialog._query_all()
        states = list(dialog._states.values())
        assert states[0].lookup is LIMITED and not states[0].done
        assert "過於頻繁" in dialog.status.value
    finally:
        db.close()


def test_dialog_restores_state_when_operation_launch_is_rejected(tmp_path, monkeypatch):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [])
    try:
        dialog._launch = lambda work, name, *, cancellable=False: False
        candidate = dialog.result.candidates[0]
        dialog._query_one(candidate)
        assert dialog._states[candidate.entry_id].running is False
        dialog._query_all()
        assert dialog._busy is False and dialog._bulk_running is False
        assert dialog.query_all_btn.disabled is False
        dialog._scan()
        assert dialog._busy is False
    finally:
        db.close()


def test_dialog_bulk_skips_running_candidates_and_blocks_while_running(
    tmp_path, monkeypatch
):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND, FOUND])
    try:
        first = dialog.result.candidates[0]
        dialog._states[first.entry_id].running = True
        dialog._query_all()  # 有單筆正在查詢時不啟動批次
        assert dialog._bulk_running is False
        dialog._states[first.entry_id].running = False
        dialog._states[first.entry_id].lookup = FOUND
        dialog._query_all()
        assert all(state.done for state in dialog._states.values())
    finally:
        db.close()


def test_dialog_updates_only_the_finished_row(tmp_path, monkeypatch):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND])
    try:
        before = list(dialog.rows.controls)
        candidate = dialog.result.candidates[1]
        dialog._query_one(candidate)
        after = dialog.rows.controls
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a is not b]
        assert changed == [dialog._row_index[candidate.entry_id]]
    finally:
        db.close()


# ---------------------------------------------------------------- 候選判斷規則
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("block.productivetrees.olive.latin", True),
        ("tooltip.productivefarming.blueberry.latin", True),
        ("entity.mod.wolf.scientificName", True),
        ("entity.mod.wolf.scientific_name", True),
        ("gui.translating.title", False),
        ("gui.energy_usage", False),
        ("subtitles.creeper.dies", False),
    ],
)
def test_has_species_key_hint(key, expected):
    assert moddb_species_service.has_species_key_hint(key) is expected


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("gui.energy_usage", "Energy usage"),
        ("item.oak_planks", "Oak planks"),
        ("subtitles.creeper.dies", "Creeper dies"),
        ("gui.copy", "Copy to clipboard"),
        ("block.mod.olive.latin", "Steel Casing"),
    ],
)
def test_species_candidate_rejects_general_text_by_default(key, value):
    assert not moddb_species_service.is_species_candidate(key, value)


def test_species_candidate_accepts_key_hint_or_taxonomic_marker():
    check = moddb_species_service.is_species_candidate
    assert check("block.mod.olive.latin", "Olea europaea")
    assert check("misc.unknown", "Apium graveolens var. dulce")
    assert check("misc.unknown", "Mentha × piperita")
    assert not check("misc.unknown", "Olea europaea")


def test_species_candidate_loose_mode_only_checks_format():
    check = moddb_species_service.is_species_candidate
    assert check("misc.unknown", "Olea europaea", require_key_hint=False)
    assert not check("misc.unknown", "Energy-Usage!", require_key_hint=False)


def test_scan_skips_general_text_and_loose_mode_includes_it(tmp_path):
    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(KIND_LANG, "m", "block.m.olive.latin", "Olea europaea"),
                ScanItem(KIND_LANG, "m", "gui.energy_usage", "Energy usage"),
                ScanItem(KIND_LANG, "m", "misc.oak", "Oak planks"),
            ],
        )
        strict = moddb_species_service.scan_species_candidates(db, "1.21.1")
        loose = moddb_species_service.scan_species_candidates(
            db, "1.21.1", require_key_hint=False
        )
    finally:
        db.close()
    assert [c.value for c in strict.candidates] == ["Olea europaea"]
    assert {c.value for c in loose.candidates} == {
        "Olea europaea",
        "Energy usage",
        "Oak planks",
    }


# ---------------------------------------------------------------- 進階篩選：疑似學名文字
def test_species_name_quality_filter_lists_only_species_entries(tmp_path):
    from translation_tool.translation_db import EntryFilter, QualityFilter

    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(KIND_LANG, "m", "block.m.olive.latin", "Olea europaea"),
                ScanItem(KIND_LANG, "m", "misc.mint", "Mentha × piperita"),
                ScanItem(KIND_LANG, "m", "gui.energy_usage", "Energy usage"),
                ScanItem(KIND_LANG, "m", "block.m.steel.latin", "Steel Casing"),
                ScanItem(KIND_LANG, "m", "misc.oak", "Oak planks"),
            ],
        )
        criteria = EntryFilter(
            version="1.21.1", quality=QualityFilter(status="species_name")
        )
        rows, total = db.list_entries(criteria=criteria, limit=50)
        unpaged, unpaged_total = db.list_entries(criteria=criteria, limit=None)
    finally:
        db.close()

    assert total == unpaged_total == 2
    assert {row.en_us for row in rows} == {"Olea europaea", "Mentha × piperita"}
    assert {row.en_us for row in unpaged} == {row.en_us for row in rows}


def test_species_names_module_is_pure_and_shared():
    from translation_tool.utils import species_names

    assert species_names.is_species_candidate("a.latin", "Olea europaea")
    assert species_cache.is_potential_species_name is (
        species_names.is_potential_species_name
    )
