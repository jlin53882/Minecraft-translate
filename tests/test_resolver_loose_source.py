"""流水線查詢：原文只差大小寫或同一行內空白時視為同一句（不是原文變動）。

規則與掃描判斷「原文已變動」同一套（``loose_text``）；換行位置與 ``%s``／``%S`` 這類格式碼的
大小寫有語義，仍視為不同。
"""

from __future__ import annotations

import pytest

from translation_tool.translation_db import (
    ScanItem,
    TranslationDB,
    TranslationResolver,
    WriteBackItem,
)
from translation_tool.translation_db.schema import KIND_LANG

VERSION = "1.21.1"
KEY = "item.foo.a"


@pytest.fixture
def db(tmp_path):
    d = TranslationDB(tmp_path / "t.db")
    yield d
    d.close()


def _seed(db, en="Show Background", tw="顯示背景", version=VERSION):
    db.ingest(version, [ScanItem(KIND_LANG, "foo", KEY, en, tw, "")])


@pytest.mark.parametrize(
    "queried",
    ["Show background", "SHOW BACKGROUND", "Show   Background", " Show Background "],
)
def test_case_and_inline_whitespace_differences_still_hit(db, queried):
    _seed(db)
    resolver = TranslationResolver(db, VERSION)
    hit = resolver.lookup(KIND_LANG, "foo", KEY, queried)
    assert hit is not None and hit.zh_tw == "顯示背景"
    assert resolver.stats.hit_loose == 1 and resolver.stats.en_mismatch == 0


def test_exact_match_is_preferred_and_not_counted_as_loose(db):
    _seed(db)
    resolver = TranslationResolver(db, VERSION)
    assert resolver.lookup(KIND_LANG, "foo", KEY, "Show Background").zh_tw == "顯示背景"
    assert resolver.stats.hit_loose == 0 and resolver.stats.hit_target == 1


@pytest.mark.parametrize(
    ("stored", "queried"),
    [
        ("Line one\nLine two", "Line one Line two"),  # 換行位置有語義
        ("Line one\nLine two", "Line one Line\ntwo"),
        ("Value: %s", "Value: %S"),  # %s 與 %S 的格式化結果不同
        ("Show Background", "Background Style"),  # 真正的原文變動
    ],
)
def test_semantic_differences_are_still_a_mismatch(db, stored, queried):
    _seed(db, en=stored, tw="譯")
    resolver = TranslationResolver(db, VERSION)
    assert resolver.lookup(KIND_LANG, "foo", KEY, queried) is None
    assert resolver.stats.en_mismatch == 1 and resolver.stats.hit_loose == 0


def test_cross_version_lookup_also_tolerates_case_and_whitespace(db):
    _seed(db, version="1.20.1")
    resolver = TranslationResolver(db, VERSION, cross_version=True)
    hit = resolver.lookup(KIND_LANG, "foo", KEY, "Show  background")
    assert hit is not None and hit.cross and hit.zh_tw == "顯示背景"


def test_ai_write_back_with_only_a_case_change_is_found_next_run(db):
    """AI 翻完寫回後，下次查同一句（只差大小寫）要查得到，不能每次重送。"""
    _seed(db, tw="")
    stats = db.write_back(
        VERSION, [WriteBackItem(KIND_LANG, "foo", KEY, "Show background", "顯示背景")]
    )
    assert stats.written == 1 and db._one("SELECT COUNT(*) FROM src_change")[0] == 0
    hit = TranslationResolver(db, VERSION).lookup(
        KIND_LANG, "foo", KEY, "Show background"
    )
    assert hit is not None and hit.zh_tw == "顯示背景"


def test_other_original_is_not_duplicated_by_a_case_only_difference(db):
    """其他原文也一樣：只差大小寫不另建一份，兩種寫法都查得到同一份譯文。"""
    _seed(db)
    db.ingest(
        VERSION, [ScanItem(KIND_LANG, "foo", KEY, "Background Style", "背景樣式", "")]
    )
    db.write_back(
        VERSION, [WriteBackItem(KIND_LANG, "foo", KEY, "Background style", "AI 譯")]
    )
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 1
    resolver = TranslationResolver(db, VERSION)
    assert (
        resolver.lookup(KIND_LANG, "foo", KEY, "Background Style").zh_tw == "背景樣式"
    )
    assert (
        resolver.lookup(KIND_LANG, "foo", KEY, "Background style").zh_tw == "背景樣式"
    )
