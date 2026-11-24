"""條目篩選「含 {單字}」：原文或譯文有大括號包字母詞彙的條目。"""

from __future__ import annotations

import pytest

from app.views.moddb import advanced_filters
from translation_tool.translation_db import EntryFilter, ScanItem, TranslationDB
from translation_tool.translation_db.schema import KIND_LANG


@pytest.fixture
def db(tmp_path):
    database = TranslationDB(tmp_path / "t.db")
    database.ingest(
        "1.21.1",
        [
            ScanItem(KIND_LANG, "m", "a.source_word", "Needs {growth} here"),
            ScanItem(KIND_LANG, "m", "b.translated_word", "Plain", "已被改成 {成長}"),
            ScanItem(KIND_LANG, "m", "c.pt_word", "Precisa de {crescer}"),
            ScanItem(KIND_LANG, "m", "d.number", "Count {0}", "數量 {0}"),
            ScanItem(KIND_LANG, "m", "e.marker", "Line {@pagebreak} end"),
            ScanItem(KIND_LANG, "m", "f.empty", "Empty {} braces"),
            ScanItem(KIND_LANG, "m", "g.plain", "Nothing special"),
        ],
    )
    yield database
    database.close()


def _keys(db, **kwargs):
    rows, total = db.list_entries(
        criteria=EntryFilter(version="1.21.1", **kwargs), limit=50
    )
    assert total == len(rows)
    return sorted(row.key for row in rows)


def test_brace_word_filter_lists_entries_with_braced_words(db):
    assert _keys(db, has_brace_word=True) == [
        "a.source_word",
        "b.translated_word",
        "c.pt_word",
    ]


def test_brace_word_filter_is_off_by_default(db):
    assert len(_keys(db)) == 7


def test_brace_word_filter_combines_with_other_conditions(db):
    assert _keys(db, has_brace_word=True, query="crescer") == ["c.pt_word"]
    assert _keys(db, has_brace_word=True, state="none") == [
        "a.source_word",
        "c.pt_word",
    ]


def test_advanced_filter_exposes_the_option_and_counts_it():
    filters = advanced_filters.AdvancedFilters(lambda: None)
    assert filters.has_brace_word is False
    assert filters.set_summary() == 0
    filters.brace_word.value = "has"
    assert filters.has_brace_word is True
    assert filters.set_summary() == 1
    filters.reset()
    assert filters.has_brace_word is False
