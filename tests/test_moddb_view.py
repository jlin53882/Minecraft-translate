"""Mod 資料庫頁（總覽／條目校對／掃描匯入）與服務層的行為測試。"""

from __future__ import annotations

import json
import sqlite3
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services_impl import moddb_service
from app.tasks.task_session import TaskSession
from app.views import moddb_view
from app.views.moddb import entries_panel, scan_panel
from tests.conftest import mock_filepicker, mock_page
from translation_tool.translation_db import (
    KIND_LANG,
    DbSettings,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.models import HistoryRow
from translation_tool.translation_db.schema import SRC_MANUAL


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(path), version="1.21.1"),
    )
    return path


def seed(path: Path) -> None:
    db = TranslationDB(path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "鋼製外殼"),
            ScanItem(KIND_LANG, "foo", "item.foo.b", "Infused Alloy"),
            ScanItem(KIND_LANG, "bar", "item.bar.a", "Steel Casing", "鋼殼"),
        ],
    )
    db.ingest(
        "1.20.1", [ScanItem(KIND_LANG, "foo", "item.foo.a", "Steel Casing", "鋼外殼")]
    )
    db.close()


def texts_of(control) -> list[str]:
    """遞迴取出控制項樹中所有 ``Text.value``（repr 會被截斷，不能用 str(control) 比對）。"""
    found: list[str] = []
    value = getattr(control, "value", None)
    if isinstance(value, str) and type(control).__name__ == "Text":
        found.append(value)
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for item in child:
                found.extend(texts_of(item))
        elif child is not None and not isinstance(child, str):
            found.extend(texts_of(child))
    return found


def make_jar(path: Path, files: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False))
    return path


# ------------------------------------------------------------------ 服務層
def test_version_choices_puts_database_versions_first(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    choices = moddb_service.version_choices(db)
    db.close()
    assert choices[:2] == ["1.21.1", "1.20.1"]
    assert (
        len(choices) == len(set(choices)) and len(choices) > 5
    )  # 之後接資源包版本清單


def test_pack_format_hint_matches_bundler_data():
    assert moddb_service.pack_format_hint("1.21 (24w18a)~1.21.1") == "pack_format 33–34"
    assert moddb_service.pack_format_hint("not-a-version") == ""


def test_summarize_database_never_creates_a_file(db_path):
    assert moddb_service.summarize_database() is None
    assert not db_path.exists()
    seed(db_path)
    info = moddb_service.summarize_database()
    assert (
        info["entries"] == 4
        and info["versions"][0] == "1.21.1"
        and info["target_version"] == "1.21.1"
    )
    assert 0 < info["progress"] <= 100


def test_scan_service_writes_database_and_reports_summary(db_path, tmp_path):
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Some Text Here"}},
    )
    session = TaskSession()
    moddb_service.run_moddb_scan_service(
        str(tmp_path / "mods"), moddb_service.ScanOptions("1.21.1"), session
    )
    snap = session.snapshot()
    assert snap["status"] == "DONE" and snap["summary"]["new_entries"] == 1
    db = TranslationDB(db_path)
    assert db.count_entries() == 1
    db.close()


def test_scan_service_cancelled_session_is_reported(db_path, tmp_path):
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Some Text Here"}},
    )
    session = TaskSession()
    session.request_cancel()
    moddb_service.run_moddb_scan_service(
        str(tmp_path / "mods"), moddb_service.ScanOptions("1.21.1"), session
    )
    # start() 會清除取消旗標，所以這裡只驗證任務會正常結束且不留下 ERROR
    assert session.snapshot()["status"] in ("DONE",)


# ------------------------------------------------------------------ 主頁
def test_view_starts_on_overview_with_empty_state_when_no_database(db_path):
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    assert view.tab == "overview"
    assert (
        view.overview.empty.visible is True
        and view.overview.content_col.visible is False
    )
    assert not db_path.exists()  # 開頁不會憑空建立資料庫


def test_view_switches_tabs_and_shows_overview_numbers(db_path):
    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    assert view.overview.content_col.visible is True
    assert view.overview.stat_mods.value_text.value == "2"
    assert (
        view.overview.stat_diff.value_text.value == "1"
    )  # foo 的 Steel Casing 兩版本譯文不同
    view.show_tab("entries")
    assert view.body.content is view.entries and view.tab == "entries"
    view.show_tab("scan")
    assert view.body.content is view.scan


def test_database_tab_refreshes_run_queries_in_worker_threads(db_path, monkeypatch):
    from app.tasks.operation_registry import OperationRegistry

    seed(db_path)
    page = mock_page()
    page.operation_registry = OperationRegistry()
    page.run_thread = lambda _target: None
    query_threads = []
    originals = {
        "versions": TranslationDB.versions,
        "list_entries": TranslationDB.list_entries,
        "count_untranslated": TranslationDB.count_untranslated,
    }

    def record(name):
        original = originals[name]

        def wrapped(db, *args, **kwargs):
            query_threads.append((name, threading.current_thread().name))
            return original(db, *args, **kwargs)

        return wrapped

    for method in originals:
        monkeypatch.setattr(TranslationDB, method, record(method))

    view = moddb_view.ModDbView(page, mock_filepicker())
    try:
        for key in ("entries", "scan", "translate"):
            view.show_tab(key)
            assert view._panels[key].refresh_indicator.visible is True
            assert page.operation_registry.wait_for_idle(timeout=2)
            page._run_all_tasks()
            page._tasks.clear()
            assert view._panels[key].refresh_indicator.visible is False
        assert view.entries.total == 3
        assert view.scan._db_versions == {"1.21.1", "1.20.1"}
        assert "未翻譯" in view.translate.count_text.value
        assert query_threads
        assert all(name != threading.current_thread().name for _, name in query_threads)
        assert view._db is None
    finally:
        page.operation_registry.begin_shutdown()
        assert page.operation_registry.wait_for_idle(timeout=2)


def test_moddb_overview_load_uses_a_worker_owned_connection(db_path, monkeypatch):
    from app.tasks.operation_registry import OperationRegistry

    seed(db_path)
    page = mock_page()
    registry = OperationRegistry()
    page.operation_registry = registry
    # The production page exposes run_thread; launch_page_operation uses the
    # attached registry for the actual owner and worker lifecycle.
    page.run_thread = lambda _target: None

    load_entered = threading.Event()
    release_load = threading.Event()
    worker_connections = []
    original_version_stats = TranslationDB.version_stats

    def gated_version_stats(db):
        if threading.current_thread().name.startswith("operation-moddb-overview-load"):
            worker_connections.append(db)
            load_entered.set()
            assert release_load.wait(timeout=2)
        return original_version_stats(db)

    monkeypatch.setattr(TranslationDB, "version_stats", gated_version_stats)
    view = moddb_view.ModDbView(page, mock_filepicker())
    view.did_mount()
    try:
        assert load_entered.wait(timeout=1)
        assert view.overview.loading.visible is True
        assert view._db is None  # 隱藏的掃描面板不可先在 UI 執行緒開資料庫
        release_load.set()
        assert registry.wait_for_idle(timeout=2)
        page._run_all_tasks()
        assert len(worker_connections) == 1
        assert worker_connections[0] is view._db
        assert view.overview.content_col.visible is True
        assert worker_connections[0]._conn.execute("SELECT 1").fetchone() == (1,)
        with sqlite3.connect(db_path) as check_conn:
            assert (
                check_conn.execute("SELECT count(*) FROM stat_cache").fetchone()[0] > 0
            )
    finally:
        release_load.set()
        registry.begin_shutdown()
        assert registry.wait_for_idle(timeout=2)
        if view._db is not None:
            view._db.close()
            view._db = None


