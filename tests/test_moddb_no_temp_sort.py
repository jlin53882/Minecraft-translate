"""Mod 資料庫的讀取查詢不得建立臨時排序（``USE TEMP B-TREE FOR ORDER BY``）。

臨時排序要先把整個候選集讀完再排，成本與資料量成正比，不論只取前幾筆。
列表、分頁與候選查詢都要靠索引直接依序走訪。確實無法避免時，
把查詢加進 ``ALLOWED`` 並寫明原因（見 ``docs/developer-guide/performance.md``）。
"""

from __future__ import annotations

import itertools

import pytest

from translation_tool.translation_db import (
    EntryFilter,
    QualityFilter,
    ScanItem,
    TranslationDB,
)
from translation_tool.translation_db.models import TimeFilter

VERSIONS = ("1.20.1", "1.21.1")
STATES = ("all", "none", "diff", "changed", "manual", "ok", "same")
SORTS = (
    "default",
    "entry_newest",
    "entry_oldest",
    "effective_updated_newest",
    "effective_updated_oldest",
    "manual_activity_newest",
    "manual_activity_oldest",
)
QUALITY = (
    "all",
    "mismatch",
    "consistent",
    "unknown_source",
    "missing_translation",
    "whitespace",
    "species_name",
    "species_format",
)

# 查詢計畫中出現這些片段的語句，臨時排序的資料量有明確上界，視為例外。新增項目必須寫明原因。
ALLOWED_PLAN_MARKERS = {
    "json_each": "第二階段只讀「當頁 id」（頁大小為上界）的完整欄位並依原順序排列，排序筆數 <= 頁大小",
    "f.entry_id IS NULL": "未翻譯清單同樣只含單一版本沒有譯文的條目，"
    "依 (類型, 模組, 鍵值) 排序；排序量是缺譯筆數而不是整庫",
    "f.zh_tw = e.en_us": "只列「生效 AI 譯文與原文相同」的條目（實測數百筆），排序量遠小於整庫",
    "t.zh_tw": "不一致清單先經特殊字元前置篩選，只排序疑似不一致的列；"
    "首次計算受 Python 逐列檢查限制，結果有快取（見 performance.md）",
    "WITH RECURSIVE": "kinds() 只排序不同的條目類型（個位數），與資料量無關",
    "MATERIALIZED": "未翻譯候選集只含單一版本沒有譯文的條目（實測數千筆、約 35ms），"
    "要依 (類型, 模組, 鍵值) 排序後才能逐筆比對重用；不是整庫排序",
}

# (排序, 原因)：確實需要臨時排序的組合。新增項目必須寫明原因。
ALLOWED = {
    "entry_newest": "entry.created_at 沒有索引；加索引會拖慢匯入，且只影響最新匯入排序",
    "entry_oldest": "同上（entry.created_at 沒有索引）",
}


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "plans.db")
    for version in VERSIONS:
        database.ingest(
            version,
            [
                ScanItem(
                    kind,
                    mod,
                    f"key.{index}",
                    f"{mod} {kind} {index}",
                    "測試譯文" if index % 2 == 0 else "",
                )
                for mod in ("alpha", "beta")
                for kind in ("lang", "patchouli")
                for index in range(6)
            ],
        )
    entry_id = database.list_entries("1.20.1", limit=1)[0][0].id
    database.save_manual(entry_id, "人工譯文", actor="test")
    yield database
    database.close()


def _temp_sort_plans(db: TranslationDB, call) -> list[str]:
    seen: list[str] = []
    db._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        db._conn.set_trace_callback(None)
    offenders = []
    for statement in seen:
        head = statement.lstrip().upper()
        if not head.startswith(("SELECT", "WITH")):
            continue
        plan = " | ".join(
            str(row[-1]) for row in db._conn.execute(f"EXPLAIN QUERY PLAN {statement}")
        )
        if "TEMP B-TREE FOR ORDER BY" in plan and not any(
            marker in plan or marker in statement for marker in ALLOWED_PLAN_MARKERS
        ):
            offenders.append(f"{statement[:160]} => {plan}")
    return offenders


