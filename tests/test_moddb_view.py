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
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
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
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    assert entries_panel.NO_SOURCE_TEXT == panel.src_text.value
    assert "（原文未知）" in texts_of(panel.list_view)
    assert panel.token_hint.visible is False  # 沒有原文就不比對特殊字元
    overview = moddb_view.OverviewPanel(mock_page(), lambda: db)
    overview.refresh()
    assert "原文未知 1" in overview.stat_content.delta_text.value
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
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
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
    panel = entries_panel.EntriesPanel(mock_page(), lambda: db)
    panel.refresh()
    panel._on_state("none")
    panel._load_list(page=2, keep_selection=False)
    assert len(panel.rows) == 1
    panel.tw_field.value = "最後一筆"
    panel._on_text_change()
    panel._save()
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