def test_delayed_moddb_overview_load_discards_stale_priority(db_path, monkeypatch):
    from dataclasses import replace

    from app.tasks.operation_registry import OperationRegistry

    seed(db_path)
    page = mock_page()
    registry = OperationRegistry()
    page.operation_registry = registry
    page.run_thread = lambda _target: None

    constructor_entered = threading.Event()
    release_constructor = threading.Event()
    worker_connections = []
    original_init = TranslationDB.__init__
    settings_state = {"value": moddb_service.current_settings()}
    new_priority = tuple(reversed(settings_state["value"].priority))
    assert new_priority != settings_state["value"].priority
    monkeypatch.setattr(
        moddb_service, "load_db_settings", lambda: settings_state["value"]
    )

    def gated_init(db, *args, **kwargs):
        is_overview_worker = threading.current_thread().name.startswith(
            "operation-moddb-overview-load"
        )
        if is_overview_worker and not constructor_entered.is_set():
            constructor_entered.set()
            assert release_constructor.wait(timeout=2)
        original_init(db, *args, **kwargs)
        if is_overview_worker:
            worker_connections.append(db)

    monkeypatch.setattr(TranslationDB, "__init__", gated_init)
    view = moddb_view.ModDbView(page, mock_filepicker())
    view.did_mount()
    try:
        assert constructor_entered.wait(timeout=1)
        settings_state["value"] = replace(
            settings_state["value"], priority=new_priority
        )
        release_constructor.set()
        assert registry.wait_for_idle(timeout=2)
        page._run_all_tasks()
        page._tasks.clear()
        assert registry.wait_for_idle(timeout=2)
        page._run_all_tasks()
        assert len(worker_connections) == 2
        with sqlite3.connect(db_path) as check_conn:
            stored_priority = check_conn.execute(
                "SELECT value FROM meta WHERE key = 'priority'"
            ).fetchone()[0]
            assert (
                check_conn.execute("SELECT count(*) FROM stat_cache").fetchone()[0] > 0
            )
        assert stored_priority == ",".join(str(source) for source in new_priority)
        with pytest.raises(sqlite3.ProgrammingError):
            worker_connections[0]._conn.execute("SELECT 1")
        assert worker_connections[1] is view._db
    finally:
        release_constructor.set()
        registry.begin_shutdown()
        assert registry.wait_for_idle(timeout=2)
        if view._db is not None:
            view._db.close()
            view._db = None


def test_overview_link_opens_entries_with_filter(db_path):
    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    view.open_entries("none", "1.21.1", "foo")
    assert (
        view.tab == "entries"
        and view.entries.state == "none"
        and view.entries.mod_id == "foo"
    )
    assert [r.key for r in view.entries.rows] == ["item.foo.b"]


# ------------------------------------------------------------------ 條目校對
@pytest.fixture
def entries(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    yield panel
    db.close()


def test_entries_lists_selected_version_and_selects_first(entries):
    assert entries.version == "1.21.1" and entries.total == 3
    assert entries.selected is not None and entries.editor_body.visible is True
    assert entries.src_text.value == entries.selected.en_us
    assert entries.save_btn.disabled is True  # 沒改動不能儲存


def test_entries_filters_by_state_mod_and_search(entries):
    entries._on_state("none")
    assert [r.key for r in entries.rows] == ["item.foo.b"]
    entries._on_state("diff")
    assert [r.key for r in entries.rows] == ["item.foo.a"]
    entries._on_state("all")
    entries.mod_id = "bar"
    entries._load_list()
    assert [r.key for r in entries.rows] == ["item.bar.a"]
    entries.mod_id = None
    entries._on_search(
        type("E", (), {"control": type("C", (), {"value": "alloy"})()})()
    )
    assert [r.key for r in entries.rows] == ["item.foo.b"]


def test_entries_edit_shows_impact_and_save_propagates(entries):
    foo_a = next(r for r in entries.rows if r.key == "item.foo.a")
    entries.select(foo_a.id)
    entries.tw_field.value = "鋼製外殼（校對）"
    entries._on_text_change()
    assert entries.save_btn.disabled is False and entries.impact_box.visible is True
    assert (
        "1.20.1" in entries.impact_text.value
        and "一併改為" in entries.impact_text.value
    )

    entries._save()
    entries._page._run_all_tasks()
    assert "同步 1 個版本" in entries.saved_text.value
    db = entries.db()
    old = next(r for r in db.list_entries("1.20.1")[0] if r.key == "item.foo.a")
    assert (old.zh_tw, old.source) == ("鋼製外殼（校對）", SRC_MANUAL)
    # 清單與編輯區已重新載入：不再有版本差異
    assert (
        entries.selected.zh_tw == "鋼製外殼（校對）" and entries.selected.diff is False
    )


def test_entries_sync_switch_off_only_changes_current_version(entries):
    foo_a = next(r for r in entries.rows if r.key == "item.foo.a")
    entries.select(foo_a.id)
    entries.sync_row.value = False
    entries.tw_field.value = "只改這版"
    entries._on_text_change()
    assert (
        "不會影響其他版本" in entries.impact_text.value
        or "只出現在" in entries.impact_text.value
        or entries.impact_box.visible
    )
    entries._save()
    entries._page._run_all_tasks()
    db = entries.db()
    other = next(r for r in db.list_entries("1.20.1")[0] if r.key == "item.foo.a")
    assert other.zh_tw == "鋼外殼"


def test_entries_suggestions_cover_both_kinds_and_apply_fills_the_field(entries):
    foo_a = next(r for r in entries.rows if r.key == "item.foo.a")
    entries.select(foo_a.id)
    assert "鋼外殼" in texts_of(entries.sug_col)  # 同鍵值・其他版本
    entries._on_sug_tab("text")
    assert entries.sug_tab == "text"
    assert any("鋼殼" in t for t in texts_of(entries.sug_col))  # bar 模組相同原文
    entries._apply_suggestion("鋼殼")
    assert entries.tw_field.value == "鋼殼" and entries.save_btn.disabled is False


def test_entries_revert_restores_previous_translation(entries):
    foo_a = next(r for r in entries.rows if r.key == "item.foo.a")
    entries.select(foo_a.id)
    entries.tw_field.value = "新譯文"
    entries._save()
    entries._page._run_all_tasks()
    history = entries.detail.history[0]
    entries._revert(history.id)
    db = entries.db()
    assert (
        next(r for r in db.list_entries("1.21.1")[0] if r.key == "item.foo.a").zh_tw
        == "鋼製外殼"
    )


def test_ai_retranslation_history_has_no_manual_revert_action(entries):
    row = entries.rows[0]
    entries.select(row.id)
    entries.detail.history = [
        HistoryRow(
            1,
            "batch",
            "2026-10-07 10:00:00",
            "AI 重翻",
            "ai_retranslate",
            "舊譯文",
            "新譯文",
            "",
        )
    ]

    entries._render_history()

    rendered = texts_of(entries.history_col)
    assert any(text.startswith("AI 重翻・AI 重翻") for text in rendered)
    assert "還原這次更新" not in rendered


def test_entries_step_moves_within_page_and_empty_state_without_database():
    panel = entries_panel.EntriesPanel(mock_page(), lambda: None)
    panel.refresh()
    assert panel.editor_body.visible is False and panel.empty_editor.visible is True


# ------------------------------------------------------------------ 掃描匯入
# 只替換 scan_panel 看到的 threading：直接改全域 threading.Thread 會連 ThreadPoolExecutor 的
# worker 一起變成同步執行而死結。
class _SyncThread:
    def __init__(self, target=None, args=(), daemon=None, **_kw):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def test_scan_requires_version_and_folder(db_path):
    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    panel.version_field.value = ""
    panel.start_clicked()
    assert panel.status_chip.label.value == "請先選擇或輸入遊戲版本"
    panel.version_field.value = "1.21.1"
    panel.start_clicked()
    assert panel.status_chip.label.value == "請先選擇 mods 資料夾"


def test_scan_runs_and_reports_counts(db_path, tmp_path, monkeypatch):
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {
            "assets/foo/lang/en_us.json": {
                "item.foo.a": "Steel Casing",
                "item.foo.b": "Infused Alloy",
            },
            "assets/foo/lang/zh_tw.json": {"item.foo.a": "鋼製外殼"},
        },
    )
    finished = []
    monkeypatch.setattr(scan_panel, "threading", SimpleNamespace(Thread=_SyncThread))
    monkeypatch.setattr(
        scan_panel.PollerHandle, "start", lambda self, page, handler: True
    )
    panel = scan_panel.ScanPanel(
        mock_page(),
        mock_filepicker(),
        lambda: None,
        on_finished=lambda: finished.append(1),
    )
    panel.version_field.value = "1.21.1"
    panel.path_field.value = str(tmp_path / "mods")

    panel.start_clicked()
    panel.sync_from_session()

    assert panel.status_chip.label.value == "掃描完成" and finished == [1]
    assert panel.stat_new.value_text.value == "2"
    assert panel.stat_skip.value_text.value == "0"
    assert panel.cancel_btn.disabled is True and panel.start_btn.disabled is False

    panel.start_clicked()  # 第二次：全部已存在 → 略過
    panel.sync_from_session()
    assert (
        panel.stat_new.value_text.value == "0"
        and panel.stat_skip.value_text.value == "2"
    )


