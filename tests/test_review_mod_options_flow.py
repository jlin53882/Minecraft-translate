"""同文一致性與版本差異審核：模組選單只列有結果的模組（經背景載入的完整流程）。

以真實資料庫跑 ``_reload`` → 背景 ``work`` → ``_loaded``，背景工作與回到 UI 執行緒都改成同步執行。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb import same_text_review_dialog as stdlg
from app.views.moddb import version_diff_dialog as vdd
from app.views.moddb.result_mod_options import ResultModOptions
from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "options.db")
    yield database
    database.close()


@pytest.fixture
def shown(monkeypatch):
    """記錄每個下拉選單最後一次設定的選項值。"""
    record: dict[int, list[str]] = {}

    def capture(dd, pairs):
        record[id(dd)] = [key for key, _ in pairs]
        return True

    monkeypatch.setattr(stdlg.kit, "set_dropdown_options", capture)
    monkeypatch.setattr(vdd.kit, "set_dropdown_options", capture)
    return record


def _item(key, en, tw="", mod="foo"):
    return ScanItem(KIND_LANG, mod, key, en, tw, "", None)


def _review(db, version, key, text, mod="foo"):
    entry = next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )
    db.save_manual(entry.id, text, propagate=False)
    db.review_manual(entry.id, expected_zh_tw=text, propagate=False)


def _synchronous(dialog, db, calls):
    dialog.get_db = lambda: db
    dialog._closed = False
    dialog._generation = 0
    dialog._busy = False
    dialog._set_busy = lambda *a, **k: None
    dialog._render = lambda: None
    dialog._safe_update = lambda: None
    dialog._post = lambda callback, *args: callback(*args)
    dialog._launch = lambda work, *a, **k: work()
    dialog.status = SimpleNamespace(value="", visible=False)
    dialog.progress = SimpleNamespace(value=0)
    dialog.selected = set()
    dialog._confirm_overwrite = False
    dialog._mod_options = ResultModOptions()
    dialog.current_page = 1
    real = db.list_same_text_groups

    def counting(*args, **kwargs):
        calls.append(kwargs.get("mod_ids"))
        return real(*args, **kwargs)

    db.list_same_text_groups = counting


def test_same_text_mod_menu_lists_only_mods_with_groups(db, shown):
    version = "1.21.1"
    db.ingest(
        version,
        [
            _item("a", "Close", "關閉", mod="alpha"),
            _item("b", "Close", "閉合", mod="alpha"),
            _item("c", "Open", "開啟", mod="beta"),  # beta 沒有同文群組
        ],
    )
    _review(db, version, "a", "關閉", mod="alpha")
    dialog = object.__new__(stdlg.SameTextReviewDialog)
    calls: list = []
    _synchronous(dialog, db, calls)
    dialog.version_dd = SimpleNamespace(value=version)
    dialog.mod_dd = SimpleNamespace(value="alpha")  # 一開始就選了單一模組
    dialog.kind_dd = SimpleNamespace(value=stdlg.ALL_KINDS)
    dialog.pack_scope = ""
    dialog.en_us = None
    dialog.choice = {}

    dialog._reload()
    # 先以 alpha 掃描，再另取不限模組的結果算出選單
    assert calls == [["alpha"], None]
    assert shown[id(dialog.mod_dd)] == [stdlg.ALL_MODS, "alpha"]
    assert len(dialog.groups) == 1

    calls.clear()
    dialog._reload()  # 同一範圍已算過：不再另取不限模組的結果
    assert calls == [["alpha"]]


def test_version_diff_mod_menu_lists_only_mods_with_differences(db, shown):
    db.ingest("1.20.1", [_item("a", "Bright", "", mod="alpha")])
    _review(db, "1.20.1", "a", "亮色", mod="alpha")
    db.ingest(
        "26.1",
        [
            _item("a", "Bright", "光亮", mod="alpha"),  # 與參考版本不同：有差異
            _item("b", "Dark", "暗色", mod="beta"),  # beta 沒有參考：沒有差異
        ],
    )
    dialog = object.__new__(vdd.VersionDiffDialog)
    _synchronous(dialog, db, [])
    dialog.version_dd = SimpleNamespace(value="26.1")
    dialog.ref_dd = SimpleNamespace(value=None)
    dialog.mod_dd = SimpleNamespace(value="beta")
    dialog._confirm_row = None

    dialog._reload()
    assert dialog.rows == []
    # beta 仍選著所以保留；不限模組的結果只有 alpha 有差異
    assert shown[id(dialog.mod_dd)] == [vdd.ALL_MODS, "alpha", "beta"]

    dialog.mod_dd.value = vdd.ALL_MODS
    dialog._reload()
    assert {row.mod_id for row in dialog.rows} == {"alpha"}
    assert shown[id(dialog.mod_dd)] == [vdd.ALL_MODS, "alpha"]
