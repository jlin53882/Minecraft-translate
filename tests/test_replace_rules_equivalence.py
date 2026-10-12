"""替換規則語意與遞迴套用回歸測試。"""

import json
import random
from pathlib import Path

from translation_tool.utils.replace_rules import find_replacement_conflicts
from translation_tool.utils.text_processor import apply_replace_rules


def _naive(text, rules):
    """舊版語意：固定字串依長詞優先逐條替換（可串接），整段無關鍵字時略過。"""
    literal = sorted(
        [(r["from"], r["to"]) for r in rules], key=lambda x: len(x[0]), reverse=True
    )
    if len(text) < 2:
        return text
    keys = {src[:2] for src, _ in literal if src}
    if keys and not any(
        k in text or k.replace(" ", "") in text.replace(" ", "") for k in keys
    ):
        return text
    for src, dst in literal:
        if src and src in text:
            text = text.replace(src, dst)
    return text


def test_cascading_replacement_matches_sequential_semantics():
    # 長詞優先，且後面的規則會作用在前面規則的輸出上
    rules = [
        {"from": "內存條", "to": "記憶體模組"},
        {"from": "記憶", "to": "回憶"},
        {"from": "模組", "to": "模組件"},
    ]
    text = "這是內存條"
    assert (
        apply_replace_rules(text, rules, cascade=True)
        == _naive(text, rules)
        == "這是回憶體模組件"
    )


def test_literal_replacement_is_atomic_by_default():
    rules = [
        {"from": "內存條", "to": "記憶體模組"},
        {"from": "記憶", "to": "回憶"},
        {"from": "模組", "to": "模組件"},
    ]

    assert apply_replace_rules("這是內存條", rules) == "這是記憶體模組"


def test_atomic_replacement_protects_existing_destination_phrases():
    rules = [
        {"from": "苔石磚", "to": "青苔石磚"},
        {"from": "苔石磚樓梯", "to": "青苔石磚階梯"},
        {"from": "苔石", "to": "青苔鵝卵石"},
        {"from": "樓梯", "to": "階梯"},
        {"from": "甲青苔", "to": "甲轉換"},
    ]

    assert apply_replace_rules("苔石磚", []) == "苔石磚"  # 沒有規則時維持原文
    assert apply_replace_rules("苔石磚", rules) == "青苔石磚"
    assert apply_replace_rules("苔石磚樓梯", rules) == "青苔石磚階梯"
    assert apply_replace_rules("青苔石磚", rules) == "青苔石磚"
    assert apply_replace_rules("青苔石磚樓梯", rules) == "青苔石磚階梯"
    assert apply_replace_rules("甲青苔石磚", rules) == "甲青苔石磚"
    converted = apply_replace_rules("苔石磚", rules)
    assert apply_replace_rules(converted, rules) == converted
    assert apply_replace_rules("苔石磚", rules, cascade=True) == "青青苔鵝卵石磚"


def test_atomic_replacement_allows_a_destination_that_is_another_source():
    rules = [
        {"from": "蜜脾", "to": "蜂巢"},
        {"from": "蜂巢", "to": "蜂窩"},
    ]

    assert apply_replace_rules("蜜脾", rules) == "蜂巢"
    assert apply_replace_rules("蜂巢", rules) == "蜂窩"


def test_project_rules_keep_real_overlapping_outputs_stable():
    rules = json.loads(
        (Path(__file__).parents[1] / "replace_rules.json").read_text(encoding="utf-8")
    )

    expected = {
        "苔石磚": "青苔石磚",
        "苔石磚樓梯": "青苔石磚階梯",
        "青苔石磚": "青苔石磚",
        "青苔石磚樓梯": "青苔石磚樓梯",
        "蜂巢": "蜂窩",
        "紅磚": "紅磚頭",
        "樹脂磚": "樹脂磚頭",
    }

    for source, destination in expected.items():
        assert apply_replace_rules(source, rules) == destination


def test_conflict_report_finds_sources_inside_outputs():
    rules = [
        {"from": "苔石磚", "to": "青苔石磚"},
        {"from": "苔石", "to": "青苔鵝卵石"},
    ]

    conflicts = find_replacement_conflicts(rules)
    assert (0, "苔石磚", 0, "青苔石磚") in conflicts
    assert (1, "苔石", 0, "青苔石磚") in conflicts


def test_randomized_equivalence_with_sequential_replace():
    random.seed(7)
    alphabet = "甲乙丙丁戊己庚辛 "
    for _ in range(200):
        rules = []
        for _ in range(random.randint(1, 25)):
            src = "".join(random.choice(alphabet) for _ in range(random.randint(1, 3)))
            dst = "".join(random.choice(alphabet) for _ in range(random.randint(0, 4)))
            rules.append({"from": src, "to": dst})
        for _ in range(10):
            text = "".join(
                random.choice(alphabet) for _ in range(random.randint(0, 20))
            )
            assert apply_replace_rules(text, rules, cascade=True) == _naive(
                text, rules
            ), (
                text,
                rules,
            )


def test_regex_rules_are_not_hidden_by_unmatched_fixed_rule_keywords():
    rules = [
        {"from": "unrelated", "to": "literal"},
        {"from": r"\d+", "to": "number"},
    ]
    assert apply_replace_rules("item 42", rules) == "item number"