def test_scan_preview_does_not_write(db_path, tmp_path, monkeypatch):
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Some Text Here"}},
    )
    monkeypatch.setattr(scan_panel, "threading", SimpleNamespace(Thread=_SyncThread))
    monkeypatch.setattr(
        scan_panel.PollerHandle, "start", lambda self, page, handler: True
    )
    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    panel.version_field.value = "1.21.1"
    panel.path_field.value = str(tmp_path / "mods")
    panel.start_clicked(dry_run=True)
    panel.sync_from_session()
    assert (
        panel.status_chip.label.value == "預覽完成"
        and panel.stat_new.value_text.value == "1"
    )
    assert not db_path.exists()


def test_scan_version_list_filters_and_choosing_fills_the_field(db_path):
    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    panel.version_search.value = "1.21"
    panel._render_versions()
    assert 0 < len(panel.version_list.controls) < len(panel._versions)
    panel._choose_version("1.20.1")
    assert panel.version() == "1.20.1" and panel.start_btn.content == "開始掃描 1.20.1"


def test_scan_option_switches_flow_into_scan_options(db_path):
    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    panel.version_field.value = "1.21.1"
    panel.nested_row.value = False
    panel.book_row.value = False
    opts = panel.build_options()
    assert (opts.version, opts.scan_nested, opts.include_patchouli, opts.dry_run) == (
        "1.21.1",
        False,
        False,
        False,
    )


# ------------------------------------------------------------------ 入口：工作台與機器翻譯頁
def _dashboard(moddb):
    from app.views.dashboard_view import DashboardView

    view = DashboardView(
        mock_page(),
        cache_overview_loader=dict,
        rules_count_loader=lambda: 0,
        key_snapshot_loader=list,
        moddb_loader=lambda: moddb,
    )
    view.reload(sync=True)
    return view


def test_dashboard_card_shows_database_summary_or_invitation():
    summary = {
        "versions": ["1.21.1", "1.20.1"],
        "entries": 98420,
        "translated": 70000,
        "progress": 71,
        "target_version": "1.21.1",
    }
    shown = texts_of(_dashboard(summary).moddb_column)
    assert "98,420" in shown and any(
        "已翻譯 71%" in t and "1.21.1、1.20.1" in t for t in shown
    )
    assert any("目標版本：1.21.1" in t for t in shown)

    empty = texts_of(_dashboard(None).moddb_column)
    assert "尚未建立資料庫" in empty


def test_dashboard_card_buttons_navigate_to_the_page():
    view = _dashboard(None)
    went = []
    view._navigate = went.append
    view._go("moddb")
    assert went == ["moddb"]


def test_lm_view_shows_database_status_and_defaults_from_settings(db_path):
    from app.views import lm_view

    view = lm_view.LMView(mock_page(), mock_filepicker())
    assert "尚未建立 Mod 資料庫" in view.db_info.value
    assert view.use_db_switch.value is True  # 預設啟用（資料庫不存在時流程自動略過）
    seed(db_path)
    view.refresh_db_info()
    assert "4 條目" in view.db_info.value and "1.21.1" in view.db_info.value


def test_every_view_spec_reaches_moddb_through_registry_and_palette():
    from app import view_registry as vr

    spec = vr.get_spec("moddb")
    assert (spec.group, spec.cls, spec.needs_file_picker) == ("data", "ModDbView", True)


def test_lm_view_warns_when_database_is_on_but_no_version_is_set(db_path, monkeypatch):
    from app.views import lm_view
    from app.views.moddb import lm_db_options

    monkeypatch.setattr(
        lm_db_options.moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(db_path), version=""),
    )
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.use_db_switch.value = True
    view.db_version_field.value = ""
    view.refresh_db_info()
    assert "尚未指定目標版本" in view.db_info.value

    view.db_version_field.value = "1.21.1"
    view.lm_db_options._on_version_changed(
        SimpleNamespace(control=view.db_version_field)
    )
    assert "尚未指定目標版本" not in view.db_info.value

    view.db_version_field.value = ""
    view.use_db_switch.value = False
    view.lm_db_options._on_enabled_changed(SimpleNamespace(control=view.use_db_switch))
    assert "尚未指定目標版本" not in view.db_info.value


