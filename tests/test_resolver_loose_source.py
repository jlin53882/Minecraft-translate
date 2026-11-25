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
    assert hit is not None and hit.zh_tw.strip() == "顯示背景"  # 前後空白跟著查詢的原文
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


# ------------------------------------------------ 版本優先序、統計、前後空白、保留原文
def _two_versions(
    db, *, target_en, target_tw, other_en, other_tw, other_version="1.20.1"
):
    db.ingest(VERSION, [ScanItem(KIND_LANG, "foo", KEY, target_en, target_tw, "")])
    db.ingest(other_version, [ScanItem(KIND_LANG, "foo", KEY, other_en, other_tw, "")])


@pytest.mark.parametrize("cross", [True, False])
def test_target_version_loose_match_beats_other_version_exact_match(db, cross):
    """目標版本寬鬆相等的譯文優先於其他版本逐字相同的譯文（關閉跨版本沿用時也要查得到）。"""
    _two_versions(
        db,
        target_en="Show background",
        target_tw="目標版本",
        other_en="Show Background",
        other_tw="舊版本",
    )
    resolver = TranslationResolver(db, VERSION, cross_version=cross)
    hit = resolver.lookup(KIND_LANG, "foo", KEY, "Show Background")
    assert hit is not None and hit.zh_tw == "目標版本" and not hit.cross
    assert resolver.stats.hit_target == 1 and resolver.stats.hit_loose == 1


def test_exact_candidate_wins_over_a_loose_one_of_the_same_version_rank(db):
    """其他版本之間：逐字相同優先於寬鬆相同（即使寬鬆那個版本比較近）。"""
    db.ingest(
        "1.20.1", [ScanItem(KIND_LANG, "foo", KEY, "Show Background", "逐字", "")]
    )
    db.ingest(
        "1.21.0", [ScanItem(KIND_LANG, "foo", KEY, "Show background", "寬鬆", "")]
    )
    resolver = TranslationResolver(db, VERSION, cross_version=True)
    hit = resolver.lookup(KIND_LANG, "foo", KEY, "Show Background")
    assert hit is not None and hit.zh_tw == "逐字" and hit.cross
    assert resolver.stats.hit_loose == 0


def test_loose_match_in_another_version_is_ignored_when_cross_version_is_off(db):
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", KEY, "Show background", "舊", "")])
    resolver = TranslationResolver(db, VERSION, cross_version=False)
    assert resolver.lookup(KIND_LANG, "foo", KEY, "Show Background") is None
    assert resolver.stats.miss == 1 and resolver.stats.hit_loose == 0


def test_loose_cross_version_hit_is_counted_once(db):
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", KEY, "Show background", "舊", "")])
    resolver = TranslationResolver(db, VERSION, cross_version=True)
    hit = resolver.lookup(KIND_LANG, "foo", KEY, "Show Background")
    assert hit is not None and hit.cross
    stats = resolver.stats
    assert (stats.hit_cross, stats.hit_loose, stats.miss, stats.en_mismatch) == (
        1,
        1,
        0,
        0,
    )


@pytest.mark.parametrize(
    ("stored", "queried", "expected"),
    [
        ("Show", " Show", " 顯示"),  # 查詢的原文有前導空白：譯文跟著有
        (" Show ", "Show", "顯示"),  # 查詢的原文沒有：譯文不帶資料庫那份的空白
        ("Value:\t%s", "value: %s", "值：%s"),  # 內部空白不影響前後
    ],
)
def test_loose_hit_takes_the_queried_texts_leading_and_trailing_whitespace(
    db, stored, queried, expected
):
    translation = {"Show": "顯示", " Show ": " 顯示 ", "Value:\t%s": "值：%s"}[stored]
    _seed(db, en=stored, tw=translation)
    hit = TranslationResolver(db, VERSION).lookup(KIND_LANG, "foo", KEY, queried)
    assert hit is not None and hit.zh_tw == expected


