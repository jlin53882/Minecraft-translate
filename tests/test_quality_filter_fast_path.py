"""品質篩選的加速路徑必須與原本的逐列判斷結果完全相同。

``matches_quality`` 曾對每列先跑完整 token 分析；現在依狀態只做必要判斷，並在 SQL 先預篩候選列。
這裡以原本的實作當參考答案，確認兩者在各種文字、狀態、方向、類別下一致。
"""

from __future__ import annotations

import itertools
import random

import pytest

from translation_tool.translation_db import KIND_LANG, ScanItem, TranslationDB
from translation_tool.translation_db.models import EntryFilter, QualityFilter
from translation_tool.translation_db.quality import (
    format_tokens,
    matches_quality,
    quality_state,
    token_category,
)

TEXTS = [
    "",
    "Plain text",
    "純中文",
    " 前置空白",
    "後置空白\n",
    "\t",
    "　全形空白　",
    " 不換行空白",
    "Use %s",
    "使用 %s",
    "%1$s and %2$d",
    "%%",
    "§aGreen §rReset",
    "§z not a code",
    "{0} and {1}",
    "{}",
    "line1\nline2",
    "line1\\nline2",
    "a\r\nb",
    "$(item)Hammer$()",
    "$(t:tooltip)Hover$(/t)",
    "$(unterminated macro",
    "$(",
    "100%",
    "50% off",
    "\\",
    "back\\slash",
    "$ dollar",
    "{ brace",
    "§",
]

CRITERIA = [
    QualityFilter(status=status, direction=direction, token_category=category)
    for status in (
        "all",
        "mismatch",
        "consistent",
        "unknown_source",
        "missing_translation",
        "whitespace",
    )
    for direction in ("all", "missing", "extra")
    for category in ("all", "placeholder", "minecraft", "patchouli", "newline")
]


def _reference_matches_quality(source: str, translated: str, criteria) -> bool:
    """加速前的原始實作（每列先跑 quality_state）。"""
    if not criteria.active:
        return True
    _, _, whitespace = quality_state(source, translated)
    status = criteria.status
    if status == "all":
        status = "mismatch"
    if status == "unknown_source":
        return not source
    if status == "missing_translation":
        return bool(source) and not translated
    if status == "whitespace":
        return bool(translated) and bool(whitespace)
    if status == "consistent":
        return bool(source and translated) and format_tokens(source) == format_tokens(
            translated
        )
    if status != "mismatch" or not source or not translated:
        return False
    wanted, actual = format_tokens(source), format_tokens(translated)
    missing, extra = wanted - actual, actual - wanted

    def selected(counter) -> bool:
        return any(
            count > 0
            and (
                criteria.token_category == "all"
                or token_category(token) == criteria.token_category
            )
            for token, count in counter.items()
        )

    return (criteria.direction in {"all", "missing"} and selected(missing)) or (
        criteria.direction in {"all", "extra"} and selected(extra)
    )


def test_fast_path_matches_reference_for_every_text_pair_and_criteria():
    for source, translated in itertools.product(TEXTS, repeat=2):
        for criteria in CRITERIA:
            assert matches_quality(source, translated, criteria) == (
                _reference_matches_quality(source, translated, criteria)
            ), (source, translated, criteria)


def test_fast_path_matches_reference_on_random_text():
    rng = random.Random(20261011)
    pieces = [*TEXTS, "a", "b", " ", "中", "文", "%s", "§a", "{0}", "\n", "$(x)"]
    for _ in range(1500):
        source = "".join(rng.choices(pieces, k=rng.randint(0, 5)))
        translated = "".join(rng.choices(pieces, k=rng.randint(0, 5)))
        criteria = rng.choice(CRITERIA)
        assert matches_quality(source, translated, criteria) == (
            _reference_matches_quality(source, translated, criteria)
        ), (source, translated, criteria)


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "quality.db")
    yield database
    database.close()


def _filter_results(db, quality: QualityFilter, **kwargs):
    criteria = EntryFilter(version="1.21.1", quality=quality, **kwargs)
    return db.list_entries(criteria=criteria, limit=1000)


@pytest.mark.parametrize(
    "quality", CRITERIA, ids=lambda q: f"{q.status}-{q.direction}-{q.token_category}"
)
def test_sql_prefilter_does_not_change_list_entries_results(db, quality):
    """SQL 預篩只能縮小候選，不能漏掉 Python 判斷會通過的列。"""
    items = []
    for index, (source, translated) in enumerate(itertools.product(TEXTS, repeat=2)):
        items.append(
            ScanItem(KIND_LANG, "quality", f"item.{index:04d}", source, translated, "")
        )
    db.ingest("1.21.1", items)

    rows, total = _filter_results(db, quality)
    expected = sorted(
        (
            row
            for row in db.list_entries("1.21.1", limit=10_000)[0]
            if _reference_matches_quality(row.en_us or "", row.zh_tw or "", quality)
        ),
        key=lambda row: (row.mod_id, row.kind, row.key, row.id),
    )

    assert total == len(expected)
    assert [row.id for row in rows] == [row.id for row in expected]