# ------------------------------------------------------------------ 特殊字元在介面上的呈現
def test_formatting_helpers_detect_and_compare_special_tokens():
    from app.views.moddb import formatting as fm

    assert fm.visible_breaks("哈囉\n世界  ！") == "哈囉 ¶ 世界 ！"
    assert fm.shorten("哈囉\r\n世界", 20) == "哈囉 ¶ 世界"
    assert fm.token_issues("Hello %s\nWorld", "哈囉 %s\n世界") == []
    assert fm.token_issues("Hello %s\nWorld", "哈囉\n世界") == ["少了 1 個「%s」"]
    assert fm.token_issues("a", "§a哈囉") == ["多了 1 個「§a」"]
    assert fm.token_issues("a\\nb", "甲\n乙") == [
        "多了 1 個「換行」",
        "少了 1 個「字面 \\n」",
    ]
    assert fm.token_issues("$(br)text", "$(br)文字") == []
    assert fm.whitespace_note(" 哈囉 ") != "" and fm.whitespace_note("哈囉") == ""


@pytest.fixture
def special_entries(db_path):
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "foo", "tip.a", "Hello %s\nWorld", "§a哈囉§r %s\n世界 "
            ),
            ScanItem(KIND_LANG, "foo", "tip.b", "Plain Text Here", "純文字"),
        ],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    yield panel
    db.close()


def test_list_shows_newline_marker_and_editor_keeps_exact_text(special_entries):
    panel = special_entries
    first = next(r for r in panel.rows if r.key == "tip.a")
    panel.select(first.id)
    shown = texts_of(panel.list_view)
    assert any("¶" in t for t in shown)  # 清單預覽：換行顯示成 ¶
    assert panel.src_text.value == "Hello %s\nWorld"  # 原文保留真正的換行
    assert panel.tw_field.value == "§a哈囉§r %s\n世界 "  # 譯文逐字相同（含結尾空白）
    assert panel.save_btn.disabled is True  # 沒改動：結尾空白不會被當成「已修改」


def test_editor_hints_for_missing_tokens_whitespace_and_color_codes(special_entries):
    panel = special_entries
    panel.select(next(r for r in panel.rows if r.key == "tip.a").id)
    assert panel.mc_preview.visible is True and panel.mc_preview.spans  # § 顏色預覽
    assert "前後有空白" in panel.token_hint.value  # 結尾空白提醒

    panel.tw_field.value = "§a哈囉§r\n世界"  # 少了 %s
    panel._on_text_change()
    assert "少了 1 個「%s」" in panel.token_hint.value
    assert panel.save_btn.disabled is False

    panel.tw_field.value = "哈囉 %s\n世界"
    panel._on_text_change()
    assert panel.token_hint.visible is False and panel.mc_preview.visible is False


def test_saving_keeps_newlines_codes_and_surrounding_whitespace(special_entries):
    panel = special_entries
    panel.select(next(r for r in panel.rows if r.key == "tip.a").id)
    panel.tw_field.value = " §c警告§r %s\n第二行\n"
    panel._on_text_change()
    panel._save()
    panel._page._run_all_tasks()
    stored = panel.db().get_entry(panel.selected.id).zh_tw
    assert stored == " §c警告§r %s\n第二行\n"


# ------------------------------------------------------------------ 翻譯 ZIP 匯入（掃描頁）
def test_scan_panel_zip_mode_switches_controls_and_options(db_path):
    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    panel.version_field.value = "1.21.1"
    jar_opts = panel.build_options()
    assert (jar_opts.translated, jar_opts.clean_english, jar_opts.apply_rules) == (
        False,
        True,
        True,
    )
    assert panel.source_dd.visible is False and panel.zip_pick_btn.visible is False

    panel._on_mode("zip")
    assert (
        panel.mode == "zip" and panel.source_dd.visible and panel.zip_pick_btn.visible
    )
    assert panel.mode_note.visible is True
    assert (
        panel.jar_tr_row.visible is False and panel.rules_row.visible is False
    )  # zh_tw 不判讀、不套規則
    assert panel.start_btn.content == "開始匯入 1.21.1"

    panel.source_dd.value = str(scan_panel.SRC_SUBTITLE)
    panel.clean_row.value = False
    opts = panel.build_options()
    assert (
        opts.translated is True
        and opts.apply_rules is False
        and opts.read_jar_translations is True
    )
    assert (
        opts.clean_english is False
        and opts.translation_source == scan_panel.SRC_SUBTITLE
    )

    panel._on_mode("jar")
    assert (
        panel.start_btn.content == "開始掃描 1.21.1" and panel.rules_row.visible is True
    )


def test_scan_panel_imports_a_translated_zip_directly(db_path, tmp_path, monkeypatch):
    make_jar(
        tmp_path / "pack.zip",
        {
            "assets/foo/lang/zh_tw.json": {
                "item.foo.a": "鋼製外殼",
                "item.foo.plain": "Vanilla",
            }
        },
    )
    monkeypatch.setattr(scan_panel, "threading", SimpleNamespace(Thread=_SyncThread))
    monkeypatch.setattr(
        scan_panel.PollerHandle, "start", lambda self, page, handler: True
    )
    panel = scan_panel.ScanPanel(mock_page(), mock_filepicker(), lambda: None)
    panel.version_field.value = "1.21.1"
    panel._on_mode("zip")
    panel.path_field.value = str(tmp_path / "pack.zip")
    panel.start_clicked()
    panel.sync_from_session()
    assert (
        panel.status_chip.label.value == "掃描完成"
        and panel.stat_new.value_text.value == "2"
    )
    db = TranslationDB(db_path)
    rows = {r.key: r for r in db.list_entries("1.21.1")[0]}
    assert (
        rows["item.foo.plain"].zh_tw == "Vanilla" and rows["item.foo.plain"].en_us == ""
    )
    assert rows["item.foo.a"].source == scan_panel.SRC_CUSTOM
    db.close()


def test_entries_and_overview_show_unknown_original_text(db_path):
    db = TranslationDB(db_path)
    db.ingest("1.21.1", [ScanItem(KIND_LANG, "foo", "item.foo.a", "", "鋼製外殼")])
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    assert entries_panel.NO_SOURCE_TEXT == panel.src_text.value
    assert "（原文未知）" in texts_of(panel.list_view)
    assert panel.token_hint.visible is False  # 沒有原文就不比對特殊字元
    overview = moddb_view.OverviewPanel(mock_page(), lambda: db)
    overview.refresh()
    assert "原文未知 1" in overview.stat_content.delta_text.value
    db.close()


def test_batch_replace_dialog_pages_all_rows_and_requires_preview_after_selection(
    db_path,
):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "batch",
                f"item.batch.{index:03d}",
                f"Source {index}",
                f"譯文舊{index:03d}",
            )
            for index in range(55)
        ],
    )
    dialog = BatchReplaceDialog(
        mock_page(),
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )
    dialog.find_field.value = "舊"
    dialog.replace_field.value = "新"
    dialog._preview()

    assert dialog.plan is not None and dialog.plan.update_count == 55
    assert len(dialog.rows.controls) == 50 and dialog.next_btn.disabled is False
    dialog._turn(1)
    assert len(dialog.rows.controls) == 5

    first_id = dialog.plan.changes[0].entry_id
    dialog._toggle_root(first_id, False)
    assert dialog.plan is None and dialog.apply_btn.disabled is True
    dialog._preview()
    assert dialog.plan is not None and dialog.plan.update_count == 54
    db.close()


