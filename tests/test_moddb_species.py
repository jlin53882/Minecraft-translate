"""Mod DB 學名候選掃描的服務與 UI 控制測試。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import requests

from app.services_impl import moddb_species_service
from app.views.moddb import species_bulk_lookup as bulk_module
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

    assert result.total_candidates == 3
    assert [candidate.value for candidate in result.candidates] == [
        "Agave tequilana",
        "Akebia quinata",
        "Cucumis sativus",
    ]
    assert all(candidate.mod_id == "plants" for candidate in result.candidates)


def test_scan_species_candidates_has_no_cap_and_does_not_write(tmp_path):
    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    KIND_LANG,
                    "m",
                    f"block.m.s{i}.latin",
                    f"Genus species{chr(97 + i % 26)}",
                )
                for i in range(700)
            ],
        )
        before = db.count_entries()
        result = moddb_species_service.scan_species_candidates(db, "1.21.1")
        assert result.total_candidates == 700
        assert db.count_entries() == before
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
    return _isolate_species_cache(monkeypatch)


def _isolate_species_cache(monkeypatch):
    monkeypatch.setattr(species_cache, "_initialized", True)
    monkeypatch.setattr(species_cache, "_species_cache_data", {})
    monkeypatch.setattr(species_cache, "_CACHE_FILE", None)
    monkeypatch.setattr(species_cache, "_in_flight", {})
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
def _sync(callback, *args):
    callback(*args)


def _run_now(_page, target, **_kwargs):
    target()
    return True


def _new_dialog(page, db):
    dialog = SpeciesCandidateDialog(page, lambda: db, "1.21.1", None, None)
    dialog._post = _sync
    dialog._launch = lambda work, name: (work(), True)[1]
    dialog.job._post = _sync
    return dialog


def _make_dialog(tmp_path, monkeypatch, lookups, *, launcher=_run_now):
    """建立同步執行背景工作的對話框；``lookups`` 依序回傳每筆查詢結果。"""
    _isolate_species_cache(monkeypatch)
    db = _seed(tmp_path / "mod.db")
    monkeypatch.setattr(bulk_module, "launch_page_operation", launcher)
    results = iter(lookups)
    monkeypatch.setattr(bulk_module, "safe_lookup", lambda _value, **_kw: next(results))
    dialog = _new_dialog(mock_page(), db)
    dialog.open()
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
        original = bulk_module.safe_lookup

        def stop_after_first(value, **kwargs):
            result = original(value, **kwargs)
            dialog._stop()
            return result

        monkeypatch.setattr(bulk_module, "safe_lookup", stop_after_first)
        dialog._query_all()
        states = list(dialog._states.values())
        assert states[0].done is True
        assert all(state.lookup is None for state in states[1:])
        assert dialog._busy is False and dialog.job.running is False
        assert dialog.job.session.snapshot()["status"] == "DONE"
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
        dialog._launch = lambda work, name: False
        monkeypatch.setattr(
            bulk_module, "launch_page_operation", lambda *_a, **_k: False
        )
        candidate = dialog.result.candidates[0]
        dialog._query_one(candidate)
        assert dialog._states[candidate.entry_id].running is False
        dialog._query_all()
        assert dialog._busy is False and dialog.job.running is False
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
        assert dialog.job.running is False
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


def test_dialog_pages_candidates_without_a_cap(tmp_path, monkeypatch):
    from app.views.moddb.species_dialog import PAGE_SIZE

    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    KIND_LANG,
                    "m",
                    f"block.m.s{i:03}.latin",
                    f"Genus species{chr(97 + i % 26)}{chr(97 + i // 26)}",
                )
                for i in range(PAGE_SIZE * 2 + 5)
            ],
        )
        dialog = SpeciesCandidateDialog(mock_page(), lambda: db, "1.21.1", None, None)
        dialog._post = lambda callback, *args: callback(*args)
        dialog._launch = lambda work, name, *, cancellable=False: (work(), True)[1]
        dialog._scan()
        assert dialog.result.total_candidates == PAGE_SIZE * 2 + 5
        assert len(dialog.rows.controls) == PAGE_SIZE
        assert dialog.pager.visible is True and dialog.pager.total_pages == 3
        dialog._on_page(3)
        assert len(dialog.rows.controls) == 5
        first_on_last_page = dialog._page_candidates[0]
        assert first_on_last_page is dialog.result.candidates[PAGE_SIZE * 2]
    finally:
        db.close()


def test_species_format_filter_is_format_only(tmp_path):
    from translation_tool.translation_db import EntryFilter, QualityFilter

    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(KIND_LANG, "m", "misc.a", "Olea europaea"),
                ScanItem(KIND_LANG, "m", "misc.b", "oak planks"),
                ScanItem(KIND_LANG, "m", "misc.c", "Steel"),
            ],
        )
        rows, total = db.list_entries(
            criteria=EntryFilter(
                version="1.21.1", quality=QualityFilter(status="species_format")
            ),
            limit=None,
        )
    finally:
        db.close()
    assert total == 1 and rows[0].en_us == "Olea europaea"


# ---------------------------------------------------------------- 條目校對「學名」分頁
def _entry(key, value, zh=""):
    from types import SimpleNamespace

    return SimpleNamespace(key=key, en_us=value, zh_tw=zh)


def _texts(controls):
    found = []

    def walk(control):
        for attr in ("value", "content", "text"):
            value = getattr(control, attr, None)
            if isinstance(value, str):
                found.append(value)
        for child in getattr(control, "controls", None) or [
            getattr(control, "content", None)
        ]:
            if child is not None and not isinstance(child, str):
                walk(child)

    for control in controls:
        walk(control)
    return found


def test_species_suggestion_shows_cached_name_and_applies_it(isolated_species_cache):
    from app.views.moddb.species_suggestion import SpeciesSuggestion

    species_cache._species_cache_data["Olea europaea"] = "油橄欖"
    applied = []
    suggestion = SpeciesSuggestion(mock_page(), applied.append, lambda: None)
    texts = _texts(
        suggestion.build(_entry("block.m.olive.latin", "Olea europaea", "橄欖"))
    )
    assert "油橄欖" in texts and "套用" in texts and "查詢維基百科" not in texts


def test_species_suggestion_offers_query_when_not_cached(isolated_species_cache):
    from app.views.moddb.species_suggestion import SpeciesSuggestion

    suggestion = SpeciesSuggestion(mock_page(), lambda _t: None, lambda: None)
    texts = _texts(suggestion.build(_entry("block.m.x.latin", "Ginkgo biloba")))
    assert "尚未查詢" in texts and "查詢維基百科" in texts


def test_species_suggestion_explains_non_species_entries(isolated_species_cache):
    from app.views.moddb.species_suggestion import SpeciesSuggestion

    suggestion = SpeciesSuggestion(mock_page(), lambda _t: None, lambda: None)
    texts = _texts(suggestion.build(_entry("gui.energy", "Energy 100")))
    assert any("不像學名" in text for text in texts)


def test_species_suggestion_query_updates_and_allows_retry(
    isolated_species_cache, monkeypatch
):
    from app.views.moddb import species_suggestion as module

    changes = []
    suggestion = module.SpeciesSuggestion(
        mock_page(), lambda _t: None, lambda: changes.append(1)
    )
    suggestion._post = lambda callback, *args: callback(*args)
    suggestion._launch = lambda work: (work(), True)[1]
    monkeypatch.setattr(module, "_safe_lookup", lambda _name, **_kw: FAILED)
    suggestion.query("Ginkgo biloba")
    texts = _texts(suggestion.build(_entry("block.m.x.latin", "Ginkgo biloba")))
    assert "重試" in texts and changes


def test_retry_after_is_capped_and_cached_lookup_never_connects(
    isolated_species_cache,
):
    response = requests.Response()
    response.headers["Retry-After"] = "999"
    assert species_cache._retry_after_seconds(response) == 10.0
    response.headers["Retry-After"] = "abc"
    assert species_cache._retry_after_seconds(response) == 0.0
    assert species_cache.cached_species_lookup("Olea europaea") is None


def test_panel_open_species_entry_selects_species_tab(tmp_path):
    from app.views.moddb.species_suggestion import TAB_KEY

    db = _seed(tmp_path / "mod.db")
    try:
        panel = EntriesPanel(mock_page(), lambda: db)
        panel.refresh()
        entry_id = panel.rows[0].id
        panel.open_species_entry(entry_id)
        assert panel.sug_tab == TAB_KEY
        assert panel.sug_seg.value == TAB_KEY
        assert panel.selected.id == entry_id
    finally:
        db.close()


# ---------------------------------------------------------------- 背景批次查詢
class _DeferredLauncher:
    """記下背景工作但不立刻執行，模擬查詢還在跑的時候關閉視窗。"""

    def __init__(self):
        self.targets = []
        self.kwargs = {}

    def __call__(self, _page, target, **kwargs):
        self.targets.append(target)
        self.kwargs = kwargs
        return True

    def run(self):
        self.targets.pop(0)()


def test_bulk_lookup_keeps_running_after_dialog_closes_and_reopens(
    tmp_path, monkeypatch
):
    launcher = _DeferredLauncher()
    db, dialog = _make_dialog(
        tmp_path, monkeypatch, [FOUND, MISSING, FOUND], launcher=launcher
    )
    try:
        dialog._query_all()
        job = dialog.job
        assert job.running and dialog.stop_btn.visible
        assert launcher.kwargs["task_session"] is job.session
        assert job.session.name == "學名批次查詢"
        assert job.session.view_key == "moddb"
        assert dialog.background_btn.visible
        dialog._run_in_background()
        assert job.running  # 背景執行：收起視窗，查詢繼續

        reopened = _new_dialog(dialog.page, db)
        assert reopened.job is job
        reopened.open()
        assert reopened.result is job.result
        assert reopened.scan_btn.disabled and reopened.stop_btn.visible
        assert "查詢學名 0/3" in reopened.status.value

        launcher.run()
        assert not job.running
        assert [s.lookup for s in reopened._states.values()] == [
            FOUND,
            MISSING,
            FOUND,
        ]
        assert "學名查詢完成" in reopened.status.value
        assert "找到 2" in reopened.status.value
        assert reopened.stop_btn.visible is False
        assert reopened.background_btn.visible is False
        assert reopened.scan_btn.disabled is False
        # 已關閉的視窗不再接收事件
        assert all(s.lookup is None for s in dialog._states.values())
        assert job.session.snapshot()["progress"] == 1.0
    finally:
        db.close()


def test_bulk_lookup_stops_when_task_capsule_cancels(tmp_path, monkeypatch):
    launcher = _DeferredLauncher()
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND], launcher=launcher)
    try:
        dialog._query_all()
        launcher.kwargs["on_cancel"]()  # 頂列任務膠囊的取消
        launcher.run()
        assert dialog.job.stop_reason == "cancelled"
        assert all(s.lookup is None for s in dialog._states.values())
        assert "已停止查詢" in dialog.status.value
    finally:
        db.close()


def test_scan_prefills_cached_names_and_skips_them_in_bulk(tmp_path, monkeypatch):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND, FOUND])
    try:
        species_cache._species_cache_data["Agave tequilana"] = "龍舌蘭"
        dialog._scan()
        values = {c.entry_id: c.value for c in dialog.result.candidates}
        cached = [entry_id for entry_id, state in dialog._states.items() if state.done]
        assert [values[i] for i in cached] == ["Agave tequilana"]
        assert "1 個已查過" in dialog.status.value
        dialog._query_all()
        assert dialog.job.total == 2
        assert dialog.query_all_btn.disabled is True  # 全部都有結果
    finally:
        db.close()


def test_species_suggestion_rerenders_on_bulk_results(isolated_species_cache):
    from app.views.moddb.species_bulk_lookup import bulk_lookup_for
    from app.views.moddb.species_suggestion import SpeciesSuggestion

    page = mock_page()
    changes = []
    suggestion = SpeciesSuggestion(page, lambda _t: None, lambda: changes.append(1))
    suggestion._results["Ginkgo biloba"] = FAILED
    species_cache._species_cache_data["Ginkgo biloba"] = "銀杏"
    bulk_lookup_for(page)._notify("lookup", 1)
    assert changes == [1]
    texts = _texts(suggestion.build(_entry("block.m.x.latin", "Ginkgo biloba")))
    assert "銀杏" in texts and "套用" in texts


def test_bulk_lookup_listeners_do_not_keep_closed_views_alive():
    import gc

    from app.views.moddb.species_bulk_lookup import SpeciesBulkLookup

    job = SpeciesBulkLookup(mock_page())

    class _View:
        def on_event(self, _event, _entry_id):
            raise AssertionError("released view must not be called")

    view = _View()
    job.add_listener(view.on_event)
    del view
    gc.collect()
    job._notify("finished")


def test_close_button_stops_bulk_lookup(tmp_path, monkeypatch):
    launcher = _DeferredLauncher()
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND], launcher=launcher)
    try:
        dialog._query_all()
        dialog._close()  # 「關閉」是真的結束：停止查詢
        launcher.run()
        assert dialog.job.stop_reason == "cancelled"
        assert not dialog.job.running
    finally:
        db.close()


def test_open_entry_and_dismiss_keep_bulk_lookup_running(tmp_path, monkeypatch):
    launcher = _DeferredLauncher()
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND] * 3, launcher=launcher)
    try:
        dialog._query_all()
        dialog._open_entry(dialog.result.candidates[0].entry_id)
        dialog._on_dismiss()
        launcher.run()
        assert dialog.job.stop_reason == "completed"
    finally:
        db.close()


def test_query_all_button_turns_grey_when_disabled(tmp_path, monkeypatch):
    import flet as ft

    db, dialog = _make_dialog(tmp_path, monkeypatch, [])
    try:
        style = dialog.query_all_btn.style
        assert ft.ControlState.DISABLED in style.bgcolor
        assert (
            style.bgcolor[ft.ControlState.DISABLED]
            != style.bgcolor[ft.ControlState.DEFAULT]
        )
        assert ft.ControlState.DISABLED in style.color
    finally:
        db.close()


# ---------------------------------------------------------------- 審查回歸
def test_species_quality_filter_matches_between_list_and_batch_replace(tmp_path):
    from translation_tool.translation_db import EntryFilter, QualityFilter

    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest(
            "1.21.1",
            [
                ScanItem(
                    KIND_LANG, "m", "block.m.olive.latin", "Olea europaea", "橄欖"
                ),
                ScanItem(KIND_LANG, "m", "misc.mint", "Mentha × piperita", "橄欖薄荷"),
                ScanItem(KIND_LANG, "m", "misc.olive", "Olea europaea", "橄欖"),
                ScanItem(KIND_LANG, "m", "gui.energy", "Energy usage", "橄欖"),
            ],
        )
        criteria = EntryFilter(
            version="1.21.1", quality=QualityFilter(status="species_name")
        )
        rows, total = db.list_entries(criteria=criteria, limit=None)
        plan = db.preview_batch_replace(criteria, "橄欖", "油橄欖")
    finally:
        db.close()
    assert total == 2
    assert len(plan.changes) == total
    assert {change.entry_id for change in plan.changes} == {row.id for row in rows}


@pytest.mark.parametrize(
    "value",
    [" Olea europaea", "Olea\teuropaea", "Olea  europaea", "Olea　europaea"],
)
def test_species_format_prefilter_keeps_whitespace_variants(tmp_path, value):
    from translation_tool.translation_db import EntryFilter, QualityFilter

    db = TranslationDB(tmp_path / "mod.db")
    try:
        db.ingest("1.21.1", [ScanItem(KIND_LANG, "m", "misc.a", value)])
        criteria = EntryFilter(
            version="1.21.1", quality=QualityFilter(status="species_format")
        )
        _rows, total = db.list_entries(criteria=criteria, limit=None)
    finally:
        db.close()
    assert moddb_species_service.is_potential_species_name(value)
    assert total == 1


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (None, species_cache.STATUS_NOT_FOUND),
        (_http_error(429), species_cache.STATUS_RATE_LIMITED),
        (requests.Timeout(), species_cache.STATUS_ERROR),
    ],
)
def test_concurrent_lookups_share_not_found_and_failures(
    isolated_species_cache, monkeypatch, raised, status
):
    calls = []
    release = threading.Event()

    def slow_fetch(name):
        calls.append(name)
        release.wait(2)
        if raised is not None:
            raise raised

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
    while not calls:
        threading.Event().wait(0.01)
    threading.Event().wait(0.1)  # 讓其他執行緒進入等待
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(calls) == 1
    assert [r.status for r in results] == [status] * 4
    cached = species_cache.cached_species_lookup("Olea europaea")
    species_cache.lookup_species("Olea europaea")
    if status == species_cache.STATUS_NOT_FOUND:
        # 查無是確定結果：寫入快取，之後不再連線
        assert cached.status == species_cache.STATUS_NOT_FOUND
        assert len(calls) == 1
    else:
        # 暫時失敗不保存：之後再查會重新連線
        assert cached is None
        assert len(calls) == 2


def test_open_species_entry_redraws_page(tmp_path):
    db = _seed(tmp_path / "mod.db")
    try:
        page = mock_page()
        panel = EntriesPanel(page, lambda: db)
        panel.refresh()
        entry_id = panel.rows[1].id
        before = page.updated
        panel.open_species_entry(entry_id)
        assert page.updated > before
        assert panel.selected.id == entry_id
    finally:
        db.close()


# ---------------------------------------------------------------- 查無保存、再查一次、上一層物種
def test_not_found_is_persisted_and_reloaded(
    isolated_species_cache, monkeypatch, tmp_path
):
    cache_file = tmp_path / "species_cache.tsv"
    monkeypatch.setattr(species_cache, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(species_cache, "_query_wikipedia_title", lambda _name: None)

    result = species_cache.lookup_species("Olea europaea")
    assert result.status == species_cache.STATUS_NOT_FOUND
    assert cache_file.read_text(encoding="utf-8").splitlines() == ["Olea europaea\t"]

    # 重新載入快取檔：查無仍是確定結果
    monkeypatch.setattr(species_cache, "_initialized", False)
    monkeypatch.setattr(species_cache, "_species_cache_data", None)
    monkeypatch.setattr(species_cache, "resolve_project_path", lambda _p: tmp_path)
    monkeypatch.setattr(
        species_cache,
        "load_config",
        lambda: {"species_cache": {"cache_filename": cache_file.name}},
    )
    assert species_cache.initialize_species_cache()
    cached = species_cache.cached_species_lookup("Olea europaea")
    assert cached.status == species_cache.STATUS_NOT_FOUND


def test_refresh_bypasses_cached_not_found(isolated_species_cache, monkeypatch):
    species_cache._species_cache_data["Ginkgo biloba"] = ""
    calls = []

    def fetch(name):
        calls.append(name)
        return "銀杏"

    monkeypatch.setattr(species_cache, "_query_wikipedia_title", fetch)
    assert species_cache.lookup_species("Ginkgo biloba").status == "not_found"
    assert calls == []
    refreshed = species_cache.lookup_species("Ginkgo biloba", refresh=True)
    assert refreshed.value == "銀杏" and calls == ["Ginkgo biloba"]
    assert species_cache.cached_species_lookup("Ginkgo biloba").value == "銀杏"


@pytest.mark.parametrize(
    ("name", "parent"),
    [
        ("Fagus sylvatica purpurea", "Fagus sylvatica"),
        ("Apium graveolens var. dulce", "Apium graveolens"),
        ("Mentha × piperita citrata", "Mentha × piperita"),
        ("Mentha × piperita", None),
        ("Olea europaea", None),
        ("Steel Casing", None),
    ],
)
def test_parent_species_name(name, parent):
    from translation_tool.utils.species_names import parent_species_name

    assert parent_species_name(name) == parent


def test_not_found_infraspecific_name_carries_parent_reference(
    isolated_species_cache, monkeypatch
):
    species_cache._species_cache_data["Fagus sylvatica"] = "歐洲山毛櫸"
    monkeypatch.setattr(species_cache, "_query_wikipedia_title", lambda _name: None)

    result = species_cache.lookup_species("Fagus sylvatica purpurea")
    assert result.status == species_cache.STATUS_NOT_FOUND
    assert result.value is None  # 參考名稱不會變成答案
    assert (result.parent_name, result.parent_value) == (
        "Fagus sylvatica",
        "歐洲山毛櫸",
    )
    cached = species_cache.cached_species_lookup("Fagus sylvatica purpurea")
    assert cached.parent_value == "歐洲山毛櫸"


def test_parent_reference_is_looked_up_when_not_cached(
    isolated_species_cache, monkeypatch
):
    answers = {"Fagus sylvatica purpurea": None, "Fagus sylvatica": "歐洲山毛櫸"}
    monkeypatch.setattr(species_cache, "_query_wikipedia_title", answers.get)
    result = species_cache.lookup_species("Fagus sylvatica purpurea")
    assert result.parent_value == "歐洲山毛櫸"
    assert species_cache.cached_species_lookup("Fagus sylvatica").value == "歐洲山毛櫸"


def test_dialog_shows_cached_not_found_and_offers_requery(tmp_path, monkeypatch):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [FOUND])
    try:
        species_cache._species_cache_data["Agave tequilana"] = ""
        dialog._scan()
        assert "1 個已查過" in dialog.status.value
        candidate = dialog.result.candidates[0]
        state = dialog._states[candidate.entry_id]
        assert state.lookup.status == species_cache.STATUS_NOT_FOUND
        row_texts = _texts([dialog.rows.controls[0]])
        assert "再查一次" in row_texts and "查詢" not in row_texts
        dialog._query_one(candidate)  # 已有確定結果：一般查詢不重查
        assert state.lookup.status == species_cache.STATUS_NOT_FOUND
        dialog._query_one(candidate, refresh=True)
        assert state.lookup is FOUND
    finally:
        db.close()


def test_dialog_row_shows_parent_reference(tmp_path, monkeypatch):
    db, dialog = _make_dialog(tmp_path, monkeypatch, [])
    try:
        candidate = dialog.result.candidates[0]
        state = dialog._states[candidate.entry_id]
        state.lookup = species_cache.SpeciesLookup(
            species_cache.STATUS_NOT_FOUND,
            parent_name="Agave americana",
            parent_value="龍舌蘭",
        )
        status, _color = dialog._status_for(state)
        assert status == (
            "找不到對應條目；上一層物種 Agave americana：龍舌蘭（僅供參考）"
        )
    finally:
        db.close()


def _find_button(control, label):
    if getattr(control, "content", None) == label:
        return control
    for child in getattr(control, "controls", None) or [
        getattr(control, "content", None)
    ]:
        if (
            child is not None
            and not isinstance(child, str)
            and (found := _find_button(child, label))
        ):
            return found
    return None


def test_species_suggestion_offers_parent_reference(isolated_species_cache):
    from app.views.moddb.species_suggestion import SpeciesSuggestion

    applied = []
    species_cache._species_cache_data["Fagus sylvatica purpurea"] = ""
    species_cache._species_cache_data["Fagus sylvatica"] = "歐洲山毛櫸"
    suggestion = SpeciesSuggestion(mock_page(), applied.append, lambda: None)
    controls = suggestion.build(
        _entry("block.m.copper_beech.latin", "Fagus sylvatica purpurea")
    )
    texts = _texts(controls)
    assert "維基百科找不到對應條目" in texts
    assert "上一層物種 Fagus sylvatica：歐洲山毛櫸" in texts
    assert "套用參考名稱" in texts and "再查一次" in texts

    button = next(b for c in controls if (b := _find_button(c, "套用參考名稱")))
    button.on_click(None)
    assert applied == ["歐洲山毛櫸"]