def test_merge_fill_keeps_the_source_for_a_deliberately_untranslated_proper_noun(db):
    """資料庫存 'Create' → 'Create'（刻意維持原文）：用 'CREATE' 查也不能當成已翻譯。"""
    from translation_tool.core.lang_merge_db import MergeDbFill

    _seed(db, en="Create", tw="Create")
    fill = MergeDbFill(db, VERSION, cross_version=False)
    final, pending, hits = fill.fill("foo", {}, {KEY: "CREATE"})
    assert hits == 0 and final == {} and pending == {KEY: "CREATE"}
    assert fill.skipped_same_as_source == 1 and fill.filled == 0


def test_merging_duplicates_does_not_keep_a_variant_equal_to_the_main_original_loosely(
    tmp_path,
):
    """別名合併：與保留條目主原文只差大小寫的其他原文不搬。"""
    from translation_tool.translation_db import variants

    db = TranslationDB(tmp_path / "m.db")
    try:
        _seed(db, en="Show Background", tw="顯示背景")
        db.ingest(VERSION, [ScanItem(KIND_LANG, "foo", "dup", "Other", "", "")])
        survivor = db._one("SELECT id FROM entry WHERE key=?", (KEY,))[0]
        duplicate = db._one("SELECT id FROM entry WHERE key='dup'")[0]
        with db._tx() as conn:
            variants.add_translation(conn, duplicate, "show background", 0, "x")
            variants.merge_into(conn, survivor, duplicate)
        assert (
            db._one("SELECT COUNT(*) FROM entry_variant WHERE entry_id=?", (survivor,))[
                0
            ]
            == 0
        )
    finally:
        db.close()


def test_loose_hit_keeps_the_translation_as_is_when_both_originals_have_the_same_edges(
    db,
):
    """資料庫原文與查詢的原文前後空白相同：譯文原樣（原文與譯文刻意不同的空白不動）。"""
    _seed(db, en="Speed: ", tw="速度：")
    hit = TranslationResolver(db, VERSION).lookup(KIND_LANG, "foo", KEY, "speed: ")
    assert hit is not None and hit.zh_tw == "速度："


def test_loose_cross_version_hit_also_takes_the_queried_texts_edges(db):
    db.ingest("1.20.1", [ScanItem(KIND_LANG, "foo", KEY, "Show", "顯示", "")])
    resolver = TranslationResolver(db, VERSION, cross_version=True)
    hit = resolver.lookup(KIND_LANG, "foo", KEY, " show")
    assert hit is not None and hit.cross and hit.zh_tw == " 顯示"


@pytest.mark.parametrize(
    ("stored", "queried"),
    [
        ("Press $(k:Jump) to jump", "Press $(k:jump) to jump"),  # Patchouli 巨集
        ("§aGreen", "§AGreen"),  # § 色碼在品質檢查裡是不同 token
    ],
)
def test_protected_token_case_differences_are_a_different_original(db, stored, queried):
    """品質檢查保護的 token（巨集、§ 色碼）大小寫不同，譯文不能互換：不是寬鬆相等。"""
    _seed(db, en=stored, tw="譯")
    resolver = TranslationResolver(db, VERSION)
    assert resolver.lookup(KIND_LANG, "foo", KEY, queried) is None
    assert resolver.stats.en_mismatch == 1 and resolver.stats.hit_loose == 0


def test_text_around_protected_tokens_is_still_compared_loosely(db):
    _seed(db, en="Press $(k:Jump) TO Jump", tw="按 $(k:Jump) 跳躍")
    hit = TranslationResolver(db, VERSION).lookup(
        KIND_LANG, "foo", KEY, "press $(k:Jump) to  jump"
    )
    assert hit is not None and hit.zh_tw == "按 $(k:Jump) 跳躍"