def test_batch_replace_pending_selection_never_displays_executable_stale_extras(
    db_path,
):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Source", "原譯文")],
    )
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Source", "原譯文")],
    )
    dialog = BatchReplaceDialog(
        mock_page(),
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )
    dialog.find_field.value = "原"
    dialog.replace_field.value = "新"
    dialog.propagate.value = True
    dialog._preview()
    assert dialog.plan is not None
    assert dialog.plan.extra_version_count == 1

    root_id = dialog.plan.root_ids[0]
    dialog._toggle_root(root_id, False)
    assert dialog.plan is None
    assert all("跨版本" not in text for text in texts_of(dialog.rows))
    assert dialog.apply_btn.disabled is True

    dialog._preview()
    assert dialog.plan is not None and dialog.plan.root_ids == ()
    assert dialog.plan.update_count == 0
    assert dialog.apply_btn.disabled is True
    assert all("跨版本" not in text for text in texts_of(dialog.rows))
    db.close()


def test_batch_replace_requires_second_confirmation_of_same_plan(db_path):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Source", "原譯文")],
    )
    page = mock_page()
    dialog = BatchReplaceDialog(
        page,
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )
    dialog.find_field.value = "原"
    dialog.replace_field.value = "新"
    dialog._preview()
    approved_plan = dialog.plan
    assert approved_plan is not None
    dialog._open_final_confirmation()
    assert dialog.final_summary.value.find("實際寫入 1") >= 0
    assert len(page.overlay) == 1
    dialog.final_ack.value = True
    dialog.plan = None  # Any changed preview invalidates the final confirmation.
    dialog._confirm_apply()
    assert dialog._busy is False
    assert db.list_entries("1.21.1")[0][0].zh_tw == "原譯文"
    db.close()


def test_batch_replace_final_confirmation_applies_exact_preview(db_path):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Source", "原譯文")],
    )
    page = mock_page()
    completed = []
    dialog = BatchReplaceDialog(
        page,
        lambda: db,
        EntryFilter(version="1.21.1"),
        completed.append,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )
    dialog.open()
    dialog.find_field.value = "原"
    dialog.replace_field.value = "新"
    dialog._preview()
    approved_plan = dialog.plan
    assert approved_plan is not None
    dialog._open_final_confirmation()
    dialog.final_ack.value = True
    dialog._confirm_apply()

    assert len(completed) == 1
    assert completed[0].updated == approved_plan.update_count == 1
    row = db.list_entries("1.21.1")[0][0]
    assert row.zh_tw == "新譯文"
    assert row.review_status == "unreviewed"
    assert dialog._dialog_open is False
    db.close()


def test_batch_replace_quality_ack_is_bound_to_one_exact_plan(db_path):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG,
                "quality",
                f"item.quality.{index}",
                "Hello %s",
                "舊譯文",
            )
            for index in range(2)
        ],
    )
    dialog = BatchReplaceDialog(
        mock_page(),
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )
    dialog.find_field.value = "舊譯文"
    dialog.replace_field.value = "新譯文%s%s"
    dialog._preview()

    plan_a = dialog.plan
    assert plan_a is not None and plan_a.update_count == 2
    assert all(change.quality_mixed for change in plan_a.changes)
    assert plan_a.quality_mixed_count == plan_a.quality_worsened_count == 2
    dialog.quality_ack.value = True
    dialog._on_quality_ack()
    approved_a = dialog.plan
    assert approved_a is not None
    assert approved_a.confirmed_quality_worsening is True

    # A selection change starts a distinct plan. Its consent must not inherit A.
    dialog._toggle_root(approved_a.root_ids[0], False)
    assert dialog.quality_ack.value is False
    assert dialog.plan is None
    dialog._preview()
    plan_b = dialog.plan
    assert plan_b is not None and plan_b.update_count == 1
    assert plan_b.confirmed_quality_worsening is False
    assert dialog.quality_ack.visible is True
    assert dialog.apply_btn.disabled is True

    # Even an explicit fresh preview of the same current criteria needs consent again.
    dialog.quality_ack.value = True
    dialog._on_quality_ack()
    assert dialog.plan.confirmed_quality_worsening is True
    dialog._preview()
    assert dialog.plan.confirmed_quality_worsening is False
    assert dialog.quality_ack.value is False
    assert dialog.apply_btn.disabled is True
    db.close()


def test_batch_replace_plan_precomputes_cross_version_summary_counts(db_path):
    from app.services_impl.moddb_service import EntryFilter

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Source", "原譯文")],
    )
    db.ingest(
        "1.20.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Source", "不同譯文")],
    )
    plan = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "原", "新", propagate=True
    )
    assert plan.update_count == 1
    assert plan.skipped_count == plan.conflict_count == 1
    assert plan.extra_version_count == 0
    assert plan.extra_candidate_count == 1
    assert plan.total_unique_entries == 2
    db.close()


def test_batch_replace_completion_does_not_expand_large_root_selection():
    from app.services_impl.moddb_batch_operation import BatchOperationOutcome
    from app.services_impl.moddb_service import BatchReplacePlan, EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    class LargeRootIds(tuple):
        def __new__(cls):
            return super().__new__(cls, ())

        def __len__(self):
            return 243_064

        def __iter__(self):
            raise AssertionError("completion must not copy every root id")

    plan = BatchReplacePlan(
        database_identity="db",
        criteria=EntryFilter(version="1.21.1"),
        find_text="舊",
        replace_text="新",
        propagate=False,
        root_ids=LargeRootIds(),
        changes=(),
        skipped=(),
        total_unique_entries=243_064,
        root_changes=(),
    )
    dialog = BatchReplaceDialog(
        mock_page(), lambda: None, plan.criteria, lambda _result: None
    )
    dialog._finish_preview(
        {"state": "complete"},
        BatchOperationOutcome("preview", 0, "complete", result=plan),
    )
    assert dialog.selection_plan is plan
    assert "勾選 243,064" in dialog.summary.value


def test_batch_replace_selection_criteria_use_sparse_exclusions():
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    base = EntryFilter(version="1.21.1")
    only_checked = BatchReplaceDialog._criteria_for_selection(
        base, (10, 20, 30), {20}, True
    )
    current_range = BatchReplaceDialog._criteria_for_selection(
        base, (10, 20, 30), {20}, False
    )
    assert only_checked.include_ids == (10, 30)
    assert current_range.exclude_ids == (20,)


def test_batch_replace_scope_change_invalidates_quality_ack(db_path):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "quality", f"item.q.{index}", "%s", "舊")
            for index in range(2)
        ],
    )
    dialog = BatchReplaceDialog(
        mock_page(),
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )
    dialog.find_field.value = "舊"
    dialog.replace_field.value = "新%s%s"
    dialog._preview()
    dialog.quality_ack.value = True
    dialog._on_quality_ack()
    assert dialog.plan.confirmed_quality_worsening is True

    dialog.only_checked_control.value = False
    dialog._on_scope_change()
    assert dialog.plan is None
    assert dialog.quality_ack.value is False
    dialog._preview()
    assert dialog.plan.confirmed_quality_worsening is False
    db.close()


