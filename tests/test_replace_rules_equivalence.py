"""Task 7：apply_replace_rules 加速後，結果需與「逐條依序 replace」完全相同。"""

import random

from translation_tool.utils.text_processor import apply_replace_rules


def _naive(text, rules):
    """舊版語意：固定字串依長詞優先逐條 replace（可串接），整段無關鍵字時略過。"""
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
    assert apply_replace_rules(text, rules) == _naive(text, rules) == "這是回憶體模組件"


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
            assert apply_replace_rules(text, rules) == _naive(text, rules), (
                text,
                rules,
            )


def test_regex_rules_are_not_hidden_by_unmatched_fixed_rule_keywords():
    rules = [
        {"from": "unrelated", "to": "literal"},
        {"from": r"\d+", "to": "number"},
    ]
    assert apply_replace_rules("item 42", rules) == "item number"
