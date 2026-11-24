"""其他原文（變體）的譯文不能繞過資料庫譯文共用的品質把關。

流水線讀到變體譯文的路徑（語系合併、目錄翻譯的資料庫命中）與一般資料庫命中用同一套
特殊字元檢查；變體譯文的特殊字元與它的原文不一致時，必須和一般命中一樣被拒絕。
"""

from __future__ import annotations

import pytest

from translation_tool.core.lang_merge_db import MergeDbFill
from translation_tool.core.lm_translator_db import DirectoryDbContext, split_db_hits
from translation_tool.translation_db import (
    ScanItem,
    TranslationDB,
    TranslationResolver,
    WriteBackItem,
)
from translation_tool.translation_db.md_pipeline import (
    MdOutputReport,
    MdSourceFile,
    _resolve_blocks,
)
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_LANG

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
    fill = MergeDbFill(db, VERSION, cross_version=False)
    final, pending, hits = fill.fill("foo", {}, {KEY: "New Value: %s (%d)"})
    assert hits == 0 and final == {} and pending == {KEY: "New Value: %s (%d)"}
    assert fill.rejected_quality == 1


def test_merge_fill_accepts_a_variant_translation_with_matching_tokens(db):
    _variant(db, "New Value: %s (%d)", "新值：%s（%d）")
    fill = MergeDbFill(db, VERSION, cross_version=False)
    final, _pending, hits = fill.fill("foo", {}, {KEY: "New Value: %s (%d)"})
    assert hits == 1 and final == {KEY: "新值：%s（%d）"}


def _directory_split(db, tmp_path, item):
    """正式的目錄翻譯資料庫分流（``split_db_hits``，含品質把關的接線）。"""
    ctx = DirectoryDbContext(
        db=db,
        version=VERSION,
        resolver=TranslationResolver(db, VERSION),
        buffer=None,
        root=tmp_path,
    )
    return split_db_hits(ctx, [item])


def test_directory_translation_rejects_a_variant_translation_with_mismatched_tokens(
    db, tmp_path
):
    _variant(db, "New Value: %s (%d)", "新值：%s")
    item = _lang_item(tmp_path, "New Value: %s (%d)")
    hits, rest = _directory_split(db, tmp_path, item)
    assert hits == [] and rest == [item]


def test_directory_translation_accepts_a_variant_translation_with_matching_tokens(
    db, tmp_path
):
    _variant(db, "New Value: %s (%d)", "新值：%s（%d）")
    item = _lang_item(tmp_path, "New Value: %s (%d)")
    hits, rest = _directory_split(db, tmp_path, item)
    assert [h["text"] for h in hits] == ["新值：%s（%d）"] and rest == []


def _guide_source(text):
    block = type("Block", (), {"text": text})()
    return MdSourceFile(
        rel="assets/foo/guide/intro.md",
        out_rel="assets/foo/guide/_zh_tw/intro.md",
        mod_id="foo",
        file_key="guide/intro.md",
        text=text,
        blocks=[block],
        translatable={0},
    )


@pytest.mark.parametrize(
    ("translation", "accepted"),
    [("新值：%s", False), ("新值：%s（%d）", True)],
)
def test_guide_pipeline_applies_the_same_gate_to_variant_translations(
    db, translation, accepted
):
    """指南流水線（``_resolve_blocks``）：其他原文的譯文特殊字元不一致時保留原文。"""
    en = "New Value: %s (%d)"
    db.ingest(
        VERSION,
        [ScanItem(KIND_GUIDE, "foo", "guide/intro.md#0", "Old: %s", "舊：%s", "")],
    )
    db.write_back(
        VERSION, [WriteBackItem(KIND_GUIDE, "foo", "guide/intro.md#0", en, translation)]
    )
    report = MdOutputReport()
    blocks, translated = _resolve_blocks(
        TranslationResolver(db, VERSION), _guide_source(en), report
    )
    if accepted:
        assert (blocks, translated, report.blocks_rejected) == ([translation], 1, 0)
    else:
        assert (blocks, translated, report.blocks_rejected) == ([en], 0, 1)