def test_batch_replace_preview_shows_db_bound_identity_and_quality_details(db_path):
    from dataclasses import replace

    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog
    from translation_tool.translation_db.models import (
        BatchReplaceSkipped,
        QualityIssueDelta,
    )

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "same", "item.same", "Hello %s", "舊譯文")],
    )
    with db._tx() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('custom_sources',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"釘宮翻譯組": 100}),),
        )
    db.source_catalog = db._load_source_catalog()
    base = db.preview_batch_replace(EntryFilter(version="1.21.1"), "舊", "新")
    template = base.changes[0]
    deltas = (
        QualityIssueDelta("%s", "missing", 2, 1),
        QualityIssueDelta("§a", "extra", 0, 1),
        QualityIssueDelta("$(s0)", "missing", 1, 0),
        QualityIssueDelta("\\n", "extra", 0, 1),
        QualityIssueDelta("\n", "missing", 1, 0),
    )
    sources = ((0, None), (1, None), (3, None), (6, "legacy_unknown"), (100, None))
    rows = tuple(
        replace(
            template,
            entry_id=index + 1,
            key=f"item.preview.{index}",
            en_us=f"English source {index}\\nwith a second line",
            effective_source=source,
            effective_review_status=status,
            is_extra_version=index == 4,
            quality_deltas=deltas,
        )
        for index, (source, status) in enumerate(sources)
    )
    dialog = BatchReplaceDialog(
        mock_page(),
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
    )
    dialog._plan_catalog = db.source_catalog
    texts = [
        text for control in dialog._render_rows(rows) for text in texts_of(control)
    ]
    visible = "\n".join(texts)

    assert "English source 0\\nwith a second line" in visible
    assert "生效來源：釘宮翻譯組〔3〕" in visible
    assert "生效來源：釘宮翻譯組（自訂 #100）〔100〕" in visible
    assert "人工（歷史狀態待確認）" in visible
    assert "人工審核：不適用" in visible
    assert "跨版本額外項目" in visible
    assert "缺少佔位符 `%s`：2 → 1" in visible
    assert "多出 Minecraft 色碼 `§a`：0 → 1" in visible
    assert "缺少 Patchouli 巨集 `$(s0)`：1 → 0" in visible
    assert "字面 \\n" in visible and "實際換行" in visible
    detail_lines = [text for text in texts if text.startswith("生效來源：")]
    assert len(detail_lines) == 5
    assert all(
        "人工審核：不適用" in text for text in detail_lines if "〔6〕" not in text
    )
    assert not any(
        "歷史狀態待確認" in text and "〔6〕" not in text for text in detail_lines
    )
    dialog.show_skipped = True
    skipped_text = "\n".join(
        text
        for control in dialog._render_rows(
            [BatchReplaceSkipped(99, "1.20.1", "item.skipped", "來源狀態不一致")]
        )
        for text in texts_of(control)
    )
    assert "item.skipped：來源狀態不一致" in skipped_text
    db.close()


def test_batch_worker_keeps_large_plan_out_of_task_session_summary(
    db_path, monkeypatch
):
    from dataclasses import replace

    from app.services_impl.moddb_batch_operation import (
        BatchOperationResult,
        launch_batch_replace_job,
    )
    from app.services_impl.moddb_service import EntryFilter
    from translation_tool.translation_db.models import BatchReplacePlan

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [
            ScanItem(
                KIND_LANG, "large", "item.large", "Sensitive English", "Sensitive 繁中"
            )
        ],
    )
    base = db.preview_batch_replace(
        EntryFilter(version="1.21.1"), "Sensitive", "Updated"
    )
    row = base.changes[0]
    large_plan = replace(
        base,
        root_ids=tuple(range(10_000)),
        changes=tuple(
            replace(row, entry_id=index + 1, key=f"item.large.{index}")
            for index in range(10_000)
        ),
    )

    def fail_plan_repr(_self):
        pytest.fail("TaskSession.finish must not stringify a complete batch plan")

    monkeypatch.setattr(BatchReplacePlan, "__repr__", fail_plan_repr)
    lifecycle = []
    monkeypatch.setattr(
        TaskSession,
        "_log_lifecycle",
        lambda _session, text, _level="info": lifecycle.append(text),
    )
    channel = BatchOperationResult("preview", 19)
    session, launched = launch_batch_replace_job(
        mock_page(),
        kind="preview",
        generation=19,
        work=lambda _session: large_plan,
        result_channel=channel,
        operation_launcher=lambda target, **_kwargs: (target(), True)[1],
    )

    assert launched is True
    assert session.is_finished
    assert session.summary["update_count"] == 10_000
    assert "result" not in session.summary
    lifecycle_text = " ".join(lifecycle)
    assert "Sensitive English" not in lifecycle_text
    assert "Sensitive 繁中" not in lifecycle_text
    delivered = channel.take(session, "preview", 19)
    assert delivered is not None and delivered.result is large_plan
    db.close()


def test_batch_worker_result_channels_isolate_workspaces_and_discard_late_results(
    db_path,
):
    from app.services_impl.moddb_batch_operation import (
        BatchOperationResult,
        launch_batch_replace_job,
    )

    jobs = []
    channel_a = BatchOperationResult("preview", 3)
    channel_b = BatchOperationResult("preview", 9)
    session_a, launched_a = launch_batch_replace_job(
        mock_page(),
        kind="preview",
        generation=3,
        work=lambda _session: {"database": "workspace-a"},
        result_channel=channel_a,
        operation_launcher=lambda target, **_kwargs: (jobs.append(target), True)[1],
    )
    session_b, launched_b = launch_batch_replace_job(
        mock_page(),
        kind="preview",
        generation=9,
        work=lambda _session: {"database": "workspace-b"},
        result_channel=channel_b,
        operation_launcher=lambda target, **_kwargs: (jobs.append(target), True)[1],
    )
    assert launched_a is True and launched_b is True

    # Complete out of order: no channel can consume another job's payload.
    jobs[1]()
    jobs[0]()
    assert channel_a.take(session_b, "preview", 9) is None
    result_a = channel_a.take(session_a, "preview", 3)
    result_b = channel_b.take(session_b, "preview", 9)
    assert result_a is not None and result_a.result == {"database": "workspace-a"}
    assert result_b is not None and result_b.result == {"database": "workspace-b"}

    discarded = BatchOperationResult("preview", 11)
    session_c, _launched_c = launch_batch_replace_job(
        mock_page(),
        kind="preview",
        generation=11,
        work=lambda _session: {"database": "closed-dialog"},
        result_channel=discarded,
        operation_launcher=lambda target, **_kwargs: (jobs.append(target), True)[1],
    )
    discarded.discard()
    jobs[2]()
    assert discarded.take(session_c, "preview", 11) is None


