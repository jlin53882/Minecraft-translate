"""「其他原文」頁籤：背景載入、篩選、並排檢視、沿用目前譯文、頁籤接線與過期結果。"""

from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

from app.services_impl import moddb_service
from app.tasks.operation_registry import OperationRegistry
from app.views import moddb_view
from app.views.moddb import variants_panel
from app.views.moddb.variants_panel import ALL_MODS, VariantsPanel
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.variant_listing import (
    STATUS_TRANSLATED,
    STATUS_UNTRANSLATED,
)

VERSION = "1.21.1"


def _item(mod, key, en, tw=""):
    return ScanItem(KIND_LANG, mod, key, en, tw, "")


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "variants.db"
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(path), version=VERSION),
    )
    db = TranslationDB(path)
    db.ingest(VERSION, [_item("foo", "item.foo.a", "Show Background", "顯示背景")])
    db.ingest(VERSION, [_item("bar", "item.bar.b", "Old Bar")])
    db.ingest(VERSION, [_item("foo", "item.foo.a", "Background Style", "背景樣式")])
    db.ingest(VERSION, [_item("foo", "item.foo.a", "Draw Layer")])
    db.ingest(VERSION, [_item("bar", "item.bar.b", "New Bar Text")])
    db.close()
    return path


@pytest.fixture
def db(db_path):
    d = TranslationDB(db_path)
    yield d
    d.close()


def _panel(db, **kwargs) -> VariantsPanel:
    return VariantsPanel(SimpleNamespace(), lambda: db, **kwargs)