@pytest.mark.parametrize("sort_by", SORTS)
@pytest.mark.parametrize("state", STATES)
def test_list_entries_states_and_sorts_avoid_temp_sort(db, state, sort_by):
    if sort_by in ALLOWED:
        pytest.skip(ALLOWED[sort_by])
    criteria = EntryFilter(version="1.20.1", state=state, sort_by=sort_by)
    offenders = _temp_sort_plans(
        db, lambda: db.list_entries(criteria=criteria, limit=5, offset=3)
    )
    assert not offenders, offenders


@pytest.mark.parametrize("status", QUALITY)
def test_list_entries_quality_filters_avoid_temp_sort(db, status):
    criteria = EntryFilter(version="1.20.1", quality=QualityFilter(status=status))
    offenders = _temp_sort_plans(
        db, lambda: db.list_entries(criteria=criteria, limit=5, offset=0)
    )
    assert not offenders, offenders


@pytest.mark.parametrize(
    "extra",
    [
        {"mod_id": "alpha"},
        {"kind": "lang"},
        {"source": 6},
        {"review_status": "unreviewed"},
        {"query": "alpha"},
        {
            "time": TimeFilter(
                kind="effective_updated", start_utc="2000-01-01T00:00:00Z"
            )
        },
    ],
)
def test_list_entries_with_filters_avoids_temp_sort(db, extra):
    for sort_by, state in itertools.product(
        (
            "default",
            "effective_updated_newest",
            "effective_updated_oldest",
            "manual_activity_newest",
            "manual_activity_oldest",
        ),
        ("all", "ok", "diff"),
    ):
        try:
            criteria = EntryFilter(
                version="1.20.1", state=state, sort_by=sort_by, **extra
            )
        except (TypeError, ValueError):
            pytest.skip("此篩選組合不存在")
        offenders = _temp_sort_plans(
            db,
            lambda criteria=criteria: db.list_entries(
                criteria=criteria, limit=5, offset=2
            ),
        )
        assert not offenders, (extra, sort_by, state, offenders)


def test_candidate_and_listing_queries_avoid_temp_sort(db):
    calls = (
        lambda: db.untranslated_entries("1.20.1"),
        lambda: db.untranslated_entries("1.20.1", exclude_reusable=True),
        lambda: db.count_reusable("1.20.1"),
        lambda: db.same_as_source_ai_entries("1.20.1"),
        lambda: db.mismatched_translation_entries("1.20.1"),
        lambda: db.reviewed_lang_glossary("alpha", source_texts=("alpha",)),
        lambda: db.missing_by_mod("1.20.1"),
        lambda: db.kinds("1.20.1"),
        lambda: db.mods("1.20.1"),
    )
    for call in calls:
        offenders = _temp_sort_plans(db, call)
        assert not offenders, offenders


def _all_plans(db: TranslationDB, call) -> list[str]:
    seen: list[str] = []
    db._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        db._conn.set_trace_callback(None)
    plans = []
    for statement in seen:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            plans.extend(
                str(row[-1])
                for row in db._conn.execute(f"EXPLAIN QUERY PLAN {statement}")
            )
    return plans


def test_count_reusable_does_not_sort_candidates(db):
    """只回傳計數的查詢不需要排序；通用的 MATERIALIZED 例外不能遮蔽它。"""
    plans = _all_plans(db, lambda: db.count_reusable("1.20.1"))
    assert plans
    assert not [p for p in plans if "TEMP B-TREE FOR ORDER BY" in p], plans


def test_manual_activity_sort_fails_ready_check_without_its_index(db):
    from translation_tool.translation_db import entry_activity

    assert entry_activity.is_installed(db._conn)
    db._conn.execute("DROP INDEX idx_act_ver_at")
    assert not entry_activity.is_installed(db._conn)
    entry_activity.install(db._conn)  # 重新安裝會補回索引
    assert entry_activity.is_installed(db._conn)
    assert entry_activity.verify(db._conn) == []
