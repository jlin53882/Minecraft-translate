"""跨版本已審核譯文沿用與版本差異審核。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB
from translation_tool.translation_db.cross_version_review import (
    ApplyItem,
    QueryCancelled,
)
from translation_tool.translation_db.schema import (
    SRC_CROSS_VERSION,
    SRC_JAR_TW,
    SRC_MANUAL,
    SRC_SUBTITLE,
)


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "cv.db")
    yield d
    d.close()


def _item(key, en, tw="", mod="foo", source=None):
    return ScanItem(KIND_LANG, mod, key, en, tw, "", source)


def _entry(db, version, key, mod="foo"):
    return next(
        r
        for r in db.list_entries(version, limit=None)[0]
        if r.key == key and r.mod_id == mod
    )


def _seed_reviewed(db, version="1.20.1", key="k", text="亮色"):
    db.ingest(version, [_item(key, "Bright", "")])
    eid = _entry(db, version, key).id
    db.save_manual(eid, text, propagate=False)
    db.review_manual(eid, expected_zh_tw=text, propagate=False)
    return eid


def _import(db, version, items):
    """匯入並回傳本次匯入交易實際新增或新增譯文的條目編號。"""
    ids: set[int] = set()
    db.ingest(version, items, entry_ids_out=ids)
    return ids


def _translation_rows(db, version, key):
    eid = _entry(db, version, key).id
    return {
        t.source: (t.zh_tw, t.review_status) for t in db.entry_detail(eid).translations
    }


def _apply_item(row, ref=None):
    return ApplyItem.from_row(row, ref or row.reference)


def test_user_scenario_import_reuses_reviewed_translation(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    assert _entry(db, "26.1", "k").zh_tw == "明亮"

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert (result.applied, result.conflicts, result.skipped_reviewed) == (1, 0, 0)
    row = _entry(db, "26.1", "k")
    assert row.zh_tw == "亮色"
    assert row.review_status == "reviewed"
    assert db.list_cross_version_differences("26.1") == []


def test_entry_without_any_translation_is_reused_too(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "")])  # 沒有 zh_tw 的新條目
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"


def test_repeat_import_is_idempotent(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 1
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 0


def test_conflicting_references_are_listed_not_applied(db):
    _seed_reviewed(db, "1.20.1", text="亮色")
    _seed_reviewed(db, "1.19.2", text="明亮")
    ids = _import(db, "26.1", [_item("k", "Bright", "光亮")])

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert (result.applied, result.conflicts) == (0, 1)
    assert _entry(db, "26.1", "k").zh_tw == "光亮"
    rows = db.list_cross_version_differences("26.1")
    assert len(rows) == 1 and rows[0].conflict
    assert {r.version for r in rows[0].refs} == {"1.20.1", "1.19.2"}


def test_target_reviewed_is_not_overwritten(db):
    _seed_reviewed(db, "1.20.1", text="亮色")
    ids = _import(db, "26.1", [_item("k", "Bright", "")])
    eid = _entry(db, "26.1", "k").id
    db.save_manual(eid, "光亮", propagate=False)
    db.review_manual(eid, expected_zh_tw="光亮", propagate=False)

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert (result.applied, result.skipped_reviewed) == (0, 1)
    assert _entry(db, "26.1", "k").zh_tw == "光亮"


def test_different_source_text_is_not_reused(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Brighter", "明亮")])
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 0
    assert db.list_cross_version_differences("26.1") == []


def test_only_the_entry_ids_of_this_import_are_reused(db):
    """範圍是本次匯入回報的條目，不靠時間推測：連續快速匯入也不會互相影響。"""
    _seed_reviewed(db, key="a", text="甲")
    _seed_reviewed(db, key="b", text="乙")
    first = _import(db, "26.1", [_item("a", "Bright", "明亮")])
    second = _import(db, "26.1", [_item("b", "Bright", "明亮")])  # 同一秒內的第二批
    assert first.isdisjoint(second)

    assert db.reuse_reviewed_translations("26.1", entry_ids=second).applied == 1
    assert _entry(db, "26.1", "b").zh_tw == "乙"
    assert _entry(db, "26.1", "a").zh_tw == "明亮"  # 第一批的條目沒被碰


def test_untouched_entries_are_not_reported_by_a_noop_import(db):
    _seed_reviewed(db)
    _import(db, "26.1", [_item("k", "Bright", "明亮")])
    ids = _import(
        db, "26.1", [_item("k", "Bright", "明亮")]
    )  # 完全相同，沒有新增任何東西
    assert ids == set()
    assert db.reuse_reviewed_translations("26.1", entry_ids=ids).applied == 0


def test_reuse_is_undoable(db):
    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)
    assert db.revert(result.history_id) == 1
    row = _entry(db, "26.1", "k")
    assert row.zh_tw == "明亮" and row.source == SRC_JAR_TW


@pytest.mark.parametrize(
    "ref_source", [SRC_JAR_TW, SRC_SUBTITLE, SRC_MANUAL], ids=lambda s: f"ref{s}"
)
def test_reuse_keeps_the_targets_own_translation_even_with_the_same_source(
    db, ref_source
):
    """目標尚未審核時一律另寫 SRC_CROSS_VERSION 列，目標原本的譯文與來源不被覆寫。"""
    db.ingest("1.20.1", [_item("k", "Bright", "亮色", source=ref_source)])
    ref = _entry(db, "1.20.1", "k")
    db.review_manual(ref.id, expected_zh_tw="亮色", propagate=False)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮", source=ref_source)])

    result = db.reuse_reviewed_translations("26.1", entry_ids=ids)

    assert result.applied == 1
    rows = _translation_rows(db, "26.1", "k")
    assert rows[ref_source][0] == "明亮"  # 目標自己的譯文仍在原本的來源
    assert rows[SRC_CROSS_VERSION] == ("亮色", "reviewed")
    effective = _entry(db, "26.1", "k")
    assert (effective.zh_tw, effective.source) == ("亮色", SRC_CROSS_VERSION)
    assert db.revert(result.history_id) == 1
    assert SRC_CROSS_VERSION not in _translation_rows(db, "26.1", "k")
    assert _entry(db, "26.1", "k").zh_tw == "明亮"


def test_latest_reference_is_most_recently_reviewed_version(db):
    _seed_reviewed(db, "1.19.2", text="舊譯")
    _seed_reviewed(db, "1.20.1", text="新譯")
    db.ingest("26.1", [_item("k", "Bright", "")])
    fixed = db.list_cross_version_differences("26.1", ref_version="1.19.2")
    assert [r.reference.zh_tw for r in fixed] == ["舊譯"]
    both = db.list_cross_version_differences("26.1")
    assert both[0].conflict and both[0].reference.version == "1.20.1"


def test_review_time_comes_from_the_review_history_not_from_updates(db):
    """審核後又被其他操作更新譯文列，「最近審核」仍以審核事件為準。"""
    _seed_reviewed(db, "1.19.2", text="舊譯")
    eid = _seed_reviewed(db, "1.20.1", text="新譯")
    # 1.19.2 的列之後被更新（updated_at 變新），但沒有新的審核事件
    old = _entry(db, "1.19.2", "k").id
    with db._tx() as conn:
        conn.execute(
            "UPDATE translation SET updated_at='2999-01-01 00:00:00' WHERE entry_id=?",
            (old,),
        )
    db.ingest("26.1", [_item("k", "Bright", "")])
    row = db.list_cross_version_differences("26.1")[0]
    by_version = {r.version: r for r in row.refs}
    assert by_version["1.20.1"].review_seq > by_version["1.19.2"].review_seq
    assert by_version["1.20.1"].review_seq > 0 and eid


def test_apply_reference_and_stale_guard(db):
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    stale = ApplyItem(
        row.entry_id,
        row.reference.version,
        "別人改過",
        row.reference.zh_tw,
        row.target_source,
        row.target_reviewed,
        row.target_revision,
        row.reference.revision,
    )
    assert db.apply_cross_version_references([stale]).stale == 1

    result = db.apply_cross_version_references([_apply_item(row)])
    assert result.applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"
    assert db.revert(result.history_id) == 1
    assert _entry(db, "26.1", "k").zh_tw == "明亮"


def test_apply_is_stale_when_the_target_became_reviewed_in_between(db):
    """畫面上目標是未審核；套用前同一段譯文被標為已審核 → 不可覆寫（文字沒變也一樣）。"""
    _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    assert not row.target_reviewed
    item = _apply_item(row)

    tgt = _entry(db, "26.1", "k")
    db.review_manual(tgt.id, expected_zh_tw="明亮", propagate=False)  # 另一個操作先審核

    result = db.apply_cross_version_references([item])

    assert (result.applied, result.stale) == (0, 1)
    after = _entry(db, "26.1", "k")
    assert (after.zh_tw, after.review_status) == ("明亮", "reviewed")


def test_apply_is_stale_when_the_target_revision_or_source_changed(db):
    _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    row = db.list_cross_version_differences("26.1")[0]
    item = _apply_item(row)

    # 同樣的文字被重新儲存成人工譯文（來源與 revision 都變了，文字沒變）
    tgt = _entry(db, "26.1", "k")
    db.save_manual(tgt.id, "明亮", propagate=False)

    assert db.apply_cross_version_references([item]).stale == 1


def test_apply_is_stale_when_the_reference_changed(db):
    ref_id = _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    item = _apply_item(db.list_cross_version_differences("26.1")[0])

    # 參考被改成別的又改回原文（A→B→A）：文字相同，但 revision 已經不同
    db.save_manual(ref_id, "別的", propagate=False)
    db.save_manual(ref_id, "亮色", propagate=False)
    db.review_manual(ref_id, expected_zh_tw="亮色", propagate=False)

    assert db.apply_cross_version_references([item]).stale == 1


def test_explicit_overwrite_of_a_reviewed_target_still_works(db):
    """畫面上就是已審核的目標（使用者勾了「包含目標已審核」）仍可套用。"""
    _seed_reviewed(db, text="亮色")
    db.ingest("26.1", [_item("k", "Bright", "")])
    tgt = _entry(db, "26.1", "k")
    db.save_manual(tgt.id, "明亮", propagate=False)
    db.review_manual(tgt.id, expected_zh_tw="明亮", propagate=False)
    row = db.list_cross_version_differences("26.1")[0]
    assert row.target_reviewed

    assert db.apply_cross_version_references([_apply_item(row)]).applied == 1
    assert _entry(db, "26.1", "k").zh_tw == "亮色"


def test_scan_service_helper_reuses_and_reports(db):
    from types import SimpleNamespace

    from app.services_impl.moddb_service import _reuse_reviewed_after_scan
    from translation_tool.translation_db.scanner import ScanOptions, ScanReport

    _seed_reviewed(db)
    ids = _import(db, "26.1", [_item("k", "Bright", "明亮")])
    logs, summaries = [], []
    session = SimpleNamespace(
        set_summary=summaries.append,
        add_log=lambda *a, **k: logs.append(a),
        log=lambda *a, **k: logs.append(a),
    )
    report = ScanReport(version="26.1")
    report.touched_entry_ids.update(ids)
    _reuse_reviewed_after_scan(session, db, ScanOptions(version="26.1"), report)
    assert report.reuse_applied == 1
    assert summaries[-1]["reuse_applied"] == 1
    # 自動沿用的歷史編號帶進摘要，之後可從對話框直接復原
    assert summaries[-1]["reuse_history_id"] == report.reuse_history_id is not None
    assert db.revert(report.reuse_history_id) == 1
    assert _entry(db, "26.1", "k").zh_tw == "明亮"

    off = ScanReport(version="26.1")
    off.touched_entry_ids.update(ids)
    _reuse_reviewed_after_scan(
        session, db, ScanOptions(version="26.1", reuse_reviewed=False), off
    )
    assert off.reuse_applied == 0


@pytest.mark.parametrize(
    ("ref_source", "target_source"),
    [(SRC_JAR_TW, 3), (3, 6), (6, 3), (3, 3)],
)
def test_apply_replaces_a_reviewed_target_whatever_the_sources(
    db, ref_source, target_source
):
    """套用參考譯文後，目標目前顯示的譯文一定要變成參考譯文（不受來源排序影響），且可復原。"""
    db.ingest("1.21.1", [_item("k", "Bright", "亮色", source=ref_source)])
    ref = _entry(db, "1.21.1", "k")
    db.review_manual(ref.id, expected_zh_tw="亮色", propagate=False)
    db.ingest("1.20.1", [_item("k", "Bright", "明亮", source=target_source)])
    tgt = _entry(db, "1.20.1", "k")
    db.review_manual(tgt.id, expected_zh_tw="明亮", propagate=False)

    row = db.list_cross_version_differences("1.20.1", ref_version="1.21.1")[0]
    result = db.apply_cross_version_references([_apply_item(row)])

    after = _entry(db, "1.20.1", "k")
    assert result.applied == 1
    assert (after.zh_tw, after.review_status) == ("亮色", "reviewed")
    assert db.list_cross_version_differences("1.20.1", ref_version="1.21.1") == []
    assert db.revert(result.history_id) == 1
    assert _entry(db, "1.20.1", "k").zh_tw == "明亮"


def test_list_differences_can_be_cancelled_between_chunks(db):
    _seed_reviewed(db, key="a", text="甲")
    db.ingest("26.1", [_item("a", "Bright", "明亮")])
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1  # 第一段之後取消

    with pytest.raises(QueryCancelled):
        db.list_cross_version_differences("26.1", cancel=cancel)


def test_difference_queries_use_no_temporary_sort(db):
    """CLAUDE.md：translation_db 的查詢不得出現 ``USE TEMP B-TREE FOR ORDER BY``。"""
    _seed_reviewed(db)
    db.ingest("26.1", [_item("k", "Bright", "明亮")])
    from translation_tool.translation_db import cross_version_review as cv

    captured: list[tuple[str, list]] = []
    conn = db._conn
    real = conn.execute

    class Spy:
        def __getattr__(self, name):
            return getattr(conn, name)

        def execute(self, sql, params=()):
            captured.append((sql, list(params)))
            return real(sql, params)

    spy = Spy()
    cv._fetch_rows(spy, "26.1", ref_version="", entry_ids=[1, 2])  # type: ignore[arg-type]
    db.list_cross_version_differences("26.1")
    for sql, params in captured:
        if not sql.lstrip().upper().startswith("SELECT"):
            continue
        plan = " ".join(
            str(r[3]) for r in real(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
        )
        # 規則禁的是 ORDER BY 的臨時排序；共用語系比對子查詢的 count(DISTINCT)
        # 只在單一條目的少數語系上建暫存結構，不在此限。
        assert "TEMP B-TREE FOR ORDER BY" not in plan, (sql[:80], plan)