def test_other_originals_differing_only_in_a_protected_token_case_are_kept_apart(db):
    """兩份只差巨集大小寫的其他原文各自存譯文，不會因為共用同一份而卡住第二份。"""
    _seed(db, en="Main", tw="主")
    db.write_back(
        VERSION,
        [WriteBackItem(KIND_LANG, "foo", KEY, "Press $(k:Jump)", "按 $(k:Jump)")],
    )
    db.write_back(
        VERSION,
        [WriteBackItem(KIND_LANG, "foo", KEY, "Press $(k:jump)", "按 $(k:jump)")],
    )
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 2
    resolver = TranslationResolver(db, VERSION)
    assert (
        resolver.lookup(KIND_LANG, "foo", KEY, "Press $(k:Jump)").zh_tw
        == "按 $(k:Jump)"
    )
    assert (
        resolver.lookup(KIND_LANG, "foo", KEY, "Press $(k:jump)").zh_tw
        == "按 $(k:jump)"
    )


# ------------------------------------------------ 指南：連結目標大小寫是硬結構
GUIDE = "guide"
LINK_UP = "Read [the guide](https://example.org/Guide)"
LINK_LOW = "Read [the guide](https://example.org/guide)"
GKEY = "guide/intro.md#0"


def _guide_seed(db, en=LINK_UP, tw="閱讀[指南](https://example.org/Guide)"):
    db.ingest(VERSION, [ScanItem(GUIDE, "foo", GKEY, en, tw, "")])


def test_same_original_respects_guide_hard_structure():
    from translation_tool.translation_db.content_identity import same_original

    assert not same_original(GUIDE, LINK_UP, LINK_LOW)  # 連結目標大小寫不同
    assert same_original(
        GUIDE, LINK_UP, LINK_UP.replace("the guide", "THE GUIDE")
    )  # 連結文字大小寫
    assert same_original("lang", "Show Background", "show  background")


def test_lookup_does_not_treat_a_different_link_target_as_the_same_original(db):
    _guide_seed(db)
    resolver = TranslationResolver(db, VERSION)
    assert resolver.lookup(GUIDE, "foo", GKEY, LINK_LOW) is None
    assert resolver.stats.en_mismatch == 1 and resolver.stats.hit_loose == 0
    assert resolver.lookup(
        GUIDE, "foo", GKEY, LINK_UP.replace("the guide", "The Guide")
    ).zh_tw


def test_scan_registers_a_different_link_target_as_a_changed_original(db):
    _guide_seed(db)
    stats = db.ingest(VERSION, [ScanItem(GUIDE, "foo", GKEY, LINK_LOW, "", "")])
    assert stats.en_changed == 1
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 1


def test_two_guide_originals_differing_only_in_a_link_target_keep_separate_translations(
    db,
):
    """卡住第二份譯文的情境：兩份只差連結目標大小寫的其他原文各自存自己的譯文。"""
    _guide_seed(db, en="Main text", tw="主")
    db.write_back(
        VERSION,
        [
            WriteBackItem(
                GUIDE, "foo", GKEY, LINK_UP, "閱讀[指南](https://example.org/Guide)"
            )
        ],
    )
    db.write_back(
        VERSION,
        [
            WriteBackItem(
                GUIDE, "foo", GKEY, LINK_LOW, "閱讀[指南](https://example.org/guide)"
            )
        ],
    )
    assert db._one("SELECT COUNT(*) FROM entry_variant")[0] == 2
    resolver = TranslationResolver(db, VERSION)
    assert resolver.lookup(GUIDE, "foo", GKEY, LINK_UP).zh_tw.endswith("/Guide)")
    assert resolver.lookup(GUIDE, "foo", GKEY, LINK_LOW).zh_tw.endswith("/guide)")


def test_write_back_with_a_different_link_target_becomes_a_variant_not_the_main_entrys(
    db,
):
    _guide_seed(db)
    stats = db.write_back(
        VERSION,
        [
            WriteBackItem(
                GUIDE, "foo", GKEY, LINK_LOW, "閱讀[指南](https://example.org/guide)"
            )
        ],
    )
    assert stats.variant_written == 1 and stats.written == 0