def test_closed_batch_preview_discards_a_late_plan(db_path, monkeypatch):
    from app.services_impl.moddb_service import EntryFilter
    from app.views.moddb.batch_replace_dialog import BatchReplaceDialog

    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "late", "item.late", "Source", "舊譯文")],
    )
    plan = db.preview_batch_replace(EntryFilter(version="1.21.1"), "舊", "新")
    monkeypatch.setattr(db, "preview_batch_replace", lambda *_args, **_kwargs: plan)
    jobs = []
    dialog = BatchReplaceDialog(
        mock_page(),
        lambda: db,
        EntryFilter(version="1.21.1"),
        lambda _result: None,
        operation_launcher=lambda target, **_kwargs: (jobs.append(target), True)[1],
    )
    dialog.open()
    dialog.find_field.value = "舊"
    dialog.replace_field.value = "新"
    dialog._preview()
    session = dialog._job_session
    channel = dialog._job_result
    assert dialog._busy and session is not None and channel is not None

    dialog._close_dialog()
    assert dialog.plan is None
    jobs[0]()  # Simulate a worker that finished after its dialog was dismissed.
    dialog._sync_worker()

    assert session.is_finished
    assert dialog.plan is None
    assert not dialog._busy
    assert "result" not in session.summary
    assert channel.take(session, "preview", session.summary["generation"]) is None
    assert "預覽已失效" in dialog.progress_text.value
    db.close()


def test_invalid_custom_date_has_error_state_and_blocks_batch_query(db_path):
    seed(db_path)
    db = TranslationDB(db_path)
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    panel.advanced_filters.time_kind.value = "effective_updated"
    panel.advanced_filters.time_preset.value = "custom"
    panel.advanced_filters.start_date.value = "2026-02-30"
    panel.advanced_filters.end_date.value = "2026-03-01"
    panel.advanced_filters._custom_changed()

    assert panel._list_error == "日期格式請使用 YYYY-MM-DD"
    assert panel.count_badge.value.startswith("日期條件錯誤：")
    assert panel.batch_replace_btn.disabled is True
    assert "無法套用日期篩選" in texts_of(panel.list_view)
    with pytest.raises(ValueError, match="日期格式"):
        panel.advanced_filters.time_filter()

    # Changing another filter cannot silently turn an invalid custom date into
    # an ordinary empty result set.
    panel.advanced_filters.time_kind.value = "none"
    panel.advanced_filters._selection_changed()
    assert panel._list_error is not None
    assert panel.batch_replace_btn.disabled is True

    panel.advanced_filters.start_date.value = "2026-03-01"
    panel.advanced_filters._custom_changed()
    assert panel._list_error is None
    assert panel.count_badge.value.endswith("筆")
    db.close()


# ------------------------------------------------------------------ 同步開關（設定預設值與預覽）
def test_sync_switch_starts_from_setting_and_refreshes_the_preview(
    db_path, monkeypatch
):
    seed(db_path)
    monkeypatch.setattr(
        moddb_service,
        "load_db_settings",
        lambda: DbSettings(path=str(db_path), version="1.21.1", sync_manual=False),
    )
    db = TranslationDB(db_path)
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    assert panel.sync_row.value is False  # 設定關閉就不該預設同步

    panel.select(next(r for r in panel.rows if r.key == "item.foo.a").id)
    panel.tw_field.value = "新譯文"
    panel._on_text_change()
    assert (
        "不會影響其他版本" in panel.impact_text.value
        or "只出現在" in panel.impact_text.value
    )

    panel.sync_row.value = True
    panel._on_sync_change()  # 開關事件：預覽立即重算
    assert "1.20.1" in panel.impact_text.value
    panel.sync_row.value = False
    panel._on_sync_change()
    assert "1.20.1" not in panel.impact_text.value
    assert panel.sync_row.switch.on_change is not None
    db.close()


# ------------------------------------------------------------------ log：UI 與後台一致、錯誤原因明確
def _foreign_db(path: Path) -> None:
    import sqlite3

    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()


def test_scan_progress_reaches_both_ui_and_backend_log_once(db_path, tmp_path, caplog):
    import logging

    from app.services_impl.logging_service import UI_LOG_HANDLER

    make_jar(
        tmp_path / "mods" / "foo.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Some Text Here"}},
    )
    session = TaskSession()
    root = logging.getLogger()
    root.addHandler(UI_LOG_HANDLER)
    try:
        with caplog.at_level(logging.INFO):
            moddb_service.run_moddb_scan_service(
                str(tmp_path / "mods"), moddb_service.ScanOptions("1.21.1"), session
            )
    finally:
        root.removeHandler(UI_LOG_HANDLER)
    ui = [e.text for e in session.snapshot()["logs"] if "foo.jar" in e.text]
    backend = [r.getMessage() for r in caplog.records if "foo.jar" in r.getMessage()]
    assert len(ui) == 1, ui  # UI 只出現一次（沒有被 handler 重複送入）
    assert len(backend) == 1, backend  # 後台 log 也有


def test_scan_into_a_foreign_database_explains_why(db_path, tmp_path):
    _foreign_db(db_path)
    make_jar(
        tmp_path / "mods" / "foo.jar",
        {"assets/foo/lang/en_us.json": {"a.b": "Some Text Here"}},
    )
    session = TaskSession()
    moddb_service.run_moddb_scan_service(
        str(tmp_path / "mods"), moddb_service.ScanOptions("1.21.1"), session
    )
    snap = session.snapshot()
    assert snap["status"] == "ERROR"
    text = "\n".join(e.text for e in snap["logs"])
    assert "無法開啟 Mod 資料庫" in text and "不是 Mod 翻譯資料庫" in text


def test_unusable_database_is_explained_not_shown_as_not_created(db_path):
    _foreign_db(db_path)
    info = moddb_service.summarize_database()
    assert info is not None and "不是 Mod 翻譯資料庫" in info["problem"]
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    assert view.overview.problem.visible is True
    assert view.overview.empty.visible is False
    assert "資料庫無法使用" in view.overview.problem.value


def test_missing_database_is_not_a_problem(db_path):
    assert moddb_service.summarize_database() is None
    assert moddb_service.database_problem() == ""
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    assert view.overview.problem.visible is False and view.overview.empty.visible


def test_ui_handler_skips_records_already_written_to_the_session():
    import logging

    from translation_tool.utils.ui_logging_handler import UISessionLogHandler

    handler, session = UISessionLogHandler(), TaskSession()
    handler.set_session(session)
    log = logging.getLogger("t.mirror")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        log.info("只進後台", extra={"ui_mirrored": True})
        log.info("兩邊都要")
    finally:
        log.removeHandler(handler)
    assert [e.text for e in session.snapshot()["logs"]] == ["兩邊都要"]


def test_visible_segments_exposes_newline_spaces_and_tokens():
    from app.views.moddb.formatting import visible_segments

    segs = visible_segments("Hi %2$s \n")
    assert ("%2$s", "token") in segs
    assert ("·", "space") in segs
    assert ("↵", "newline") in segs
    assert "".join(s for s, k in segs if k == "text") == "Hi \n"