def _texts(control) -> list[str]:
    """控制項樹裡所有文字（含 TextSpan）。"""
    found: list[str] = []

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, ft.Text):
            if node.value:
                found.append(node.value)
            found.extend(span.text for span in node.spans or [] if span.text)
        for name in ("content", "controls"):
            child = getattr(node, name, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None:
                walk(child)

    walk(control)
    return found


def _buttons(control, label: str) -> list[ft.Button]:
    found: list[ft.Button] = []

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, ft.Button) and node.content == label:
            found.append(node)
        for name in ("content", "controls"):
            child = getattr(node, name, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None:
                walk(child)

    walk(control)
    return found


def test_activate_loads_the_first_version_newest_first_and_selects_the_first_row(db):
    panel = _panel(db)
    panel.activate()
    assert panel.version == VERSION
    assert [r.en_us for r in panel.rows] == [
        "New Bar Text",
        "Draw Layer",
        "Background Style",
    ]
    assert panel.selected_id == panel.rows[0].variant_id
    assert panel.pager.total_items == 3
    assert "共 3 份" in panel.summary.value
    assert len(panel.list_col.controls) == 3
    assert not panel._stale
    # 模組與版本選單
    assert [o.key for o in panel.mod_dd.options] == [ALL_MODS, "bar", "foo"]
    assert panel.version_dd.value == VERSION


def test_status_mod_and_search_filters_reload_the_list(db):
    panel = _panel(db)
    panel.activate()
    panel.status_dd.value = STATUS_UNTRANSLATED
    panel._on_filter()
    assert sorted(r.en_us for r in panel.rows) == ["Draw Layer", "New Bar Text"]
    panel.status_dd.value = STATUS_TRANSLATED
    panel._on_filter()
    assert [r.en_us for r in panel.rows] == ["Background Style"]
    panel.status_dd.value = "all"
    panel.mod_dd.value = "bar"
    panel._on_filter()
    assert [r.en_us for r in panel.rows] == ["New Bar Text"]
    panel.mod_dd.value = ALL_MODS
    panel.search_field.value = "draw"
    panel._on_filter()
    assert [r.en_us for r in panel.rows] == ["Draw Layer"]
    panel.search_field.value = "沒有這個"
    panel._on_filter()
    assert panel.rows == [] and panel.selected_id is None
    assert panel.pager.total_items == 0


def test_detail_shows_full_main_and_other_text_side_by_side(db):
    panel = _panel(db)
    panel.activate()
    shown = _texts(panel.detail_host.content)
    assert "item.bar.b" in shown
    assert "Old Bar" in "".join(shown) and "New Bar Text" in "".join(shown)
    panel._select(
        next(r.variant_id for r in panel.rows if r.en_us == "Background Style")
    )
    shown = "".join(_texts(panel.detail_host.content))
    assert "Show Background" in shown and "顯示背景" in shown
    assert "背景樣式" in shown  # 這份原文的譯文


def test_adopt_button_only_for_untranslated_variants_with_a_main_translation(db):
    panel = _panel(db)
    panel.activate()
    by_en = {r.en_us: r for r in panel.rows}
    panel._select(by_en["Draw Layer"].variant_id)  # 沒有譯文、主條目有譯文
    assert len(_buttons(panel.detail_host.content, "沿用目前譯文")) == 1
    panel._select(by_en["Background Style"].variant_id)  # 已有譯文
    assert _buttons(panel.detail_host.content, "沿用目前譯文") == []
    panel._select(by_en["New Bar Text"].variant_id)  # 主條目也沒有譯文
    assert _buttons(panel.detail_host.content, "沿用目前譯文") == []


def test_adopting_writes_the_translation_and_notifies_the_view(db, monkeypatch):
    notified: list[int] = []
    panel = _panel(db, on_database_changed=lambda: notified.append(1))
    panel.activate()
    row = next(r for r in panel.rows if r.en_us == "Draw Layer")
    panel._select(row.variant_id)
    [button] = _buttons(panel.detail_host.content, "沿用目前譯文")
    button.on_click(None)
    assert db._q(
        "SELECT t.zh_tw, t.source FROM variant_translation t "
        "JOIN entry_variant v ON v.id = t.variant_id WHERE v.en_us='Draw Layer'"
    ) == [("顯示背景", SRC_MANUAL)]
    assert notified == [1] and panel._stale  # 通知上層，並標成需要重新載入
    panel.activate()
    assert next(r for r in panel.rows if r.en_us == "Draw Layer").zh_tw == "顯示背景"
    assert panel.rows[0].variant_id  # 清單仍在
    assert _buttons(panel.detail_host.content, "沿用目前譯文") == []


def test_adopt_refusal_is_reported_and_nothing_is_written(db, monkeypatch):
    snacks: list[str] = []
    monkeypatch.setattr(
        variants_panel.entry_variant_actions,
        "show_snack",
        lambda _p, text, *_a: snacks.append(text),
    )
    panel = _panel(db)
    panel.activate()
    row = next(r for r in panel.rows if r.en_us == "Draw Layer")
    # 畫面上的譯文已過期（別處改過條目譯文）
    stale = SimpleNamespace(**{**row.__dict__, "main_zh": "過期的譯文"})
    panel._adopt(stale)
    assert snacks and "目前譯文已變更" in snacks[-1]
    assert (
        db._one("SELECT COUNT(*) FROM variant_translation WHERE zh_tw='過期的譯文'")[0]
        == 0
    )


def test_empty_database_and_missing_database_show_explanations(tmp_path):
    empty = TranslationDB(tmp_path / "empty.db")
    try:
        panel = _panel(empty)
        panel.activate()
        assert panel.rows == []
        assert "目前沒有其他原文" in "".join(_texts(panel.detail_host.content))
    finally:
        empty.close()
    missing = VariantsPanel(SimpleNamespace(), lambda: None)
    missing.activate()
    assert "尚未建立 Mod 資料庫" in "".join(_texts(missing.detail_host.content))


def test_load_failure_is_shown_and_does_not_raise(db, monkeypatch):
    panel = _panel(db)

    def boom(*_a, **_k):
        raise RuntimeError("壞掉了")

    monkeypatch.setattr(db, "variant_versions", boom)
    panel.activate()
    assert "載入失敗" in "".join(_texts(panel.detail_host.content))
    assert panel._stale  # 下次顯示會再試


def test_older_load_results_are_dropped(db, monkeypatch):
    """兩次載入的結果以相反順序回來時，只套用最新的一次。"""
    pending: list = []
    monkeypatch.setattr(
        variants_panel,
        "run_read",
        lambda _page, work, apply, **_k: pending.append((work, apply)) or True,
    )
    panel = _panel(db)
    panel.reload()
    panel.status = STATUS_UNTRANSLATED
    panel.reload(page=1)
    (work_old, apply_old), (work_new, apply_new) = pending
    apply_new(work_new(), None)
    assert sorted(r.en_us for r in panel.rows) == ["Draw Layer", "New Bar Text"]
    apply_old(work_old(), None)  # 舊結果（沒有篩選）不能蓋掉新的
    assert sorted(r.en_us for r in panel.rows) == ["Draw Layer", "New Bar Text"]


def test_leaving_the_tab_while_loading_reloads_on_return(db, monkeypatch):
    pending: list = []
    monkeypatch.setattr(
        variants_panel,
        "run_read",
        lambda _page, work, apply, **_k: pending.append((work, apply)) or True,
    )
    panel = _panel(db)
    panel.activate()
    panel.will_unmount()
    work, apply = pending[0]
    apply(work(), None)  # 離開後才回來的結果被丟棄
    assert panel.rows == [] and panel._stale
    panel.activate()
    assert len(pending) == 2


def test_page_clamps_to_the_last_page_when_data_shrinks(db, monkeypatch):
    monkeypatch.setattr(variants_panel, "PAGE_SIZE", 2)
    panel = _panel(db)
    panel.pager.page_size = 2
    panel.activate()
    panel.reload(page=2)
    assert panel.pager.current_page == 2 and len(panel.rows) == 1
    panel.reload(page=9)  # 超出：回到最後一頁
    assert panel.pager.current_page == 2 and len(panel.rows) == 1


# ------------------------------------------------------------------ 頁籤接線
def _scheduled_page():
    page = mock_page()
    page.operation_registry = OperationRegistry()
    page.run_thread = lambda _target: None
    return page


def _settle(page) -> None:
    for _ in range(5):
        assert page.operation_registry.wait_for_idle(timeout=5)
        page._run_all_tasks()


@pytest.fixture
def view(db_path):
    page = _scheduled_page()
    view = moddb_view.ModDbView(page, mock_filepicker())
    yield view, page
    page.operation_registry.begin_shutdown()
    page.operation_registry.wait_for_idle(timeout=5)
    if view._db is not None:
        view._db.close()


def test_the_tab_sits_after_entries_and_loads_in_a_worker(view):
    mod_view, page = view
    keys = [key for key, _label in moddb_view.TABS]
    assert keys.index("variants") == keys.index("entries") + 1
    assert dict(moddb_view.TABS)["variants"] == "其他原文"
    mod_view.show_tab("variants")
    assert mod_view._shown_tab == "variants"  # 載入完成前就先切到這個頁籤
    _settle(page)
    assert mod_view.variants.pager.total_items == 3
    assert mod_view.tab == "variants" and not mod_view.variants._stale
    assert "variants" not in mod_view._panel_refresh_needed  # 不走共用快照流程


def test_database_changes_mark_the_tab_stale_and_reload_when_visible(view):
    mod_view, page = view
    mod_view.show_tab("variants")
    _settle(page)
    mod_view._on_database_changed()  # 目前就在這個頁籤：重新載入
    assert mod_view.variants._loading or mod_view.variants._stale
    _settle(page)
    assert not mod_view.variants._stale
    mod_view.show_tab("entries")
    mod_view._invalidate_database_snapshots()
    assert mod_view.variants._stale  # 在別的頁籤：只標記，回來時才載入


def test_unmounting_the_view_discards_in_flight_variant_loads(view):
    mod_view, page = view
    mod_view.show_tab("variants")
    generation = mod_view.variants._generation
    mod_view.will_unmount()
    assert mod_view.variants._generation > generation
    _settle(page)
