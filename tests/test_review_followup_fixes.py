"""程式碼審查後的修正：沿用失敗不影響匯入、強制內容索引、逐筆套用確認、空資料庫保護、摘要帶出原文不同筆數。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.views.moddb.version_diff_dialog import VersionDiffDialog
from translation_tool.translation_db import ScanItem, TranslationDB
from translation_tool.translation_db.content_identity import (
    INDEX_NAME,
    content_table_sql,
)
from translation_tool.translation_db.cross_version_review import (
    CrossVersionRow,
    VersionRef,
)
from translation_tool.translation_db.scanner import ScanOptions, ScanReport
from translation_tool.translation_db.schema import KIND_KUBEJS_LANG, KIND_LANG

PACK_A = "atm@All the Mods 10 8.3@40b3569cc156"
PACK_B = "atm@All the Mods 10 8.5@93985ec940f7"


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    yield database
    database.close()


def _session():
    logs, summaries = [], []
    session = SimpleNamespace(
        set_summary=summaries.append,
        add_log=lambda *a, **k: logs.append(a),
        log=lambda *a, **k: logs.append(a),
    )
    return session, logs, summaries


def test_reuse_failure_does_not_fail_the_committed_import(db):
    from app.services_impl.moddb_service import _reuse_reviewed_after_scan

    class Broken:
        def reuse_reviewed_translations(self, *a, **k):
            raise RuntimeError("locked")

    session, logs, _ = _session()
    report = ScanReport(version="26.1")
    report.touched_entry_ids.add(1)
    _reuse_reviewed_after_scan(session, Broken(), ScanOptions(version="26.1"), report)
    assert report.reuse_applied == 0
    assert any("沿用其他版本已審核譯文失敗" in str(entry) for entry in logs)


def test_scan_summary_reports_source_changed_count(db):
    from app.services_impl.moddb_service import _reuse_reviewed_after_scan

    db.ingest("1.20.1", [ScanItem(KIND_LANG, "m", "k", "Old text", "舊")])
    entry_id = db._one("SELECT id FROM entry WHERE mc_version='1.20.1'")[0]
    db.review_manual(entry_id, expected_zh_tw="舊", propagate=False)
    ids = db.ingest("26.1", [ScanItem(KIND_LANG, "m", "k", "Old text!")])

    session, _, summaries = _session()
    report = ScanReport(version="26.1")
    report.touched_entry_ids.update(
        int(r[0]) for r in db._q("SELECT id FROM entry WHERE mc_version='26.1'")
    )
    _reuse_reviewed_after_scan(session, db, ScanOptions(version="26.1"), report)
    assert report.reuse_source_changed == 1
    assert summaries[-1]["reuse_source_changed"] == 1
    assert ids is not None


def test_content_queries_force_the_content_index(db):
    assert content_table_sql(db._conn, "e2") == f"entry e2 INDEXED BY {INDEX_NAME}"
    db._conn.execute(f"DROP INDEX {INDEX_NAME}")
    assert content_table_sql(db._conn, "e2") == "entry e2"


def test_cross_version_queries_still_work_with_the_forced_index(db):
    db.ingest("1.20.1", [ScanItem(KIND_KUBEJS_LANG, PACK_A, "k", "Hello", "你好")])
    db.review_manual(
        db._one("SELECT id FROM entry")[0], expected_zh_tw="你好", propagate=False
    )
    db.ingest("1.21.1", [ScanItem(KIND_KUBEJS_LANG, PACK_B, "k", "hello")])
    rows = db.list_cross_version_differences("1.21.1")
    assert [r.reference.zh_tw for r in rows] == ["你好"]
    assert db.explain_cross_version_exclusions("1.21.1").total == 0
    # 寫回補空白也走同一個索引
    from translation_tool.translation_db import WriteBackItem

    db.write_back(
        "1.21.1",
        [WriteBackItem(KIND_KUBEJS_LANG, PACK_B, "k", "hello", "哈囉")],
    )


def _row(*, reviewed: bool, changed: bool) -> CrossVersionRow:
    ref = VersionRef(2, "1.20.1", "參考", 1, "", "", mod_id="m", en_us="Old")
    return CrossVersionRow(
        1, KIND_LANG, "m", "k", "New", "目標", 1, reviewed, (ref,), 1, changed
    )


def _bare_dialog() -> VersionDiffDialog:
    dialog = object.__new__(VersionDiffDialog)
    dialog._confirm_row = None
    dialog.rendered = 0
    dialog._render = lambda: setattr(dialog, "rendered", dialog.rendered + 1)
    dialog.applied = []
    dialog._apply = lambda items, name: dialog.applied.append(items)
    return dialog


@pytest.mark.parametrize(
    ("reviewed", "changed", "needs_confirm"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_single_apply_asks_again_before_overwriting_or_applying_changed_source(
    reviewed, changed, needs_confirm
):
    dialog = _bare_dialog()
    row = _row(reviewed=reviewed, changed=changed)
    ref = row.reference

    dialog._apply_one(row, ref)
    if needs_confirm:
        assert dialog.applied == [] and dialog._confirm_row == (1, 2)
        assert "確認" in dialog._apply_label(row, ref)
        dialog._apply_one(row, ref)  # 第二次才真的套用
    assert len(dialog.applied) == 1 and dialog._confirm_row is None


def test_scope_change_without_a_database_does_not_raise():
    dialog = object.__new__(VersionDiffDialog)
    dialog.get_db = lambda: None
    dialog.current_page = 3
    dialog._reload = lambda: None
    dialog._on_scope()
    assert dialog.current_page == 1


def test_loading_a_version_without_the_selected_mod_requeries_all_mods():
    from app.views.moddb import version_diff_dialog as vdd

    dialog = object.__new__(VersionDiffDialog)
    dialog._closed = False
    dialog._generation = 1
    dialog._set_busy = lambda *a, **k: None
    dialog.status = SimpleNamespace(value="")
    dialog.mod_dd = SimpleNamespace(value="old_mod")
    dialog.current_page = 4
    reloads = []
    dialog._reload = lambda: reloads.append(dialog.mod_dd.value)
    dialog._render = lambda: reloads.append("render")
    dialog.selected = set()
    dialog._confirm_overwrite = False
    dialog._confirm_row = None
    dialog.rows = ["stale"]
    original = vdd.kit.set_dropdown_options
    vdd.kit.set_dropdown_options = lambda *a, **k: None
    try:
        dialog._loaded(1, [], ["new_mod"], None, None)
    finally:
        vdd.kit.set_dropdown_options = original

    # 舊模組不在新版本：改回全部模組並重新查詢，不沿用舊篩選的結果
    assert reloads == [vdd.ALL_MODS]
    assert dialog.current_page == 1
    assert dialog.rows == ["stale"]
