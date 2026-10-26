"""大型資料庫才會顯現的查詢計畫：批次還原不得讀完整個批次、時間範圍快取與快速路徑。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    EntryFilter,
    ScanItem,
    TimeFilter,
    TranslationDB,
)

VERSION = "1.20.1"


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "large.db")
    d.ingest(
        VERSION,
        [
            ScanItem(KIND_LANG, "mod", f"k.{i:03d}", f"Iron {i}", f"鐵{i}的", "", None)
            for i in range(40)
        ],
    )
    yield d
    d.close()


def _plans(db, marker: str) -> list[str]:
    """追蹤期間含 ``marker`` 的 SELECT，回傳它們的查詢計畫文字。"""
    statements: list[str] = []
    db._conn.set_trace_callback(
        lambda sql: statements.append(sql) if marker in sql else None
    )
    return statements


def test_batch_revert_looks_up_cleared_reviews_by_entry_not_by_batch(db):
    criteria = EntryFilter(version=VERSION)
    plan = db.preview_batch_replace(criteria, "的", "之")
    result = db.execute_batch_replace(plan)
    assert result.updated == 40
    statements = _plans(db, "'review_clear'")
    reverted = db.revert_batch_replace(result.batch_id)
    db._conn.set_trace_callback(None)
    assert reverted.reverted == 40
    assert len(statements) == 40
    # 同一批次可有數十萬列：以批次索引找會讓每個條目都讀完整批，成本變成批次大小的平方
    for sql in statements[:3]:
        plan_text = " ".join(
            row[3] for row in db._conn.execute("EXPLAIN QUERY PLAN " + sql)
        )
        assert "idx_history_batch" not in plan_text
        assert "idx_history_entry" in plan_text


def test_time_data_range_is_cached_and_matches_row_scan(db):
    criteria = EntryFilter(
        version=VERSION,
        time=TimeFilter(kind="effective_updated", start_utc="2099-01-01"),
    )
    expected = db._one(
        "SELECT MIN(t.updated_at), MAX(t.updated_at) FROM effective f "
        "JOIN translation t ON t.entry_id=f.entry_id AND t.source=f.source "
        "JOIN entry e ON e.id=f.entry_id WHERE e.mc_version=?",
        (VERSION,),
    )
    statements = _plans(db, "MIN(")
    assert db.time_data_range(criteria) == tuple(expected)
    first = len(statements)
    assert first == 1
    assert "FROM effective WHERE mc_version" in statements[0]  # 索引快速路徑
    assert db.time_data_range(criteria) == tuple(expected)
    assert len(statements) == first  # 同一資料世代不重算


def test_time_data_range_with_other_filters_uses_generic_scan(db):
    criteria = EntryFilter(
        version=VERSION,
        query="Iron 1",
        time=TimeFilter(kind="effective_updated", start_utc="2099-01-01"),
    )
    low, high = db.time_data_range(criteria)
    expected = db._one(
        "SELECT MIN(t.updated_at), MAX(t.updated_at) FROM effective f "
        "JOIN translation t ON t.entry_id=f.entry_id AND t.source=f.source "
        "JOIN entry e ON e.id=f.entry_id WHERE e.mc_version=? AND e.en_us LIKE '%Iron 1%'",
        (VERSION,),
    )
    assert (low, high) == tuple(expected)


def test_smart_review_twin_completion_starts_from_the_page_groups(tmp_path):
    """同文補查必須由頁面群組出發；讓規劃器從 effective 數萬列起頭會每列重掃 json_each。"""
    d = TranslationDB(tmp_path / "twins.db")
    d.ingest(
        VERSION,
        [
            ScanItem(KIND_LANG, "mod", f"k.{i}", "Same Text", "相同", "", None)
            for i in range(6)
        ]
        + [ScanItem(KIND_LANG, "mod", "z.other", "Other", "其他", "", None)],
    )
    statements = _plans(d, "selected_groups AS")
    snapshots = d.smart_review_candidates(
        version=VERSION, effective_source=1, review_status="unreviewed", limit=1
    )
    d._conn.set_trace_callback(None)
    assert len(snapshots[0].twin_entry_ids) == 5
    assert statements
    plan = [row[3] for row in d._conn.execute("EXPLAIN QUERY PLAN " + statements[0])]
    assert "json_each" in plan[0]
    d.close()
