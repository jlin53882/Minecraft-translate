"""資料清理視窗：「匯出已有譯文的項目」按鈕與預覽、刪除、還原的畫面。"""

from __future__ import annotations

from pathlib import Path

import flet as ft

from app.views.moddb import cleanup_dialog as module
from tests.conftest import mock_page
from translation_tool.translation_db import ScanItem, TranslationDB

VERSION = "1.21.1"


def _db(tmp_path: Path) -> TranslationDB:
    """符合規則的條目三筆：沒人動過的 TODO、jar 繁中的 _comment、人工翻過的 ← →。"""
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        VERSION,
        [
            ScanItem("lang", "m", "item.m.todo", "TODO"),
            ScanItem("lang", "m", "_comment", "Subtitle", zh_tw="常規"),
            ScanItem("lang", "m", "gui.m.arrows", "← →"),
            ScanItem("lang", "m", "item.m.a", "Alpha"),
        ],
    )
    for key, text in (("gui.m.arrows", "← →"), ("item.m.a", "甲")):
        db.save_manual(db._one("SELECT id FROM entry WHERE key = ?", (key,))[0], text)
    return db


def _dialog(monkeypatch, db, completed: list[int]):
    monkeypatch.setattr(
        module,
        "launch_page_operation",
        lambda _page, target, **_kw: (target(), True)[1],
    )
    monkeypatch.setattr(
        module.CleanupDialog, "_post", lambda self, callback, *args: callback(*args)
    )
    return module.CleanupDialog(mock_page(), lambda: db, lambda: completed.append(1))


def _texts(controls) -> list[str]:
    return [c.value for c in controls if isinstance(c, ft.Text)]


def test_overview_points_protected_entries_to_the_new_button(tmp_path, monkeypatch):
    dialog = _dialog(monkeypatch, _db(tmp_path), [])
    assert dialog.overview_title.value == "版本 1.21.1：可清理 1 筆"
    lines = _texts(dialog.overview_body.controls)
    assert any(
        "另有 2 筆符合條件但已有譯文或歷史" in line and "匯出已有譯文的項目" in line
        for line in lines
    )
    assert dialog.export_translated_btn.content == "匯出已有譯文的項目"


def test_translated_export_preview_apply_and_revert_flow(tmp_path, monkeypatch):
    db = _db(tmp_path)
    completed: list[int] = []
    dialog = _dialog(monkeypatch, db, completed)
    entries = db._one("SELECT COUNT(*) FROM entry")[0]

    dialog.export_translated_btn.on_click(None)
    summary = dialog.export_summary.value
    assert "匯出 2 筆" in summary
    assert (
        "譯文來源：" in summary and "人工 1" in summary and "模組自帶繁中 1" in summary
    )
    assert dialog.report_dd.value.endswith("-translated.json")
    assert dialog.preview_btn.disabled is False

    dialog._preview()
    assert "其中 2 筆已有譯文或歷史" in dialog.plan_summary.value
    assert dialog.apply_btn.content == "確認刪除 2 筆"

    dialog._apply()
    assert db._one("SELECT COUNT(*) FROM entry")[0] == entries - 2
    assert db._one("SELECT COUNT(*) FROM entry WHERE key = 'item.m.a'")[0] == 1
    assert db._one("SELECT COUNT(*) FROM entry WHERE key = 'item.m.todo'")[0] == 1
    assert completed == [1]
    assert dialog.revert_btn.disabled is False

    dialog._revert()
    assert db._one("SELECT COUNT(*) FROM entry")[0] == entries


def test_rule_export_still_lists_only_untouched_entries(tmp_path, monkeypatch):
    dialog = _dialog(monkeypatch, _db(tmp_path), [])
    dialog.export_btn.on_click(None)
    assert "匯出 1 筆" in dialog.export_summary.value
    assert "譯文來源：" not in dialog.export_summary.value
    assert not dialog.report_dd.value.endswith("-translated.json")


def test_busy_state_disables_both_export_buttons(tmp_path, monkeypatch):
    dialog = _dialog(monkeypatch, _db(tmp_path), [])
    dialog._set_busy(True, "處理中")
    assert dialog.export_btn.disabled and dialog.export_translated_btn.disabled
    dialog._set_busy(False)
    assert not dialog.export_btn.disabled
    assert not dialog.export_translated_btn.disabled


def test_changing_report_or_version_invalidates_the_previewed_plan(
    tmp_path, monkeypatch
):
    db = _db(tmp_path)
    dialog = _dialog(monkeypatch, db, [])
    dialog.export_translated_btn.on_click(None)
    dialog._preview()
    assert dialog._plan is not None and dialog.apply_btn.disabled is False
    dialog.report_dd.on_select(None)  # 換檢查檔
    assert dialog._plan is None and dialog.apply_btn.disabled is True
    dialog._preview()
    assert dialog._plan is not None
    dialog.version_dd.on_select(None)  # 換版本
    assert dialog._plan is None and dialog.apply_btn.disabled is True


def test_apply_refuses_a_report_that_changed_after_the_preview(tmp_path, monkeypatch):
    import json

    db = _db(tmp_path)
    dialog = _dialog(monkeypatch, db, [])
    dialog.export_translated_btn.on_click(None)
    dialog._preview()
    path = Path(dialog.report_dd.value)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"] = data["items"][:1]  # 預覽後有人改了檢查檔
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    entries = db._one("SELECT COUNT(*) FROM entry")[0]
    dialog._apply()
    assert db._one("SELECT COUNT(*) FROM entry")[0] == entries
    assert dialog._plan is None and "重新預覽" in dialog.plan_summary.value


def test_apply_refuses_when_the_selected_report_is_not_the_previewed_one(
    tmp_path, monkeypatch
):
    db = _db(tmp_path)
    dialog = _dialog(monkeypatch, db, [])
    dialog.export_translated_btn.on_click(None)
    dialog._preview()
    dialog.report_dd.value = str(Path(dialog.report_dd.value).with_name("other.json"))
    entries = db._one("SELECT COUNT(*) FROM entry")[0]
    dialog._apply()
    assert db._one("SELECT COUNT(*) FROM entry")[0] == entries


def test_older_unfinished_batches_can_be_reverted_from_the_dialog(
    tmp_path, monkeypatch
):
    db = _db(tmp_path)
    dialog = _dialog(monkeypatch, db, [])
    dialog.export_translated_btn.on_click(None)
    dialog._preview()
    dialog._apply()
    first = dialog.batch_dd.value
    db.ingest(VERSION, [ScanItem("lang", "m", "item.m.todo", "TODO")])
    dialog.export_btn.on_click(None)
    dialog._preview()
    dialog._apply()
    second = dialog.batch_dd.value
    assert first != second
    dialog._revert()  # 還原最新的
    assert dialog.batch_dd.value == first  # 較早未還原的批次仍有入口
    assert dialog.revert_btn.disabled is False
    dialog._revert()
    assert dialog.revert_btn.disabled is True
