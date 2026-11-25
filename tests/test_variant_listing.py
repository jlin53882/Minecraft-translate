"""「其他原文」清單的資料層：分頁、篩選、生效譯文與沒有臨時排序。"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import ScanItem, TranslationDB, WriteBackItem
from translation_tool.translation_db.schema import KIND_LANG, SRC_AI, SRC_MANUAL
from translation_tool.translation_db.variant_listing import (
    STATUS_ALL,
    STATUS_TRANSLATED,
    STATUS_UNTRANSLATED,
)

VERSION = "1.21.1"


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def _item(mod, key, en, tw=""):
    return ScanItem(KIND_LANG, mod, key, en, tw, "")


def _seed(db):
    """foo.a：主原文有譯文，兩份其他原文（一份有 AI 譯文）；bar.b：一份沒有譯文的其他原文。"""
    db.ingest(
        VERSION,
        [_item("foo", "item.foo.a", "Show Background", "顯示背景")],
    )
    db.ingest(VERSION, [_item("bar", "item.bar.b", "Old Bar")])
    db.ingest(VERSION, [_item("foo", "item.foo.a", "Background Style")])
    db.ingest(VERSION, [_item("foo", "item.foo.a", "Draw Layer")])
    db.ingest(VERSION, [_item("bar", "item.bar.b", "New Bar Text")])
    db.write_back(
        VERSION,
        [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Background Style", "背景樣式")],
    )


def test_listing_returns_newest_first_with_main_text_and_effective_translation(db):
    _seed(db)
    rows, total = db.list_variants(VERSION)
    assert total == 3
    assert [r.en_us for r in rows] == ["New Bar Text", "Draw Layer", "Background Style"]
    by_text = {r.en_us: r for r in rows}
    style = by_text["Background Style"]
    assert (style.main_en, style.main_zh) == ("Show Background", "顯示背景")
    assert (style.zh_tw, style.source) == ("背景樣式", SRC_AI)
    assert by_text["Draw Layer"].zh_tw == "" and by_text["Draw Layer"].source is None
    assert by_text["New Bar Text"].main_en == "Old Bar"
    assert by_text["New Bar Text"].main_zh == ""  # 主條目也沒有譯文


def test_status_filter_matches_the_untranslated_definition(db):
    _seed(db)
    untranslated, n_none = db.list_variants(VERSION, status=STATUS_UNTRANSLATED)
    translated, n_has = db.list_variants(VERSION, status=STATUS_TRANSLATED)
    _everything, n_all = db.list_variants(VERSION, status=STATUS_ALL)
    assert sorted(r.en_us for r in untranslated) == ["Draw Layer", "New Bar Text"]
    assert [r.en_us for r in translated] == ["Background Style"]
    assert (n_none, n_has, n_all) == (2, 1, 3)
    # 與批次機翻使用的「沒有譯文」同一個定義
    assert n_none == db.count_untranslated_variants(VERSION)


def test_pagination_total_is_independent_of_the_page(db):
    _seed(db)
    first, total = db.list_variants(VERSION, limit=2, offset=0)
    second, total2 = db.list_variants(VERSION, limit=2, offset=2)
    assert total == total2 == 3
    assert [r.en_us for r in first] == ["New Bar Text", "Draw Layer"]
    assert [r.en_us for r in second] == ["Background Style"]


def test_mod_and_search_filters(db):
    _seed(db)
    rows, total = db.list_variants(VERSION, mod_id="bar")
    assert [r.en_us for r in rows] == ["New Bar Text"] and total == 1
    rows, _ = db.list_variants(VERSION, search="draw")  # 不分大小寫，比對其他原文
    assert [r.en_us for r in rows] == ["Draw Layer"]
    rows, _ = db.list_variants(VERSION, search="show back")  # 比對主原文
    assert len(rows) == 2 and {r.main_en for r in rows} == {"Show Background"}
    rows, _ = db.list_variants(VERSION, search="item.bar")  # 比對鍵值
    assert [r.key for r in rows] == ["item.bar.b"]
    rows, total = db.list_variants(VERSION, search="100%_")  # LIKE 萬用字元要跳脫
    assert rows == [] and total == 0


def test_other_versions_are_not_listed_and_versions_helpers(db):
    _seed(db)
    db.ingest("1.20.1", [_item("foo", "item.foo.z", "Only Main")])
    assert db.list_variants("1.20.1") == ([], 0)
    assert db.variant_versions() == [VERSION]
    assert db.variant_mods(VERSION) == ["bar", "foo"]
    assert db.variant_mods("1.20.1") == []


def test_effective_translation_follows_source_priority(db):
    _seed(db)
    db.write_back(
        VERSION,
        [WriteBackItem(KIND_LANG, "foo", "item.foo.a", "Draw Layer", "繪製層")],
    )
    entry_id = db._q("SELECT id FROM entry WHERE key='item.foo.a'")[0][0]
    from translation_tool.translation_db import variants

    with db._lock:
        vid = variants.variant_id(db._conn, entry_id, "Draw Layer")
        variants.add_variant_translation(db._conn, vid, SRC_MANUAL, "人工圖層")
        db._conn.commit()
    rows, _ = db.list_variants(VERSION, search="Draw")
    assert rows[0].zh_tw == "人工圖層" and rows[0].source == SRC_MANUAL


def test_old_schema_without_variant_tables_lists_nothing(tmp_path):
    path = tmp_path / "old.db"
    old = TranslationDB(path)
    old._conn.executescript("DROP TABLE variant_translation; DROP TABLE entry_variant;")
    old._conn.commit()
    old.close()
    ro = TranslationDB(path, readonly=True)
    try:
        assert ro.list_variants(VERSION) == ([], 0)
        assert ro.variant_versions() == [] and ro.variant_mods(VERSION) == []
    finally:
        ro.close()
