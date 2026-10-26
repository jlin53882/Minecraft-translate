"""Mod 資料庫頁的回應速度契約：資料庫查詢離開畫面執行緒、過期結果被丟棄、更新範圍受限。

不測牆鐘時間，只測行為：哪個執行緒查詢、較舊的結果會不會蓋掉較新的、
哪些區塊被更新、同一批控制項是否被重複使用。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.services_impl import moddb_service
from app.tasks.operation_registry import OperationRegistry
from app.views import moddb_view
from app.views.moddb import entries_panel
from app.views.moddb.entry_row_tile import EntryTilePool
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)

MAIN = threading.current_thread().name


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "web.db"
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(path), version="1.21.1"),
    )
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", f"item.foo.{i:03d}", f"Text {i}", f"文字 {i}")
            for i in range(120)
        ],
    )
    db.close()
    return path


def _row(entry_id: int, **overrides):
    """清單列測試用的條目（只含 ``EntryTilePool`` 讀到的欄位）。"""
    values = {
        "id": entry_id,
        "en_us": f"Text {entry_id}",
        "zh_tw": f"文字 {entry_id}",
        "key": f"item.foo.{entry_id:03d}",
        "state": "ok",
        "source": None,
        "review_status": None,
        "quality_state": "consistent",
        "quality_issues": (),
        "whitespace_note": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _scheduled_page():
    page = mock_page()
    page.operation_registry = OperationRegistry()
    page.run_thread = lambda _target: None
    return page


def _settle(page) -> None:
    """等背景工作做完，再把排回畫面執行緒的結果跑完。"""
    for _ in range(5):
        assert page.operation_registry.wait_for_idle(timeout=5)
        page._run_all_tasks()


@pytest.fixture
def view(db_path):
    page = _scheduled_page()
    view = moddb_view.ModDbView(page, mock_filepicker())
    view.show_tab("entries")
    _settle(page)
    assert view.entries.total == 120
    yield view
    page.operation_registry.begin_shutdown()
    page.operation_registry.wait_for_idle(timeout=5)
    if view._db is not None:
        view._db.close()


def _record_threads(monkeypatch, name: str) -> list[str]:
    seen: list[str] = []
    original = getattr(TranslationDB, name)

    def wrapper(self, *args, **kwargs):
        seen.append(threading.current_thread().name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(TranslationDB, name, wrapper)
    return seen


# ------------------------------------------------------------------ 翻頁
def test_paging_queries_in_a_worker_and_shows_loading_state(view, monkeypatch):
    threads = _record_threads(monkeypatch, "list_entries")
    entries = view.entries

    entries.pager.goto(2)

    # 點擊當下：頁碼已切換、清單變暗、顯示載入中；資料列還是舊的（沒有在畫面執行緒查詢）
    assert entries.pager.current_page == 2
    assert entries.refresh_indicator.visible is True
    assert entries.list_shell.opacity < 1.0
    assert threads == []

    _settle(view._page)
    assert threads and MAIN not in threads
    assert entries.rows[0].key == "item.foo.050"
    assert entries.list_shell.opacity == 1.0
    assert entries.refresh_indicator.visible is False


def test_paging_does_not_reload_filter_options(view, monkeypatch):
    mods = _record_threads(monkeypatch, "mods")
    view.entries.pager.goto(2)
    _settle(view._page)
    assert mods == []  # 只換頁：版本／模組／來源選項沒有重算


def test_rapid_paging_applies_only_the_latest_page(view):
    entries = view.entries
    entries.pager.goto(2)
    entries.pager.goto(3)  # 第 2 頁的結果還沒回來就又換頁
    _settle(view._page)
    assert entries.pager.current_page == 3
    assert entries.rows[0].key == "item.foo.100"
    assert len(entries.rows) == 20


# ------------------------------------------------------------------ 選取
def test_select_loads_detail_in_a_worker_and_locks_editor(view, monkeypatch):
    threads = _record_threads(monkeypatch, "entry_detail")
    entries = view.entries
    target = entries.rows[3]

    entries.select(target.id)

    assert entries.editor_card.disabled is True  # 載入中不能編輯上一筆的內容
    assert entries.selected.id != target.id
    assert MAIN not in threads
    visible = [t for t in entries.list_view.controls if t.visible]
    assert [t.bgcolor is not None for t in visible].count(True) == 1
    assert next(t for t in visible if t.bgcolor is not None).data == target.id

    _settle(view._page)
    assert threads and MAIN not in threads
    assert entries.selected.id == target.id
    assert entries.editor_card.disabled is False


def test_stale_selection_result_is_discarded(view):
    entries = view.entries
    first, second = entries.rows[1], entries.rows[2]
    entries.select(first.id)
    entries.select(second.id)  # 第一筆還沒讀完就點了第二筆
    _settle(view._page)
    assert entries.selected.id == second.id
    assert entries.tw_field.value == second.zh_tw


def test_pending_selection_never_stores_previous_text_as_draft(view):
    entries = view.entries
    current = entries.selected
    entries.tw_field.value = "尚未儲存的草稿"
    entries.select(entries.rows[4].id)
    # 載入期間輸入框仍是上一筆：這時的變更事件不能把內容記到新條目底下
    entries._on_text_change()
    _settle(view._page)
    assert entries._editor_drafts.get(entries.selected.id) is None
    assert entries._editor_drafts.get(current.id) == "尚未儲存的草稿"


# ------------------------------------------------------------------ 輸入
def test_typing_does_not_query_the_database_on_the_ui_thread(view, monkeypatch):
    threads = _record_threads(monkeypatch, "preview_manual")
    entries = view.entries
    entries._edit_debounce = SimpleNamespace(
        call=lambda fn, *args: fn(*args), cancel=lambda: None
    )
    entries.tw_field.value = "新的譯文"

    entries._on_text_change()

    assert entries.save_btn.disabled is False  # 按鈕狀態立刻更新
    assert MAIN not in threads  # 影響預覽在背景
    _settle(view._page)
    assert threads and MAIN not in threads
    assert entries.impact_box.visible is True


def test_typing_burst_renders_heavy_preview_once(view):
    entries = view.entries
    calls: list[int] = []
    scheduled: list = []
    entries._edit_debounce = SimpleNamespace(
        call=lambda fn, *args: scheduled.append(fn), cancel=lambda: None
    )
    entries._refresh_after_edit = lambda: calls.append(1)
    for text in ("a", "ab", "abc"):
        entries.tw_field.value = text
        entries._on_text_change()
    assert calls == []  # 停筆前不重畫格式提示／預覽
    scheduled[-1]()
    assert calls == [1]


# ------------------------------------------------------------------ 清單列重用
def test_tile_pool_reuses_the_same_controls_across_pages(db_path):
    db = TranslationDB(db_path)
    rows, _ = db.list_entries(criteria=moddb_service.EntryFilter(version="1.21.1"))
    pool = EntryTilePool(entries_panel.PAGE_SIZE, lambda _id: None)
    before = list(pool.list_view.controls)

    pool.set_rows(
        rows[:50], selected_id=rows[0].id, marks={}, catalog=db.source_catalog
    )
    pool.set_rows(rows[50:70], selected_id=None, marks={}, catalog=db.source_catalog)

    assert list(pool.list_view.controls) == before  # 沒有新增或移除控制項
    assert sum(t.visible for t in pool.list_view.controls) == 20
    assert [t.data for t in pool.list_view.controls if t.visible] == [
        r.id for r in rows[50:70]
    ]
    assert pool.set_selected(rows[55].id) == [pool.tile_for(rows[55].id)]
    db.close()


# ------------------------------------------------------------------ 更新範圍
def test_moddb_tree_is_an_update_boundary(view):
    assert view.is_isolated()
    for panel in view._panels.values():
        assert panel.is_isolated()
    entries = view.entries
    for region in (
        entries.filter_card,
        entries.list_view,
        entries.editor_card,
        entries.history_card,
    ):
        assert region.is_isolated()


def test_moddb_updates_never_use_a_page_wide_update(view):
    page = view._page
    page_wide: list[tuple] = []
    original = page.update

    def record(*controls):
        if not controls:
            page_wide.append(controls)
        return original(*controls)

    page.update = record
    view.entries.pager.goto(2)
    _settle(page)
    view.entries.select(view.entries.rows[2].id)
    _settle(page)
    view.show_tab("scan")
    _settle(page)
    view.show_tab("translate")
    _settle(page)
    assert page_wide == []  # 只更新指定區塊，沒有整頁 page.update()


def test_translate_scope_change_counts_in_the_background(view, monkeypatch):
    threads = _record_threads(monkeypatch, "count_untranslated")
    view.show_tab("translate")
    _settle(view._page)
    threads.clear()
    translate = view.translate

    translate._on_limit_changed()

    assert threads == []  # 畫面執行緒不查筆數
    assert "正在計算" in translate.count_text.value
    _settle(view._page)
    assert threads and MAIN not in threads
    assert "未翻譯" in translate.count_text.value


# ------------------------------------------------------------------ 載入中提示與寫入後重畫
def test_loading_state_does_not_redraw_the_filter_card(view, monkeypatch):
    regions: list[tuple] = []
    monkeypatch.setattr(
        entries_panel, "update_regions", lambda *controls: regions.append(controls)
    )

    view.entries.show_loading()

    assert regions == [(view.entries.refresh_indicator, view.entries.list_shell)]


def test_panel_refresh_is_handed_to_the_worker_before_the_loading_redraw(
    view, monkeypatch
):
    order: list[str] = []
    original_launch = view._launch_panel_refresh
    original_loading = view.entries.show_loading

    def launch(*args, **kwargs):
        order.append("launch")
        return original_launch(*args, **kwargs)

    def loading():
        order.append("loading")
        original_loading()

    monkeypatch.setattr(view, "_launch_panel_refresh", launch)
    monkeypatch.setattr(view.entries, "show_loading", loading)

    view.entries.pager.goto(2)

    assert order == ["launch", "loading"]  # 讀取先開跑，畫面差異計算與它重疊
    _settle(view._page)


def test_save_redraws_the_entries_panel_once_after_the_worker_snapshot(
    view, monkeypatch
):
    entries = view.entries
    entries._edit_debounce = SimpleNamespace(
        call=lambda fn, *args: fn(*args), cancel=lambda: None
    )
    flushes: list[str] = []
    for name in ("flush", "flush_all"):
        original = getattr(entries, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            flushes.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(entries, name, counted)
    entries.tw_field.value = "新的譯文"
    entries._on_text_change()
    _settle(view._page)
    flushes.clear()

    entries._save()
    _settle(view._page)

    # 寫入完成只顯示輕量的載入中提示，整個條目面板在新快照套用後才重畫一次
    assert flushes == ["flush"]
    assert entries.selected.zh_tw == "新的譯文"
    assert entries.saved_text.value


def _prepare_edit(view, text):
    entries = view.entries
    entries._edit_debounce = SimpleNamespace(
        call=lambda fn, *args: fn(*args), cancel=lambda: None
    )
    entries.tw_field.value = text
    entries._on_text_change()
    _settle(view._page)
    return entries


def test_save_updates_only_the_changed_row_not_the_whole_list(view, monkeypatch):
    entries = _prepare_edit(view, "只改這一列")
    saved_id = entries.selected.id
    regions = _record_regions(monkeypatch)

    entries._save()
    _settle(view._page)

    saved_box = entries.tiles.tile_for(saved_id)
    assert saved_box in regions
    # 整個清單的 diff 要走訪全部 50 列；儲存只改了一列，其餘列不重畫
    assert entries.list_view not in regions
    assert regions.count(saved_box) == 1
    assert not any(
        box in regions for box in entries.tiles.boxes() if box is not saved_box
    )


def test_save_does_not_recompute_what_the_snapshot_redraws(view, monkeypatch):
    """背景快照會重畫編輯區與影響預覽，儲存結果不再另外算一次影響預覽、重畫編輯區。"""
    entries = _prepare_edit(view, "不重複計算")
    previews = _record_threads(monkeypatch, "preview_manual")
    previews.clear()
    editor_flushes: list[int] = []
    original = entries._flush_editor
    monkeypatch.setattr(
        entries, "_flush_editor", lambda: (editor_flushes.append(1), original())
    )

    entries._save()
    _settle(view._page)

    assert entries.selected.zh_tw == "不重複計算"
    assert previews == []
    assert editor_flushes == []


def test_write_results_request_the_reload_before_showing_the_snack(view, monkeypatch):
    """提示的畫面差異計算與資料庫重載可以重疊：先交出背景重載請求，再顯示提示。"""
    from app.views.moddb import entry_save_result

    entries = _prepare_edit(view, "先重載再提示")
    events: list[str] = []
    original_request = view._request_panel_refresh
    monkeypatch.setattr(
        view,
        "_request_panel_refresh",
        lambda *a, **k: (events.append("reload"), original_request(*a, **k))[1],
    )
    monkeypatch.setattr(
        entry_save_result, "show_snack", lambda *a, **k: events.append("snack")
    )

    entries._save()
    _settle(view._page)

    assert events.index("reload") < events.index("snack")


def test_tile_pool_reports_only_the_rows_that_changed():
    pool = EntryTilePool(4, lambda _id: None)
    rows = [_row(i) for i in range(1, 4)]

    first = pool.set_rows(rows, selected_id=None, marks=None, catalog=None)
    assert [b.data for b in first] == [1, 2, 3]  # 第一次：每一列都是新內容

    same = pool.set_rows(rows, selected_id=None, marks=None, catalog=None)
    assert same == []  # 完全相同：沒有任何一列要更新

    rows[1] = _row(2, zh_tw="改過的譯文")
    one = pool.set_rows(rows, selected_id=None, marks=None, catalog=None)
    assert [b.data for b in one] == [2]

    selected = pool.set_rows(rows, selected_id=3, marks=None, catalog=None)
    assert [b.data for b in selected] == [3]  # 只有選取底色變的那一列

    shorter = pool.set_rows(rows[:2], selected_id=3, marks=None, catalog=None)
    assert len(shorter) == 1  # 第 3 列被隱藏
    assert shorter[0].visible is False


def test_tile_pool_updates_the_whole_list_when_most_rows_changed():
    from app.views.moddb.entry_row_tile import MAX_ROW_UPDATES

    pool = EntryTilePool(50, lambda _id: None)
    rows = [_row(i) for i in range(1, 51)]
    pool.set_rows(rows, selected_id=None, marks=None, catalog=None)

    # 換頁、換篩選：幾乎每一列都變，一次清單 diff 比 50 次逐列 patch 便宜
    new_page = [_row(i + 100) for i in range(1, 51)]
    assert pool.set_rows(new_page, selected_id=None, marks=None, catalog=None) is None

    few = [
        *new_page[:-MAX_ROW_UPDATES],
        *[_row(i + 200) for i in range(MAX_ROW_UPDATES)],
    ]
    changed = pool.set_rows(few, selected_id=None, marks=None, catalog=None)
    assert changed is not None and len(changed) == MAX_ROW_UPDATES


def test_tile_pool_asks_for_the_whole_list_when_the_empty_state_changes():
    pool = EntryTilePool(2, lambda _id: None)
    empty = object()
    rows = [_row(1)]

    assert (
        pool.set_rows([], selected_id=None, marks=None, catalog=None, empty=empty)
        is None
    )
    # 離開空狀態：清單的控制項組成改變，不能只更新幾列
    assert pool.set_rows(rows, selected_id=None, marks=None, catalog=None) is None
    assert pool.set_rows(rows, selected_id=None, marks=None, catalog=None) == []


def test_save_failure_still_recomputes_the_impact_in_the_background(view, monkeypatch):
    threads = _record_threads(monkeypatch, "preview_manual")
    entries = view.entries
    entries._edit_debounce = SimpleNamespace(
        call=lambda fn, *args: fn(*args), cancel=lambda: None
    )
    entries.tw_field.value = "會失敗的譯文"
    entries._on_text_change()
    _settle(view._page)
    threads.clear()

    entries._update_impact()

    assert MAIN not in threads  # 畫面執行緒不查資料庫
    _settle(view._page)
    assert threads and MAIN not in threads


# ------------------------------------------------------------------ 人工審核
def _select_first_reviewable(view):
    entries = view.entries
    entries._edit_debounce = SimpleNamespace(
        call=lambda fn, *args: fn(*args), cancel=lambda: None
    )
    row = next(r for r in entries.rows if r.zh_tw)
    entries.select(row.id)
    _settle(view._page)
    entries.tw_field.value = entries.selected.zh_tw
    entries._on_text_change()
    _settle(view._page)
    return entries


def test_review_scope_preview_is_read_in_a_worker(view, monkeypatch):
    threads = _record_threads(monkeypatch, "preview_manual_review")
    entries = _select_first_reviewable(view)
    threads.clear()
    page = view._page

    entries._confirm()

    assert MAIN not in threads  # 按下審核當下不查資料庫
    assert not any(getattr(c, "title", None) for c in page.overlay)
    _settle(page)
    assert threads and MAIN not in threads
    assert page.overlay[-1].title.value == "確認條目審核"


def test_review_scope_switch_recomputes_in_a_worker_and_updates_only_the_dialog(
    view, monkeypatch
):
    entries = _select_first_reviewable(view)
    page = view._page
    entries._confirm()
    _settle(page)
    dialog = page.overlay[-1]
    switch = next(
        c for c in _walk_controls(dialog.content) if type(c).__name__ == "Switch"
    )
    threads = _record_threads(monkeypatch, "preview_manual_review")
    page_wide: list[tuple] = []
    original = page.update
    page.update = lambda *controls: (
        page_wide.append(controls) if not controls else None,
        original(*controls),
    )[1]

    switch.value = False
    switch.on_change(None)

    assert MAIN not in threads
    _settle(page)
    assert threads and MAIN not in threads
    assert page_wide == []  # 只更新確認視窗


def _walk_controls(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from _walk_controls(child)
    inner = getattr(control, "content", None)
    if inner is not None:
        yield from _walk_controls(inner)


def test_clear_review_writes_in_a_worker_and_reloads_in_place(view, monkeypatch):
    entries = _select_first_reviewable(view)
    page = view._page
    entries._confirm()
    _settle(page)
    page.overlay[-1].actions[-1].on_click(None)  # 確認審核
    _settle(page)
    assert entries.selected.review_status == "reviewed"
    assert entries.clear_review_btn.visible is True
    threads = _record_threads(monkeypatch, "clear_manual_review")

    entries.clear_review_btn.on_click(None)

    assert MAIN not in threads  # 畫面執行緒不寫資料庫
    _settle(page)
    assert threads and MAIN not in threads
    assert entries.selected.review_status == "unreviewed"
    assert entries.clear_review_btn.visible is False
    assert entries.confirm_btn.visible is True


def test_typing_in_the_search_box_suppresses_the_automatic_update(view, monkeypatch):
    suppressed: list[int] = []
    monkeypatch.setattr(
        entries_panel, "suppress_auto_update", lambda: suppressed.append(1)
    )
    entries = view.entries

    entries._on_search_input(SimpleNamespace(control=SimpleNamespace(value="ab")))

    assert suppressed == [1]  # 逐字輸入不對篩選列補一次自動更新


def _record_regions(monkeypatch) -> list:
    regions: list = []
    original = entries_panel.update_regions

    def recorder(*controls):
        regions.extend(controls)
        return original(*controls)

    monkeypatch.setattr(entries_panel, "update_regions", recorder)
    return regions


def test_reloading_unchanged_options_does_not_redraw_the_filter_card(view, monkeypatch):
    regions = _record_regions(monkeypatch)
    entries = view.entries

    view._request_panel_refresh("entries")  # 完整快照，選項、選值與每一列都沒變
    _settle(view._page)

    filter_controls = (
        entries.filter_card,
        entries.mod_dd,
        entries.kind_dd,
        entries.version_dd,
        entries.source_filter.dropdown,
    )
    assert not any(control in regions for control in filter_controls)
    assert entries.list_view not in regions  # 沒有任何一列有變：清單不必 diff 50 列
    assert not any(box in regions for box in entries.tiles.boxes())


def test_changed_options_redraw_only_the_changed_dropdowns(view, monkeypatch):
    regions = _record_regions(monkeypatch)
    entries = view.entries

    entries._on_state("none")  # 沒有未翻譯條目：模組與來源選項縮減
    _settle(view._page)

    assert entries.mod_dd in regions
    assert entries.filter_card not in regions  # 只更新有變的下拉選單，不重畫整張篩選列
    assert entries.kind_dd not in regions
    assert entries.version_dd not in regions


def test_advanced_summary_is_flushed_even_when_the_options_do_not_change(
    view, monkeypatch
):
    """品質／排序這類條件只改摘要文字：選項沒變時摘要也要送到畫面（不能等下一次選項更新）。"""
    entries = view.entries
    regions = _record_regions(monkeypatch)

    entries.advanced_filters.sort.value = "entry_newest"
    entries.advanced_filters._selection_changed()
    _settle(view._page)

    assert entries.advanced_filters.active_text.value == "已套用 1 項進階條件"
    assert entries.filter_actions_row in regions
    assert entries.filter_card not in regions


# ------------------------------------------------------------------ 搜尋：清單先、選項後
def _two_mod_view(view, db_path):
    """加入第二個模組，讓搜尋後的模組選項真的會縮小。"""
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "bar", "item.bar.001", "Other text", "其他文字")],
    )
    db.close()
    view._invalidate_database_snapshots()
    view.entries.refresh()
    view._request_panel_refresh("entries")
    _settle(view._page)


def test_search_shows_the_list_before_the_filter_options(view, db_path, monkeypatch):
    _two_mod_view(view, db_path)
    entries = view.entries
    events: list[str] = []
    for name, label in (
        ("list_entries", "list-query"),
        ("filter_option_values", "options-query"),
    ):
        original = getattr(TranslationDB, name)

        def wrapper(self, *args, _o=original, _l=label, **kwargs):
            events.append(_l)
            return _o(self, *args, **kwargs)

        monkeypatch.setattr(TranslationDB, name, wrapper)
    for name, label in (
        ("apply_refresh_snapshot", "list-applied"),
        ("apply_options_snapshot", "options-applied"),
    ):
        original = getattr(entries, name)

        def hook(*args, _o=original, _l=label, **kwargs):
            events.append(_l)
            return _o(*args, **kwargs)

        monkeypatch.setattr(entries, name, hook)
    events.clear()

    entries.search.value = "Other"
    entries._apply_search(entries.search)
    _settle(view._page)

    assert [r.key for r in entries.rows] == ["item.bar.001"]
    assert events.index("list-applied") < events.index("options-query")
    assert events.index("options-query") < events.index("options-applied")
    assert [o.key for o in entries.mod_dd.options] == ["全部模組", "bar"]


def test_filter_option_queries_run_in_a_worker(view, db_path, monkeypatch):
    _two_mod_view(view, db_path)
    threads = _record_threads(monkeypatch, "filter_option_values")
    view.entries.search.value = "Other"
    view.entries._apply_search(view.entries.search)
    assert threads == []
    _settle(view._page)
    assert threads and MAIN not in threads


def test_unchanged_filter_options_do_not_redraw_the_filter_card(
    view, db_path, monkeypatch
):
    entries = view.entries
    regions: list[tuple] = []
    original = entries_panel.update_regions
    monkeypatch.setattr(
        entries_panel,
        "update_regions",
        lambda *controls: (regions.append(controls), original(*controls))[1],
    )

    entries.search.value = "Text 1"
    entries._apply_search(entries.search)
    _settle(view._page)
    regions.clear()

    entries.search.value = "Text 10"  # 模組與來源選項和上一次相同
    entries._apply_search(entries.search)
    _settle(view._page)

    assert entries.total > 0
    assert all(entries.filter_card not in controls for controls in regions)


def test_filter_options_loader_keeps_only_the_latest_request():
    from app.views.moddb.filter_options_loader import FilterOptionsLoader

    page = _scheduled_page()
    gate = threading.Event()
    loaded: list[str] = []
    applied: list[tuple[str, dict]] = []

    def load(payload, interrupt):
        loaded.append(payload)
        gate.wait(5)
        return {"payload": payload}

    loader = FilterOptionsLoader(
        page, load, lambda payload, snapshot: applied.append((payload, snapshot))
    )
    loader.request("a")
    loader.request("b")  # a 還在跑：b、c 只保留最新的 c
    loader.request("c")
    gate.set()
    _settle(page)

    assert loaded == ["a", "c"]
    assert applied == [("c", {"payload": "c"})]
    assert loader.running is False


def test_filter_options_loader_discards_results_after_cancel():
    from app.views.moddb.filter_options_loader import FilterOptionsLoader

    page = _scheduled_page()
    gate = threading.Event()
    applied: list[str] = []
    loader = FilterOptionsLoader(
        page,
        lambda payload, interrupt: (gate.wait(5), {})[1],
        lambda payload, snapshot: applied.append(payload),
    )
    loader.request("a")
    loader.cancel()
    gate.set()
    _settle(page)
    assert applied == []


def test_stale_filter_options_are_not_applied_after_the_query_changed(
    view, db_path, monkeypatch
):
    _two_mod_view(view, db_path)
    entries = view.entries
    applied: list[dict] = []
    original = entries.apply_options_snapshot
    monkeypatch.setattr(
        entries,
        "apply_options_snapshot",
        lambda snapshot: (applied.append(snapshot), original(snapshot))[1],
    )
    entries.search.value = "Other"
    entries._apply_search(entries.search)
    assert view._page.operation_registry.wait_for_idle(timeout=5)
    view._page._run_all_tasks()  # 第一階段的清單套用；選項的背景查詢隨後開始
    entries.query = "Text"  # 選項結果回來之前，搜尋條件又變了
    _settle(view._page)

    assert applied == []
    assert [o.key for o in entries.mod_dd.options] == ["全部模組", "bar", "foo"]


def test_newer_filter_request_interrupts_the_running_query(view, monkeypatch):
    """舊查詢被中斷，結果不顯示；最後顯示的是最新條件的清單。"""
    from app.services_impl.moddb_service import QueryCancelled

    entries = view.entries
    original = moddb_view.load_entries_filter_snapshot
    seen: list[str] = []
    first_started = threading.Event()

    def load(settings, request, interrupt=None):
        query = request["criteria"].query
        seen.append(query)
        if query == "Text 1" and not first_started.is_set():
            first_started.set()
            # 模擬還在掃描：等較新的請求把它中斷
            assert interrupt is not None
            for _ in range(500):
                if interrupt.cancelled:
                    raise QueryCancelled
                threading.Event().wait(0.01)
        return original(settings, request, interrupt)

    monkeypatch.setattr(moddb_view, "load_entries_filter_snapshot", load)
    entries.search.value = "Text 1"
    entries._apply_search(entries.search)
    assert first_started.wait(5)
    entries.search.value = "Text 2"
    entries._apply_search(entries.search)
    _settle(view._page)

    assert seen == ["Text 1", "Text 2"]
    assert entries.query == "Text 2"
    assert entries.rows and all("2" in r.en_us for r in entries.rows)
    assert entries.refresh_indicator.visible is False


def test_search_typing_burst_queries_once_after_the_pause(view, monkeypatch):
    entries = view.entries
    scheduled: list = []
    entries._search_debounce = SimpleNamespace(
        call=lambda fn, *args: scheduled.append(lambda: fn(*args)),
        cancel=lambda: scheduled.clear(),
    )
    threads = _record_threads(monkeypatch, "list_entries")
    for text in ("T", "Te", "Tex", "Text 9"):
        entries.search.value = text
        entries._on_search_input(SimpleNamespace(control=entries.search))
    _settle(view._page)
    assert threads == []  # 停筆前沒有任何查詢

    scheduled[-1]()  # 只有最後一次輸入的排程會真的執行
    _settle(view._page)
    assert len(threads) == 1 and MAIN not in threads
    assert entries.query == "Text 9"


def test_search_debounce_is_short_enough_to_feel_immediate():
    # 舊查詢會被新條件中斷（QueryInterrupt），縮短等待不會造成每個字元都查詢的堆積
    assert entries_panel.SEARCH_DEBOUNCE_SECONDS <= 0.25


def test_filter_options_wait_for_the_conditions_to_settle(monkeypatch):
    """輸入中每個字都換條件：等待期間又來了新請求，舊的不查資料庫。"""
    from app.views.moddb import filter_options_loader
    from app.views.moddb.filter_options_loader import FilterOptionsLoader

    monkeypatch.setattr(filter_options_loader, "SETTLE_SECONDS", 5.0)
    page = _scheduled_page()
    loaded: list[str] = []
    applied: list[str] = []
    loader = FilterOptionsLoader(
        page,
        lambda payload, interrupt: loaded.append(payload) or {},
        lambda payload, snapshot: applied.append(payload),
    )
    loader.request("a")  # 還在等條件穩定
    monkeypatch.setattr(filter_options_loader, "SETTLE_SECONDS", 0.0)
    loader.request("b")  # 取代它：只有 b 會真的查
    _settle(page)

    assert loaded == ["b"]
    assert applied == ["b"]


def test_data_action_buttons_are_resent_when_loading_finishes(view, monkeypatch):
    """搜尋觸發載入時，若篩選卡在載入中被整張送出（按鈕停用），載入完成後按鈕必須再送出一次。

    篩選列的差異比對只記得自己送出的狀態；按鈕狀態改變時直接更新那幾顆按鈕，
    畫面才不會一直停在停用（點不了）。
    """
    entries = view.entries
    buttons = (
        entries.batch_replace_btn,
        entries.bulk_review_btn,
        entries.smart_review_btn,
        entries.species_scan_btn,
    )
    sent: list[tuple[str, bool]] = []
    for button in buttons:
        monkeypatch.setattr(
            button,
            "update",
            lambda b=button: sent.append((b.content, b.disabled)),
        )

    entries.set_data_loading(True)
    assert all(b.disabled for b in buttons)
    assert {disabled for _name, disabled in sent} == {True}
    sent.clear()

    entries.set_data_loading(False)
    assert not any(b.disabled for b in buttons)
    assert len(sent) == len(buttons)
    assert {disabled for _name, disabled in sent} == {False}

    sent.clear()
    entries.set_data_loading(False)  # 狀態沒變就不重送
    assert sent == []
