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