def test_batch_sibling_rows_matches_row_value_in_reference(db):
    """跨版本候選查詢改成 OR 形式後，結果與排序要和原本的 row-value IN 相同。"""
    for version in ("1.19.2", "1.20.1", "1.21.1"):
        db.ingest(
            version,
            [
                ScanItem(
                    KIND_LANG, "m", f"k{n}", ("Sword", "Axe", "Bow")[n % 3], "譯", ""
                )
                for n in range(250)
            ],
        )
    roots = db._query_entry_rows(
        EntryFilter(version="1.21.1"), batch_replace_projection=True
    )
    assert len(roots) > 100  # 跨過 100 筆一批的邊界

    actual = db._batch_sibling_rows(roots)

    keys = list(dict.fromkeys((r[1], r[3], r[4], r[5]) for r in roots))
    expected: dict = {key: [] for key in keys}
    for offset in range(0, len(keys), 100):
        chunk = keys[offset : offset + 100]
        marks = ",".join("(?,?,?,?)" for _ in chunk)
        sql = (
            f"{db._batch_replace_select_sql()} {db._query_source()} "
            f"WHERE (e.kind,e.mod_id,e.key,e.en_us) IN ({marks}) "
            "AND e.en_us<>'' ORDER BY e.kind,e.mod_id,e.key,e.en_us,e.mc_version"
        )
        for row in db._q(sql, [v for key in chunk for v in key]):
            expected[(row[1], row[3], row[4], row[5])].append(row)
    assert actual == expected
    assert all(len(rows) == 3 for rows in actual.values())


@pytest.mark.parametrize(
    "quality",
    CRITERIA[::7],
    ids=lambda q: f"{q.status}-{q.direction}-{q.token_category}",
)
def test_batch_projection_with_quality_matches_full_scan(db, quality):
    """批次取代的品質範圍（窄查詢＋取 id）要與讀回全部列再篩選的舊流程相同。"""
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "quality", f"item.{i:04d}", s, t, "")
            for i, (s, t) in enumerate(itertools.product(TEXTS, repeat=2))
        ],
    )
    criteria = EntryFilter(version="1.21.1", quality=quality)
    actual = db._query_entry_rows(criteria, batch_replace_projection=True)

    everything = db._query_entry_rows(
        EntryFilter(version="1.21.1"), batch_replace_projection=True
    )
    expected = db._sort_filtered_rows(
        [r for r in everything if matches_quality(r[5] or "", r[6] or "", quality)],
        criteria.sort_by,
    )
    assert actual == expected


def test_quality_page_turns_reuse_scan_and_invalidate_after_writes(db):
    """品質篩選翻頁沿用上次的符合清單；資料一變就重新掃描。

    方向或類別不是「全部」的 token 不一致仍要逐列判斷（品質旗標只縮小候選）。
    """
    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "m", f"k{i:03d}", "Use %s", "使用", "")
            for i in range(30)
        ],
    )
    criteria = EntryFilter(
        version="1.21.1", quality=QualityFilter(status="mismatch", direction="missing")
    )
    scans = []
    original = db._scan_quality_candidates
    db._scan_quality_candidates = lambda *a, **k: (scans.append(1), original(*a, **k))[
        1
    ]

    page1, total = db.list_entries(criteria=criteria, limit=10, offset=0)
    page2, _ = db.list_entries(criteria=criteria, limit=10, offset=10)
    assert total == 30 and len(scans) == 1
    assert [r.key for r in page1 + page2] == [f"k{i:03d}" for i in range(20)]

    db.save_manual(page1[0].id, "使用 %s", actor="test", propagate=False)
    _, total_after = db.list_entries(criteria=criteria, limit=10, offset=0)
    assert total_after == 29 and len(scans) == 2


def test_repairable_entries_sql_prefilter_matches_full_python_scan(db):
    """修復候選的 SQL 預篩與單次 token 計數，結果要與逐列完整分析相同。"""
    from translation_tool.translation_db.quality import repair_input_issues

    db.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "quality", f"item.{i:04d}", s, t, "")
            for i, (s, t) in enumerate(itertools.product(TEXTS, repeat=2))
        ],
    )
    rows = db._q(
        "SELECT e.id, e.en_us, t.zh_tw FROM entry e JOIN translation t "
        "ON t.entry_id=e.id WHERE e.mc_version='1.21.1' "
        "AND e.en_us<>'' AND t.zh_tw<>''"
    )
    expected = {}
    for entry_id, source, translated in rows:
        eligible, _issues, mixed = repair_input_issues(source, translated)
        newline_mismatch = (
            format_tokens(source)["\n"] != format_tokens(translated)["\n"]
        )
        if eligible:
            expected[entry_id] = (newline_mismatch, mixed)

    actual = {
        entry.entry_id: (newline_mismatch, mixed)
        for entry, newline_mismatch, mixed in db.iter_repairable_translation_entries(
            "1.21.1"
        )
    }
    assert expected and actual == expected
