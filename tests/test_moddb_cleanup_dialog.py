"""資料清理視窗：「匯出已翻譯內容檢查檔」按鈕與預覽、刪除、還原的畫面。"""

from __future__ import annotations

from pathlib import Path

import flet as ft

from app.views.moddb import cleanup_dialog as module
from tests.conftest import mock_page
from translation_tool.translation_db import ScanItem, TranslationDB

VERSION = "1.21.1"


def _db(tmp_path: Path) -> TranslationDB:
    db = TranslationDB(tmp_path / "t.db")
    db.ingest(
        VERSION,
        [
            ScanItem("lang", "m", "item.m.a", "Alpha"),
            ScanItem("lang", "m", "item.m.b", "Beta"),
            ScanItem("guide", "g", "g/b.md#0", "Real text here.", zh_tw="真正的文字"),
        ],
    )
    db.save_manual(db._one("SELECT id FROM entry WHERE key = 'item.m.a'")[0], "甲")
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


def test_overview_mentions_translated_entries_with_the_export_button(
    tmp_path, monkeypatch
):
    dialog = _dialog(monkeypatch, _db(tmp_path), [])
    lines = _texts(dialog.overview_body.controls)
    assert any("已有生效譯文 2 筆" in line for line in lines)
    assert any("匯出已翻譯內容檢查檔" in line for line in lines)
    assert dialog.export_translated_btn.content == "匯出已翻譯內容檢查檔"


def test_translated_export_preview_apply_and_revert_flow(tmp_path, monkeypatch):
    db = _db(tmp_path)
    completed: list[int] = []
    dialog = _dialog(monkeypatch, db, completed)
    entries = db._one("SELECT COUNT(*) FROM entry")[0]

    dialog.export_translated_btn.on_click(None)
    assert dialog.export_summary.visible
    assert "已有生效譯文的條目" in dialog.export_summary.value
    assert "人工 1" in dialog.export_summary.value
    assert "模組自帶繁中 1" in dialog.export_summary.value
    assert dialog.report_dd.value.endswith("-translated")
    assert dialog.preview_btn.disabled is False
    assert "2 個模組檔" in dialog.export_summary.value
    label = next(
        o.text for o in dialog.report_dd.options if o.key == dialog.report_dd.value
    )
    assert label.endswith("（資料夾，2 個模組檔）")

    dialog._preview()
    assert (
        "已有譯文：刪除會連同所有來源的譯文與歷史一起移除" in dialog.plan_summary.value
    )
    assert dialog.apply_btn.content == "確認刪除 2 筆"
    assert dialog.apply_btn.disabled is False

    dialog._apply()
    assert db._one("SELECT COUNT(*) FROM entry")[0] == entries - 2
    assert completed == [1]
    assert dialog.revert_btn.disabled is False

    dialog._revert()
    assert db._one("SELECT COUNT(*) FROM entry")[0] == entries
    assert db._one("SELECT COUNT(*) FROM effective")[0] == 2


def test_rule_export_does_not_list_translated_entries(tmp_path, monkeypatch):
    dialog = _dialog(monkeypatch, _db(tmp_path), [])
    dialog.export_btn.on_click(None)
    assert "來源：" not in dialog.export_summary.value
    assert not dialog.report_dd.value.endswith("-translated")


def test_busy_state_disables_both_export_buttons(tmp_path, monkeypatch):
    dialog = _dialog(monkeypatch, _db(tmp_path), [])
    dialog._set_busy(True, "處理中")
    assert dialog.export_btn.disabled and dialog.export_translated_btn.disabled
    dialog._set_busy(False)
    assert not dialog.export_btn.disabled
    assert not dialog.export_translated_btn.disabled
