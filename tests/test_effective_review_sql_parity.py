"""生效審核狀態 SQL 的單一定義：各查詢對同一批資料必須給出相同結果。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import TranslationDB, stat_summary
from translation_tool.translation_db import smart_review_filters as srf
from translation_tool.translation_db.schema import SRC_MANUAL
from translation_tool.translation_db.sql_helpers import effective_review_sql

# (來源, 儲存的 review_status) → 預期的生效審核狀態
CASES = [
    (0, None, "unreviewed"),  # 非人工來源沒有狀態 = 未審核
    (0, "unreviewed", "unreviewed"),
    (0, "reviewed", "reviewed"),
    (0, "weird", "unreviewed"),  # 未知舊值：依來源歸類，不原樣外洩
    (0, "", "unreviewed"),
    (SRC_MANUAL, None, "legacy_unknown"),  # 人工來源沒有狀態 = 舊資料未知
    (SRC_MANUAL, "unreviewed", "unreviewed"),
    (SRC_MANUAL, "reviewed", "reviewed"),
    (SRC_MANUAL, "legacy_unknown", "legacy_unknown"),
    (SRC_MANUAL, "weird", "legacy_unknown"),
    (1, None, "unreviewed"),
]


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "parity.db")
    conn = database._conn
    for i, (source, status, _expected) in enumerate(CASES, 1):
        conn.execute(
            "INSERT INTO entry (id, kind, mc_version, mod_id, key, en_us) "
            "VALUES (?, 'lang', '1', 'm', ?, 'e')",
            (i, f"k{i}"),
        )
        conn.execute("DELETE FROM effective WHERE entry_id=?", (i,))
        conn.execute(
            "INSERT INTO effective (entry_id, zh_tw, source, checker, review_status) "
            "VALUES (?, 't', ?, '', ?)",
            (i, source, status),
        )
    # 沒有任何生效譯文的條目
    conn.execute(
        "INSERT INTO entry (id, kind, mc_version, mod_id, key, en_us) "
        "VALUES (99, 'lang', '1', 'm', 'none', 'e')"
    )
    conn.commit()
    yield database
    database.close()


def _per_row(conn, expression: str) -> dict[int, str | None]:
    return dict(
        conn.execute(
            f"SELECT e.id, {expression} FROM entry e "
            "LEFT JOIN effective f ON f.entry_id = e.id"
        ).fetchall()
    )


def test_all_effective_review_definitions_agree_on_every_case(db):
    conn = db._conn
    expected = {i: exp for i, (_s, _r, exp) in enumerate(CASES, 1)}
    expected[99] = None

    assert _per_row(conn, db._effective_review_sql) == expected
    assert _per_row(conn, srf._effective_review_sql("f")) == expected
    assert _per_row(conn, effective_review_sql("f", True)) == expected

    # 統計觸發器版本只用在有生效列的資料：同樣的歸類
    summary = _per_row(conn, stat_summary._review("f"))
    assert {k: v for k, v in summary.items() if k != 99} == {
        k: v for k, v in expected.items() if k != 99
    }


def test_full_kpi_and_incremental_summary_agree_with_row_definition(db):
    full = db._effective_source_stats_by_version_full()
    summary = db._effective_source_stats_by_version_rows()
    assert full == summary
    per_row = _per_row(db._conn, db._effective_review_sql)
    counted = {(src, status): n for _v, src, status, n in full if src is not None}
    for (src, status), n in counted.items():
        matching = [
            i
            for i, (s, _raw, _exp) in enumerate(CASES, 1)
            if s == src and per_row[i] == status
        ]
        assert len(matching) == n


def test_legacy_schema_without_review_column_marks_manual_as_unknown(tmp_path):
    from tests.test_translation_db_core import _make_v1_db

    path = tmp_path / "legacy.db"
    _make_v1_db(path)
    readonly = TranslationDB(path, readonly=True)
    assert readonly.has_review_state is False
    expression = readonly._effective_review_sql
    assert expression == effective_review_sql("f", False)
    rows = readonly._conn.execute(
        f"SELECT f.source, {expression} FROM effective f"
    ).fetchall()
    assert rows and all(
        status == ("legacy_unknown" if source == SRC_MANUAL else None)
        for source, status in rows
    )
    readonly.close()


def test_builtin_and_schema_source_names_are_identical():
    """``schema.SOURCE_NAMES`` 與 ``BUILTIN_SOURCES`` 是兩份名稱表，必須逐項相同。"""
    from translation_tool.translation_db import schema, source_catalog

    by_code = {s.code: s.display_name for s in source_catalog.BUILTIN_SOURCES}
    assert by_code == schema.SOURCE_NAMES == schema.BUILTIN_SOURCE_NAMES
    labels = {
        code: source_catalog.DEFAULT_SOURCE_CATALOG.label(code)
        for code in schema.SOURCE_NAMES
    }
    assert labels == schema.SOURCE_NAMES


def test_custom_source_named_like_a_builtin_is_shadowed_consistently():
    from translation_tool.translation_db.schema import SOURCE_NAMES, SRC_MANUAL
    from translation_tool.translation_db.source_catalog import SourceCatalog

    name = SOURCE_NAMES[SRC_MANUAL]
    catalog = SourceCatalog.from_registry({name: 100})
    assert catalog.resolve(name) == 100  # 同名純文字依資料庫自訂來源解析
    assert catalog.resolve("builtin:manual") == SRC_MANUAL  # 穩定 token 仍可選內建
    assert catalog.label(100) == f"{name}（自訂 #100）"
    assert catalog.label(SRC_MANUAL) == name
    assert 100 not in catalog.selectable_custom_codes
    assert catalog.canonical_code(100) == SRC_MANUAL


def test_stat_summary_trigger_sql_text_is_stable():
    """統計觸發器的 SQL 文字不可變：變了會讓所有既有資料庫在開啟時重裝觸發器。"""
    assert stat_summary._review("x") == (
        "CASE WHEN x.review_status IN ('unreviewed','reviewed','legacy_unknown') "
        "THEN x.review_status WHEN x.source=6 "
        "THEN 'legacy_unknown' ELSE 'unreviewed' END"
    )
