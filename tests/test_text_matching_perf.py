"""術語比對的效能回歸測試。

這些測試不量測牆上時間（CI 不穩定），而是檢查「工作量」：
每個來源只正規化一次、每個術語在同一來源最多檢查一次。
修改前每次比對都會重新正規化來源與術語，候選數上來後會明顯變慢。
"""

from __future__ import annotations

import pytest

from translation_tool.utils import text_matching as tm


def _brute_force_matches(sources, terms):
    """逐一用 contains_term 比對，作為正確性的參考答案。"""
    return {
        term for term in terms for source in sources if tm.contains_term(source, term)
    }


SOURCES = (
    "Iron Sword and Diamond-Pickaxe",
    "block.minecraft.stone_brick_stairs",
    "Wooden_Door opens when powered",
    "a dog barked at the fox",
    "",
)

TERMS = (
    "Iron Sword",
    "Diamond Pickaxe",
    "stone brick",
    "Wooden Door",
    "dog",
    "do",
    "fox",
    "ox",
    "Nether Star",
    "iron",
    "  Iron   Sword  ",
    "missing",
)


def test_matching_terms_equals_brute_force_reference():
    """快速路徑的結果必須與逐一 contains_term 的結果完全相同。"""
    assert tm.matching_terms(SOURCES, TERMS) == _brute_force_matches(SOURCES, TERMS)


def test_matching_terms_respects_word_boundaries():
    """詞界規則不能因快速路徑而改變：「ox」不應命中「fox」以外的整字。"""
    matched = tm.matching_terms(("a fox ran",), ("ox", "fox", "a fo"))
    assert matched == {"fox"}


def test_matching_terms_normalizes_each_source_once(monkeypatch):
    """每個來源只正規化一次，不能隨術語數量線性放大。"""
    calls: list[str] = []
    original = tm.normalize_term_text

    def counting_normalize(value, *, casefold=False):
        calls.append(value)
        return original(value, casefold=casefold)

    many_terms = tuple(f"term {index} sword" for index in range(500))
    # 前綴索引是一次性成本，由呼叫端預先建立並重複使用；這裡排除它，只量比對本身。
    prefix_index = tm.build_term_prefix_index(many_terms)

    monkeypatch.setattr(tm, "normalize_term_text", counting_normalize)
    tm.matching_terms(("the sword of term 7",), many_terms, prefix_index=prefix_index)

    assert len(calls) == 1


def test_each_term_is_checked_at_most_once_per_source(monkeypatch):
    """同一術語可能落在多個前綴桶，但同一來源內只能檢查一次。"""
    checks: list[str] = []
    original = tm._contains_normalized_term

    def counting_check(normalized_source, normalized_term):
        checks.append(normalized_term)
        return original(normalized_source, normalized_term)

    monkeypatch.setattr(tm, "_contains_normalized_term", counting_check)
    terms = tuple(
        f"iron {suffix}" for suffix in ("sword", "axe", "hoe", "ingot", "nugget")
    )
    source = "iron sword iron axe iron hoe iron ingot"
    tm.matching_terms((source,), terms)

    assert len(checks) == len(set(checks))
    assert len(checks) <= len(terms)


@pytest.mark.parametrize("bad", [None, 3, object()])
def test_non_string_inputs_are_ignored(bad):
    """非字串輸入維持原本行為：不拋例外、不命中。"""
    assert tm.normalize_term_text(bad) == ""
    assert tm.contains_term(bad, "iron") is False
    assert tm.matching_terms((bad,), ("iron",)) == set()