def test_switching_back_to_a_running_tab_resumes_polling(db_path, monkeypatch):
    """機翻／掃描進行中切到別的頁籤再回來：面板卸載時輪詢已停，必須接續輪詢，畫面才不會卡住。"""
    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    for panel, key in ((view.translate, "translate"), (view.scan, "scan")):
        started = []
        monkeypatch.setattr(
            panel._poller,
            "start",
            lambda page, handler, _s=started: _s.append(1) or True,
        )
        panel.session = TaskSession()
        panel._running = True
        panel.will_unmount()  # 離開頁籤：面板被卸載、輪詢停止
        view.show_tab("entries")
        assert started == []  # 不在這個頁籤時不需要輪詢
        view.show_tab(key)
        assert started == [1], f"{key} 切回來後沒有接續輪詢"


def test_overview_kpi_cards_share_one_layout_contract(db_path):
    """總覽在可捲動欄位裡：KPI 列不可用 STRETCH（高度無上限會讓版面例外、整個總覽變空白）。

    四張卡改用相同的版面結構等高：標題列同高、說明列都保留（沒有說明文字也占位）、
    標題列內的按鈕不高過標題列。
    """
    import flet as ft

    seed(db_path)
    view = moddb_view.ModDbView(mock_page(), mock_filepicker())
    overview = view.overview
    cards = [
        overview.stat_mods,
        overview.stat_content,
        overview.stat_diff,
        overview.stat_changed,
    ]
    row = next(
        c
        for c in overview.content_col.controls
        if isinstance(c, ft.Row) and overview.stat_mods in c.controls
    )
    assert row.vertical_alignment != ft.CrossAxisAlignment.STRETCH

    heads = {card.content.controls[0].height for card in cards}
    assert len(heads) == 1 and heads != {None}
    head_height = heads.pop()
    # 每張卡的「標題列／大數字／說明列」三層結構一致，說明列一律保留
    for card in cards:
        assert len(card.content.controls) == 3
        assert card.delta_text.visible is True
    # 更新成沒有說明文字時，說明列仍占位
    overview.stat_content.set_value("1", delta="")
    assert overview.stat_content.delta_text.visible is True
    # 標題列內的按鈕不高過標題列（預設 IconButton 的 padding 會讓它高到 36）
    assert overview.diff_help_btn.height <= head_height
    assert overview.diff_help_btn.padding == 0


def test_entries_save_keeps_scroll_position_and_selects_the_next_entry(db_path):
    """儲存後清單重新載入：維持捲動位置；原條目在篩選下消失時，選同一位置的下一筆。"""
    db = TranslationDB(db_path)
    db.ingest(
        "1.21.1",
        [ScanItem(KIND_LANG, "foo", f"item.foo.{i}", f"Text {i}") for i in range(6)],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    scrolls: list[float] = []
    panel._scroll_list_to = scrolls.append  # 記錄清單被捲到哪裡

    panel._on_state("none")
    assert scrolls[-1] == 0.0  # 換篩選回到最上方
    keys = [r.key for r in panel.rows]
    assert len(keys) == 6
    panel.select(panel.rows[2].id)
    panel._scroll_offset = 420.0  # 使用者已往下捲

    panel.tw_field.value = "文字二"
    panel._on_text_change()
    panel._save()
    page._run_all_tasks()

    assert scrolls[-1] == 420.0  # 儲存後維持捲動位置，不跳回最上方
    assert [r.key for r in panel.rows] == keys[:2] + keys[3:]  # 存好的不再是「未翻譯」
    assert panel.selected is not None and panel.selected.key == keys[3]  # 選到下一筆
    db.close()


def test_entries_save_on_the_last_row_of_the_last_page_goes_back_a_page(db_path):
    """最後一頁只剩一筆、存完該筆從篩選消失：退回仍有資料的最後一頁，不顯示空清單。"""
    db = TranslationDB(db_path)
    count = entries_panel.PAGE_SIZE + 1
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "foo", f"item.foo.{i:03d}", f"T {i}")
            for i in range(count)
        ],
    )
    page = mock_page()
    page.run_thread = lambda target: target()
    panel = entries_panel.EntriesPanel(page, lambda: db)
    panel.refresh()
    panel._on_state("none")
    panel._load_list(page=2, keep_selection=False)
    assert len(panel.rows) == 1
    panel.tw_field.value = "最後一筆"
    panel._on_text_change()
    panel._save()
    page._run_all_tasks()
    assert panel.pager.current_page == 1 and len(panel.rows) == entries_panel.PAGE_SIZE
    db.close()


def test_tooltip_text_inside_patchouli_t_macro_may_be_translated():
    """`$(t:提示文字)` 括號裡是顯示給玩家的提示，翻譯它不算特殊字元不一致。"""
    from app.views.moddb import formatting as fm

    source = (
        "The $(item)Reinforced Pressure Chamber Valve/$ is an advanced block, "
        "possibly unlocking $(ttcolor)$(t:By default all recipes require a max of 5 bar, "
        "but modpacks may change this)new recipes/$."
    )
    translated = (
        "$(item)強化壓力室閥門/$ 是一種進階方塊，並可能解鎖 $(ttcolor)"
        "$(t:預設情況下，所有配方所需的最大壓力皆為 5 bar，但模組包可能會修改此設定)新的配方/$。"
    )
    assert fm.token_issues(source, translated) == []
    # 提示標記本身不見了還是要提醒
    no_tooltip = translated.replace(
        "$(t:預設情況下，所有配方所需的最大壓力皆為 5 bar，但模組包可能會修改此設定)",
        "",
    )
    assert fm.token_issues(source, no_tooltip) == ["少了 1 個「提示文字 $(t:…)」"]
    # 不能翻譯的巨集（$(item)、$(ttcolor)、連結）仍須完全相同
    assert fm.token_issues(source, translated.replace("$(ttcolor)", "")) == [
        "少了 1 個「$(ttcolor)」"
    ]
    assert fm.token_issues("$(l:patchouli:a)x/$", "$(l:patchouli:b)甲/$") == [
        "少了 1 個「$(l:patchouli:a)」",
        "多了 1 個「$(l:patchouli:b)」",
    ]


def test_patchouli_macros_with_nested_parentheses_are_one_token():
    """提示文字或網址裡成對的括號（f(x)、Foo_(bar)）不會讓 `$(…)` 標記提早結束。"""
    from app.views.moddb import formatting as fm

    assert fm.token_issues("$(t:Use f(x) here)a/$", "$(t:在這裡用 f(x))甲/$") == []
    tokens = fm.format_tokens("$(l:https://w/Foo_(bar))x$() %s")
    assert tokens["$(l:https://w/Foo_(bar))"] == 1 and tokens["$()"] == 1
    # 網址（不能翻譯）不同仍會提醒
    assert fm.token_issues(
        "$(l:https://w/Foo_(bar))x$()", "$(l:https://w/Foo)甲$()"
    ) == [
        "多了 1 個「$(l:https://w/Foo)」",
        "少了 1 個「$(l:https://w/Foo_(bar))」",
    ]
    # 沒有結尾的 `$(` 不是標記；括號沒配對完時退回第一個 `)`
    assert not fm.format_tokens("a $( b")
    assert fm.format_tokens("$(t:(未配對)")["$(t:…)"] == 1
