"""模組 × 來源分面：與逐列查詢結果一致、不掃描條目、依資料世代快取，以及舊摘要表的升級。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from translation_tool.translation_db import (
    KIND_LANG,
    KIND_PATCHOULI,
    EntryFilter,
    QualityFilter,
    ScanItem,
    TranslationDB,
    WriteBackItem,
    option_facets,
    stat_summary,
)
from translation_tool.translation_db.schema import SRC_AI, SRC_JAR_CN, SRC_MANUAL

VERSION = "1.20.1"


def _item(mod, key, en, tw="", cn="", kind=KIND_LANG):
    return ScanItem(kind, mod, key, en, tw, cn, None)


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "facets.db")
    for version in (VERSION, "1.21.1"):
        d.ingest(
            version,
            [
                _item("alpha", "a.one", "Iron Block", "鐵方塊"),
                _item("alpha", "a.two", "Gold Block"),
                _item("alpha", "a.three", "Copper Ingot", cn="铜锭"),
                _item("beta", "b.one", "Stone Block", "石頭方塊"),
                _item("beta", "b.two", "Wood", "木頭"),
                _item("beta", "b.book", "Guide Page", kind=KIND_PATCHOULI),
                _item("gamma", "g.one", "Block Of Glass"),
            ],
            convert=lambda text: text.replace("铜锭", "銅錠"),
        )
    d.write_back(
        VERSION, [WriteBackItem(KIND_LANG, "alpha", "a.two", "Gold Block", "金方塊")]
    )
    return d


def _old_options(db, criteria):
    """分面之前的作法：所有符合列的不同（模組, 來源）配對。"""
    base = replace(criteria, mod_id=None, source=None)
    condition, params = db._filter_sql(base)
    source = db._candidate_source(f"{condition} f.source")
    pairs = db._q(
        f"SELECT DISTINCT e.mod_id, f.source {source} WHERE {condition}", params
    )
    mods = sorted(
        {str(m) for m, s in pairs if criteria.source is None or s == criteria.source}
    )
    sources = tuple(
        sorted(
            {
                int(s)
                for m, s in pairs
                if s is not None and (criteria.mod_id is None or m == criteria.mod_id)
            }
        )
    )
    return mods, sources


def _old_total(db, criteria):
    condition, params = db._filter_sql(criteria)
    return db._one(db._count_sql(criteria, condition), params)[0]


FILTERS = [
    {},
    {"state": "none"},
    {"state": "diff"},
    {"state": "manual"},
    {"state": "ok"},
    {"state": "same"},
    {"kind": KIND_LANG},
    {"kind": KIND_PATCHOULI},
    {"review_status": "unreviewed"},
    {"review_status": "reviewed"},
    {"review_status": "legacy_unknown"},
    {"kind": KIND_LANG, "review_status": "unreviewed"},
    {"state": "none", "kind": KIND_LANG},
    {"state": "none", "review_status": "unreviewed"},
    {"query": "block"},
    {"query": "方塊", "query_field": "translation"},
    {"query": "b.", "query_field": "key"},
]
SELECTIONS = [
    {},
    {"mod_id": "alpha"},
    {"mod_id": "missing"},
    {"source": SRC_AI},
    {"source": SRC_JAR_CN},
    {"source": SRC_MANUAL},
    {"source": 999},
    {"mod_id": "beta", "source": SRC_AI},
]


def assert_facets_match_rows(db):
    assert db.verify_stat_summary() == []
    for kwargs in FILTERS:
        for selection in SELECTIONS:
            for version in (VERSION, "1.21.1"):
                criteria = EntryFilter(version=version, **kwargs, **selection)
                assert db.filter_option_values(criteria) == _old_options(db, criteria)
                rows, total = db.list_entries(criteria=criteria, limit=100)
                assert total == _old_total(db, criteria) == len(rows)


def test_facets_match_row_queries_after_every_write_path(db):
    assert_facets_match_rows(db)
    entry = db.list_entries(VERSION, mod_id="beta", query="Wood")[0][0]
    db.save_manual(entry.id, "木材")
    assert_facets_match_rows(db)
    db.review_manual(
        entry.id,
        expected_zh_tw="木材",
        expected_source=SRC_MANUAL,
        expected_review_status="unreviewed",
        expected_checker="",
    )
    assert_facets_match_rows(db)
    db.revert(db._one("SELECT MAX(id) FROM history")[0])
    assert_facets_match_rows(db)
    db.ingest("1.21.1", [_item("delta", "d.one", "Delta Block", "三角洲方塊")])
    assert_facets_match_rows(db)
    with db._tx() as conn:
        conn.execute("DELETE FROM entry WHERE mod_id='gamma'")
    assert_facets_match_rows(db)


def test_quality_filters_keep_row_scan_options(db):
    criteria = EntryFilter(version=VERSION, quality=QualityFilter(status="consistent"))
    assert db._option_facets(criteria) is None
    mods, sources = db.filter_option_values(criteria)
    assert mods and sources


def _selects(db):
    statements: list[str] = []
    db._conn.set_trace_callback(
        lambda sql: (
            statements.append(sql) if sql.lstrip().startswith("SELECT") else None
        )
    )
    return statements


def test_summary_facets_never_read_entries(db):
    criteria = EntryFilter(version=VERSION, review_status="unreviewed", kind=KIND_LANG)
    statements = _selects(db)
    db.filter_option_values(criteria)
    db.list_entries(criteria=criteria, limit=10)
    facet_sql = [s for s in statements if "FROM stat_src" in s and "mod_id" in s]
    assert facet_sql
    # 總數來自分面：不再有對條目表的 COUNT(*)
    assert not [
        s for s in statements if s.lstrip().startswith("SELECT COUNT(*) FROM entry")
    ]


def test_scan_facets_are_cached_and_serve_totals_for_any_selection(db):
    base = EntryFilter(version=VERSION, query="block")
    statements = _selects(db)
    db.filter_option_values(base)
    scans = [s for s in statements if "GROUP BY e.mod_id" in s]
    assert len(scans) == 1
    statements.clear()
    totals = []
    for selection in SELECTIONS:
        criteria = replace(base, **selection)
        db.filter_option_values(criteria)
        totals.append(db.list_entries(criteria=criteria, limit=1)[1])
    # 換模組、換來源、翻頁都用同一份分面：不再掃描、也不另外計數
    assert not [s for s in statements if "GROUP BY e.mod_id" in s]
    assert not [s for s in statements if s.lstrip().startswith("SELECT COUNT(*)")]
    assert totals == [_old_total(db, replace(base, **s)) for s in SELECTIONS]
    # 寫入後資料世代改變，分面重算
    db.write_back(
        VERSION, [WriteBackItem(KIND_LANG, "alpha", "a.four", "Block Four", "譯")]
    )
    statements.clear()
    db.filter_option_values(base)
    assert len([s for s in statements if "GROUP BY e.mod_id" in s]) == 1


def test_list_total_without_cached_facets_still_counts_rows(db):
    criteria = EntryFilter(version=VERSION, query="block", mod_id="alpha")
    statements = _selects(db)
    rows, total = db.list_entries(criteria=criteria, limit=10)
    assert total == _old_total(db, criteria) == len(rows)
    # 沒有先算分面時（例如只指定模組的服務層查詢）不為了總數掃描整個版本
    assert not [s for s in statements if "GROUP BY e.mod_id" in s]


def test_option_facet_helpers():
    facets = option_facets.build(
        [("a", 3, 2, 1, 1), ("b", 2, 2, 0, 2), ("c", 1, 0, 0, 0)], [0, 6]
    )
    assert facets["complete"]
    assert option_facets.total(facets, None, None) == 6
    assert option_facets.total(facets, "a", 6) == 1
    assert option_facets.total(facets, "b", 0) == 0
    assert option_facets.total(facets, None, 5) == 0
    assert option_facets.options(facets, None, None) == (["a", "b", "c"], (0, 6))
    assert option_facets.options(facets, "b", None) == (["a", "b", "c"], (6,))
    assert option_facets.options(facets, None, 0) == (["a"], (0, 6))
    unknown_source = option_facets.build([("a", 3, 3, 1, 1)], [0, 6])
    assert not unknown_source["complete"]


def test_versions_mods_and_source_codes_match_row_queries(db):
    assert db._stat_summary_ready()
    assert db._versions_uncached() == [
        r[0]
        for r in db._q(
            "SELECT mc_version FROM entry GROUP BY mc_version ORDER BY COUNT(*) DESC"
        )
    ]
    for version in (VERSION, "1.21.1", "9.9.9"):
        assert db._mods_uncached(version) == [
            r[0]
            for r in db._q(
                "SELECT DISTINCT mod_id FROM entry WHERE mc_version=? ORDER BY mod_id",
                (version,),
            )
        ]
        expected = [
            [r[0]]
            for r in db._q(
                "SELECT DISTINCT f.source FROM effective f JOIN entry e "
                "ON e.id=f.entry_id WHERE e.mc_version=? ORDER BY f.source",
                (version,),
            )
        ]
        assert db._effective_source_codes_uncached(version) == expected
    assert db._effective_source_codes_uncached(None) == [
        list(r) for r in db._q("SELECT DISTINCT source FROM effective ORDER BY source")
    ]


def test_mods_lookup_skips_by_index_instead_of_scanning_entries(db):
    plan = " ".join(
        row[3]
        for row in db._conn.execute(
            "EXPLAIN QUERY PLAN WITH RECURSIVE m(mod_id) AS ("
            "SELECT MIN(mod_id) FROM entry WHERE mc_version=? "
            "UNION ALL SELECT (SELECT MIN(mod_id) FROM entry "
            "WHERE mc_version=? AND mod_id > m.mod_id) FROM m "
            "WHERE mod_id IS NOT NULL) SELECT mod_id FROM m WHERE mod_id IS NOT NULL",
            (VERSION, VERSION),
        )
    )
    assert "SCAN entry" not in plan
    assert "TEMP B-TREE" not in plan


def test_old_summary_table_is_rebuilt_with_module_and_kind_columns(tmp_path):
    path = tmp_path / "old.db"
    first = TranslationDB(path)
    first.ingest(VERSION, [_item("alpha", "a", "Iron", "鐵")])
    first.close()
    with sqlite3.connect(path) as conn:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'stat_%'"
        ).fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DROP TABLE stat_src")
        conn.execute(
            "CREATE TABLE stat_src (mc_version TEXT NOT NULL, source INTEGER NOT NULL, "
            "review TEXT NOT NULL, cnt INTEGER NOT NULL, "
            "PRIMARY KEY (mc_version, source, review)) WITHOUT ROWID"
        )
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='stats_schema_version'",
        )
    readonly = TranslationDB(path, readonly=True)
    # 舊版摘要不可直接讀：退回逐列查詢，結果仍正確
    assert not readonly._stat_summary_ready()
    assert readonly.effective_source_codes(VERSION) == (1,)
    readonly.close()
    upgraded = TranslationDB(path)
    assert upgraded._stat_summary_ready()
    assert stat_summary.is_installed(upgraded._conn)
    assert upgraded.verify_stat_summary() == []
    columns = [r[1] for r in upgraded._q("PRAGMA table_info(stat_src)")]
    assert columns[:3] == ["mc_version", "mod_id", "kind"]
    upgraded.close()


def _expected_scope(pairs):
    modules: list[str] = []
    by_mod: dict[str, set[int]] = {}
    for mod_id, source in pairs:
        if mod_id not in by_mod:
            modules.append(str(mod_id))
            by_mod[mod_id] = set()
        if source is not None:
            by_mod[mod_id].add(int(source))
    everything = set().union(*by_mod.values()) if by_mod else set()
    return (
        tuple(modules),
        {
            None: tuple(sorted(everything)),
            **{mod: tuple(sorted(sources)) for mod, sources in by_mod.items()},
        },
    )


def test_smart_review_scope_options_match_distinct_pairs(db):
    from translation_tool.translation_db.smart_review_filters import (
        _build_smart_review_selection,
    )

    entry = db.list_entries(VERSION, mod_id="beta", query="Wood")[0][0]
    db.save_manual(entry.id, "木材")
    for kind in (None, KIND_LANG, KIND_PATCHOULI):
        for review in ("unreviewed", "reviewed", "legacy_unknown"):
            selection = _build_smart_review_selection(
                version=VERSION,
                mod_id=None,
                kind=kind,
                effective_source=None,
                review_status=review,
                query=None,
                require_reviewed_reference=False,
            )
            old = db._q(
                "SELECT DISTINCT e.mod_id, f.source FROM entry e "
                "JOIN effective f ON f.entry_id=e.id "
                f"WHERE {selection.where_sql} ORDER BY e.mod_id, f.source",
                selection.params,
            )
            assert db.smart_review_scope_options(
                version=VERSION, kind=kind, review_status=review
            ) == _expected_scope(old)


def test_bulk_review_scope_options_match_candidates_and_are_cached(db):
    for quality_pass_only in (True, False):
        modules, sources = db.bulk_review_scope_options(
            VERSION, quality_pass_only=quality_pass_only
        )
        candidates = db._bulk_review_candidates(VERSION, None, quality_pass_only)
        expected = _expected_scope(sorted((c.mod_id, c.source) for c in candidates))
        assert (modules, sources) == expected
    statements = _selects(db)
    db.bulk_review_scope_options(VERSION)
    assert not [s for s in statements if "GROUP BY e.mod_id" in s]


@pytest.mark.parametrize(
    "quality",
    [
        QualityFilter(status="missing_translation"),
        QualityFilter(status="consistent"),
        QualityFilter(status="mismatch", direction="missing"),
    ],
)
def test_quality_filter_options_are_cached_per_data_generation(db, quality):
    criteria = EntryFilter(version=VERSION, quality=quality)
    first = db.filter_option_values(criteria)
    statements = _selects(db)
    assert db.filter_option_values(replace(criteria, mod_id="alpha")) == (
        db.filter_option_values(replace(criteria, mod_id="alpha"))
    )
    assert db.filter_option_values(criteria) == first
    assert not [s for s in statements if "FROM entry e" in s]
    db.write_back(VERSION, [WriteBackItem(KIND_LANG, "alpha", "a.new", "New", "新")])
    statements.clear()
    db.filter_option_values(criteria)
    assert [s for s in statements if "FROM entry e" in s]
