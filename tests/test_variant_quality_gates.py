"""其他原文（變體）的譯文不能繞過資料庫譯文共用的品質把關。

流水線讀到變體譯文的路徑（語系合併、目錄翻譯的資料庫命中）與一般資料庫命中用同一套
特殊字元檢查；變體譯文的特殊字元與它的原文不一致時，必須和一般命中一樣被拒絕。
"""

from __future__ import annotations

import pytest

from translation_tool.core.lang_merge_db import MergeDbFill
from translation_tool.core.lm_translator_shared_cache import cache_quality_issues
from translation_tool.translation_db import (
    ScanItem,
    TranslationDB,
    TranslationResolver,
    WriteBackItem,
    split_items_by_db,
)
from translation_tool.translation_db.schema import KIND_LANG

VERSION = "1.21.1"
KEY = "item.foo.a"


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    d.ingest(
        VERSION, [ScanItem(KIND_LANG, "foo", KEY, "Old Value: %s", "舊值：%s", "")]
    )
    yield d
    d.close()


def _variant(db, en, zh):
    db.write_back(VERSION, [WriteBackItem(KIND_LANG, "foo", KEY, en, zh)])


def _lang_item(tmp_path, en):
    path = tmp_path / "assets" / "foo" / "lang" / "en_us.json"
    return {
        "file": str(path),
        "path": KEY,
        "source_text": en,
        "text": en,
        "cache_type": "lang",
    }


def test_merge_fill_rejects_a_variant_translation_with_mismatched_tokens(db):
    _variant(db, "New Value: %s (%d)", "新值：%s")  # 少了 %d
    fill = MergeDbFill(db, VERSION, cross_version=True)
    final, pending, hits = fill.fill("foo", {}, {KEY: "New Value: %s (%d)"})
    assert hits == 0 and final == {} and pending == {KEY: "New Value: %s (%d)"}
    assert fill.rejected_quality == 1


def test_merge_fill_accepts_a_variant_translation_with_matching_tokens(db):
    _variant(db, "New Value: %s (%d)", "新值：%s（%d）")
    fill = MergeDbFill(db, VERSION, cross_version=True)
    final, _pending, hits = fill.fill("foo", {}, {KEY: "New Value: %s (%d)"})
    assert hits == 1 and final == {KEY: "新值：%s（%d）"}


def test_directory_translation_hit_gate_rejects_mismatched_variant_translation(
    db, tmp_path
):
    _variant(db, "New Value: %s (%d)", "新值：%s")
    item = _lang_item(tmp_path, "New Value: %s (%d)")
    hits, rest = split_items_by_db(
        TranslationResolver(db, VERSION),
        [item],
        tmp_path,
        is_valid_hit=lambda it, text: not cache_quality_issues(it, text),
    )
    assert hits == [] and rest == [item]


def test_directory_translation_hit_gate_accepts_matching_variant_translation(
    db, tmp_path
):
    _variant(db, "New Value: %s (%d)", "新值：%s（%d）")
    item = _lang_item(tmp_path, "New Value: %s (%d)")
    hits, rest = split_items_by_db(
        TranslationResolver(db, VERSION),
        [item],
        tmp_path,
        is_valid_hit=lambda it, text: not cache_quality_issues(it, text),
    )
    assert [h["text"] for h in hits] == ["新值：%s（%d）"] and rest == []
