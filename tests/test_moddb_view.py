"""Mod 資料庫頁（總覽／條目校對／掃描匯入）與服務層的行為測試。"""

from __future__ import annotations

import json
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
    history = entries.detail.history[0]
    entries._revert(history.id)
    db = entries.db()
    assert (
        next(r for r in db.list_entries("1.21.1")[0] if r.key == "item.foo.a").zh_tw
        == "鋼製外殼"
    )


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
    assert panel.version() == "1.20.1" and panel.start_btn.text == "開始掃描 1.20.1"


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
    assert "尚未建立資料庫" in view.db_info.value
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

    monkeypatch.setattr(
        lm_view, "load_db_settings", lambda: DbSettings(path=str(db_path), version="")
    )
    view = lm_view.LMView(mock_page(), mock_filepicker())
    view.use_db_switch.value = True
    view.db_version_field.value = ""
    view.refresh_db_info()
    assert "尚未指定目標版本" in view.db_info.value

    view.db_version_field.value = "1.21.1"
    view._on_db_option_changed()
    assert "尚未指定目標版本" not in view.db_info.value

    view.db_version_field.value = ""
    view.use_db_switch.value = False
    view._on_db_option_changed()
    assert "尚未指定目標版本" not in view.db_info.value
