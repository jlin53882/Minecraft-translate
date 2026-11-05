"""跨版本已審核譯文沿用與版本差異審核。"""

from __future__ import annotations

import time

import pytest

from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB
from translation_tool.translation_db.cross_version_review import ApplyItem
from translation_tool.translation_db.schema import SRC_JAR_TW


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "cv.db")
    yield d
    d.close()


def _item(key, en, tw="", mod="foo"):
    return ScanItem(KIND_LANG, mod, key, en, tw, "")


def _entry(db, version, key, mod="foo"):
    return next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )


def _review(db, version, key, text):
    """在 ``version`` 建立並審核一筆人工譯文。"""
    db.ingest(version, [_item(key, text and "Bright", "")])
    eid = _entry(db, version, key).id
    db.save_manual(eid, text, propagate=False)
    db.review_manual(eid, expected_zh_tw=text, propagate=False)
    return eid


def _since(db):
    time.sleep(1.1)  # CURRENT_TIMESTAMP 只到秒：讓先前的寫入落在 since 之前
    return db.db_timestamp()


def _seed_reviewed(db, version="1.20.1", key="k", text="亮色"):
    db.ingest(version, [_item(key, "Bright", "")])
    eid = _entry(db, version, key).id
    db.save_manual(eid, text, propagate=False)
    db.review_manual(eid, expected_zh_tw=text, propagate=False)
    return eid


def test_user_scenario_import_reuses_reviewed_translation(db):
    _seed_reviewed(db)
    since = _since(db)
    # 26.1 匯入：模組自帶繁中與已審核譯文不同，造成「版本不同」
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    assert _entry(db, "26.1", "k").zh_tw == "明亮"

    result = db.reuse_reviewed_translations("26.1", since=since)

    assert (result.applied, result.conflicts, result.skipped_reviewed) == (1, 0, 0)
    row = _entry(db, "26.1", "k")
    assert row.zh_tw == "亮色"
    assert row.review_status == "reviewed"
    # 版本差異消失
    assert db.list_cross_version_differences("26.1") == []


def test_repeat_import_is_idempotent(db):
    _seed_reviewed(db)
    since = _since(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    assert db.reuse_reviewed_translations("26.1", since=since).applied == 1
    again = db.reuse_reviewed_translations("26.1", since=since)
    assert again.applied == 0


def test_conflicting_references_are_listed_not_applied(db):
    _seed_reviewed(db, "1.20.1", text="亮色")
    _seed_reviewed(db, "1.19.2", text="明亮")
    since = _since(db)
    db.ingest("26.1", [_item("k", "Bright", "光亮")])

    result = db.reuse_reviewed_translations("26.1", since=since)

    assert (result.applied, result.conflicts) == (0, 1)
    assert _entry(db, "26.1", "k").zh_tw == "光亮"
    rows = db.list_cross_version_differences("26.1")
    assert len(rows) == 1 and rows[0].conflict
    assert {r.version for r in rows[0].refs} == {"1.20.1", "1.19.2"}


def test_target_reviewed_is_not_overwritten(db):
    _seed_reviewed(db, "1.20.1", text="亮色")
    since = _since(db)
    db.ingest("26.1", [_item("k", "Bright", "")])
    eid = _entry(db, "26.1", "k").id
    db.save_manual(eid, "光亮", propagate=False)
    db.review_manual(eid, expected_zh_tw="光亮", propagate=False)

    result = db.reuse_reviewed_translations("26.1", since=since)

    assert (result.applied, result.skipped_reviewed) == (0, 1)
    assert _entry(db, "26.1", "k").zh_tw == "光亮"


def test_different_source_text_is_not_reused(db):
    _seed_reviewed(db)
    since = _since(db)
    db.ingest("26.1", [_item("k", "Brighter", "明亮")])
    assert db.reuse_reviewed_translations("26.1", since=since).applied == 0
    assert db.list_cross_version_differences("26.1") == []


def test_only_entries_touched_by_this_import_are_reused(db):
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    since = _since(db)
    db.ingest("26.1", [_item("other", "Dark", "暗")])  # 本次匯入沒碰 k
    assert db.reuse_reviewed_translations("26.1", since=since).applied == 0
    assert _entry(db, "26.1", "k").zh_tw == "明亮"


def test_reuse_is_undoable(db):
    _seed_reviewed(db)
    since = _since(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    result = db.reuse_reviewed_translations("26.1", since=since)
    assert db.revert(result.history_id) == 1
    row = _entry(db, "26.1", "k")
    assert row.zh_tw == "明亮" and row.source == SRC_JAR_TW


def test_latest_reference_is_most_recently_reviewed_version(db):
    _seed_reviewed(db, "1.19.2", text="舊譯")
    time.sleep(1.1)
    _seed_reviewed(db, "1.20.1", text="新譯")
    db.ingest("26.1", [_item("k", "Bright", "")])
    # 兩個版本衝突；固定參考版本時只看該版本
    fixed = db.list_cross_version_differences("26.1", ref_version="1.19.2")
    assert [r.reference.zh_tw for r in fixed] == ["舊譯"]
    both = db.list_cross_version_differences("26.1")
    assert both[0].conflict and both[0].reference.version == "1.20.1"


def test_apply_reference_and_stale_guard(db):
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    ref = row.reference
    stale = ApplyItem(row.entry_id, ref.version, "別人改過", ref.zh_tw)
    assert db.apply_cross_version_references([stale]).stale == 1

    ok = ApplyItem(row.entry_id, ref.version, row.target_zh_tw, ref.zh_tw)
    result = db.apply_cross_version_references([ok])
    assert result.applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"
    assert db.revert(result.history_id) == 1
    assert _entry(db, "26.1", "k").zh_tw == "明亮"


def test_scan_service_helper_reuses_and_reports(db):
    from types import SimpleNamespace

    from app.services_impl.moddb_service import _reuse_reviewed_after_scan
    from translation_tool.translation_db.scanner import ScanOptions, ScanReport

    _seed_reviewed(db)
    since = _since(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    logs, summaries = [], []
    session = SimpleNamespace(
        set_summary=summaries.append,
        add_log=lambda *a, **k: logs.append(a),
        log=lambda *a, **k: logs.append(a),
    )
    report = ScanReport(version="26.1")
    _reuse_reviewed_after_scan(session, db, ScanOptions(version="26.1"), report, since)
    assert report.reuse_applied == 1
    assert summaries[-1]["reuse_applied"] == 1
    off = ScanReport(version="26.1")
    _reuse_reviewed_after_scan(
        session, db, ScanOptions(version="26.1", reuse_reviewed=False), off, since
    )
    assert off.reuse_applied == 0
