"""預設 replace_rules.json 與替換引擎：短規則不可改壞較長詞，且結果不會被重複替換。"""

from __future__ import annotations

from pathlib import Path

import pytest

from translation_tool.utils.replace_rules import apply_replace_rules
from translation_tool.utils.text_processor import convert_text, load_replace_rules

_REPO_RULES = Path(__file__).resolve().parents[1] / "replace_rules.json"


@pytest.fixture(scope="module")
def default_rules():
    return load_replace_rules(str(_REPO_RULES))


@pytest.mark.parametrize(
    ("simplified", "expected"),
    [
        ("深色橡木椅子", "黑橡木椅子"),
        ("深色橡木", "黑橡木"),
        ("深色橡木原木", "黑橡木原木"),
        ("去皮橡木柱子", "剝皮橡木柱子"),
        ("去皮橡木原木", "剝皮橡木原木"),
    ],
)
def test_short_wood_rules_do_not_corrupt_longer_words(
    default_rules, simplified, expected
):
    assert convert_text(simplified, default_rules) == expected


def test_default_rules_are_idempotent_on_their_own_output(default_rules):
    for source in ("深色橡木椅子", "去皮橡木柱子", "騾子刷怪蛋", "紅磚塊"):
        once = convert_text(source, default_rules)
        assert apply_replace_rules(once, default_rules) == once


def test_engine_is_longest_match_single_pass():
    rules = [
        {"from": "深色橡木", "to": "黑橡木"},
        {"from": "深色橡木椅子", "to": "黑橡木長椅"},
        {"from": "桌", "to": "桌子"},
    ]
    assert apply_replace_rules("深色橡木椅子", rules) == "黑橡木長椅"
    assert apply_replace_rules("深色橡木桌", rules) == "黑橡木桌子"
    assert apply_replace_rules("深色橡木桌子", rules) == "黑橡木桌子"  # 不重複替換
