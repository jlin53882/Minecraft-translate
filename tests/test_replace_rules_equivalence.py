"""替換規則語意與遞迴套用回歸測試。"""

import json
import random
from pathlib import Path

from translation_tool.utils.replace_rules import (
    exact_source,
    find_replacement_conflicts,
)
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


def test_atomic_replacement_protects_adjacent_generated_destinations():
    rules = [{"from": "熊貓", "to": "貓熊"}]

    first = apply_replace_rules("熊貓熊貓", rules)
    assert first == "貓熊貓熊"
    assert apply_replace_rules(first, rules) == first


def test_atomic_replacement_falls_back_when_longest_match_is_protected():
    rules = [
        {"from": "石磚", "to": "青苔石磚"},
        {"from": "前綴青苔", "to": "完整替換"},
        {"from": "前綴", "to": "短詞替換"},
    ]

    assert apply_replace_rules("前綴青苔石磚", rules) == "短詞替換青苔石磚"


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


def test_atomic_replacement_normalizes_destination_chains():
    rules = [
        {"from": "蜜脾", "to": "蜂巢"},
        {"from": "蜂巢", "to": "蜂窩"},
    ]

    assert apply_replace_rules("蜜脾", rules) == "蜂窩"
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
        "蜜脾": "蜂巢",  # 整段規則：item.minecraft.honeycomb
        "蜂巢": "蜂巢",  # 繁中「蜂巢」是蜜脾（item.minecraft.honeycomb），不能再替換
        "紅磚": "紅磚",  # 繁中「紅磚」是方塊（block.minecraft.bricks），不能再替換
        "紅磚窗戶": "紅磚窗戶",
        "樹脂磚": "樹脂磚",
    }

    for source, destination in expected.items():
        assert apply_replace_rules(source, rules) == destination


def test_project_literal_rules_are_idempotent():
    rules = json.loads(
        (Path(__file__).parents[1] / "replace_rules.json").read_text(encoding="utf-8")
    )

    literal_rules = [
        rule
        for rule in rules
        if isinstance(rule, dict)
        and isinstance(rule.get("from"), str)
        and isinstance(rule.get("to"), str)
        and not any(char in rule["from"] for char in ".?*[]()\\")
        and exact_source(rule["from"]) is None
    ]
    assert len(literal_rules) == 1578

    expected_canonical = {
        "噴濺型力量藥水": "飛濺力量藥水",
        "噴濺型水肺藥水": "飛濺水下呼吸藥水",
        "噴濺型迅捷藥水": "飛濺迅捷藥水",
        "滯留型力量藥水": "滯留力量藥水",
        "滯留型水肺藥水": "滯留水下呼吸藥水",
        "滯留型迅捷藥水": "滯留迅捷藥水",
        "去皮緋紅菌核": "剝皮緋紅菌絲體",
        "去皮詭異菌核": "剝皮扭曲菌絲體",
        "知識就是力量": "知書就是力量",
        "雕紋樹脂磚塊": "浮雕樹脂磚",
        "樹脂磚樓梯": "樹脂磚階梯",
        "樹脂磚臺階": "樹脂磚半磚",
        "樹脂磚塊": "樹脂磚",
        "水肺之箭": "水下呼吸之箭",
        "水肺藥水": "水下呼吸藥水",
        "皮革外套": "皮革上衣",
        "紅磚樓梯": "紅磚階梯",
        "紅磚臺階": "紅磚半磚",
        "緋紅菌巖": "緋紅菌絲石",
        "緋紅菌核": "緋紅菌絲體",
        "詭異菌核": "扭曲菌絲體",
        "紅磚塊": "紅磚",
        "蜜脾塊": "蜂巢塊",
        "蜜脾": "蜂巢",
    }
    assert len(expected_canonical) == 24
    for source, destination in expected_canonical.items():
        assert apply_replace_rules(source, rules) == destination

    assert apply_replace_rules("熊貓熊貓", rules) == "貓熊貓熊"

    for rule in literal_rules:
        source = rule["from"]
        first = apply_replace_rules(source, rules)
        assert apply_replace_rules(first, rules) == first, (
            source,
            first,
            apply_replace_rules(first, rules),
        )


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


def test_longer_source_is_not_hidden_by_shorter_destination():
    """來源詞比以同樣開頭的目的詞更長時，仍要套用該規則。"""
    rules = [{"from": "鑽石礦石", "to": "鑽石礦"}, {"from": "書架", "to": "書櫃"}]
    assert apply_replace_rules("鑽石礦石", rules) == "鑽石礦"
    assert apply_replace_rules("書架", rules) == "書櫃"
    # 已是目的詞時維持不變（冪等）
    assert apply_replace_rules("鑽石礦", rules) == "鑽石礦"
    assert apply_replace_rules("書櫃", rules) == "書櫃"


def test_project_rules_apply_directly_on_first_pass():
    """真實規則的來源詞，第一次套用就要得到規則指定的目的詞（不能只是冪等）。

    目的詞內含來源詞、或被其他規則改寫的鏈狀規則，結果由 expected_canonical
    與冪等測試涵蓋，這裡排除它們。
    """
    rules = json.loads(
        (Path(__file__).parents[1] / "replace_rules.json").read_text(encoding="utf-8")
    )
    literal = [
        r
        for r in rules
        if isinstance(r, dict)
        and isinstance(r.get("from"), str)
        and isinstance(r.get("to"), str)
        and len(r["from"]) >= 2  # 單字輸入本來就不套用規則
        and not any(char in r["from"] for char in ".?*[]()\\")
        and exact_source(r["from"]) is None
    ]
    sources = {r["from"] for r in literal}
    destinations = {r["to"] for r in literal}
    # 整段完全相符規則對單獨的這段文字優先
    whole_texts = {exact_source(r["from"]) for r in rules if isinstance(r, dict)}
    direct = [
        r
        for r in literal
        if r["from"] not in whole_texts
        and r["from"] not in r["to"]
        and not any(s in r["to"] for s in sources if s != r["from"])
        and not any(d in r["from"] for d in destinations if d != r["to"])
        and not any(s != r["from"] and r["from"] in s for s in sources)
    ]
    assert direct, "沒有可直接驗證的規則"
    for rule in direct:
        assert apply_replace_rules(rule["from"], rules) == rule["to"], rule


def test_adjacent_destinations_forming_longer_source_is_a_known_limit():
    """相鄰目的詞交界形成更長來源詞時，不保證二次處理冪等（見 apply_replace_rules）。

    無狀態替換分不出「原始輸入」與「上一次替換的結果」；比目的詞更長的來源詞
    必須優先（鑽石礦石 → 鑽石礦），所以這個形狀的輸入只處理一次。
    """
    rules = [{"from": "煤礦石", "to": "煤礦"}, {"from": "石英塊", "to": "石英方塊"}]
    first = apply_replace_rules("煤礦石石英塊", rules)
    assert first == "煤礦石英方塊"
    assert apply_replace_rules(first, rules) == "煤礦英方塊"


def test_load_replace_rules_skips_non_string_rules(tmp_path):
    import json

    from translation_tool.utils.text_processor import load_replace_rules

    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            [
                {"from": 123, "to": "x"},
                {"from": "a", "to": 5},
                {"from": "ok", "to": "好"},
            ]
        ),
        encoding="utf-8",
    )

    assert [rule["from"] for rule in load_replace_rules(str(path))] == ["ok"]
