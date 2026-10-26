"""替換規則診斷：交叉引用、自我引用、重複、前後空白與 s2twp 轉換。"""

import json
from pathlib import Path

from translation_tool.utils.replace_rules_diagnostics import (
    CONVERTED_SOURCE,
    CROSS_REFERENCE,
    DUPLICATE_FROM,
    EDGE_SPACE,
    SELF_REFERENCE,
    diagnose_rules,
)
from translation_tool.utils.text_processor import safe_convert_text


def _kinds(result, idx):
    return {i.kind for i in result.get(idx, [])}


def test_cross_reference_flags_translation_containing_other_source():
    rules = [
        {"from": "噴濺型迅捷藥水", "to": "飛濺 迅捷藥水"},
        {"from": "滯留型迅捷藥水", "to": "滯留 迅捷藥水"},
        {"from": "迅捷", "to": "加速"},
    ]
    result = diagnose_rules(rules)
    assert _kinds(result, 0) == {CROSS_REFERENCE}
    assert _kinds(result, 1) == {CROSS_REFERENCE}
    assert 2 not in result
    issue = result[0][0]
    assert issue.other == 2
    assert "#3" in issue.message and "迅捷" in issue.message


def test_self_reference_and_duplicate_and_edge_space():
    rules = [
        {"from": "騾", "to": "騾子"},
        {"from": "abc", "to": "x"},
        {"from": "abc", "to": " y"},
    ]
    result = diagnose_rules(rules)
    assert _kinds(result, 0) == {SELF_REFERENCE}
    assert 1 not in result
    assert _kinds(result, 2) == {DUPLICATE_FROM, EDGE_SPACE}
    assert result[2][0].other == 1


def test_regex_sources_skip_containment_checks():
    rules = [{"from": "(.*?)", "to": "(.*?)x"}, {"from": "ab", "to": "(.*?)abc"}]
    result = diagnose_rules(rules)
    # 正則原詞不做字串包含；固定原詞的譯詞含自己的原詞仍會被標出
    assert 0 not in result
    assert _kinds(result, 1) == {SELF_REFERENCE}


def test_converted_source_needs_converter():
    rules = [{"from": "岩", "to": "石"}, {"from": "石頭", "to": "石塊"}]
    assert diagnose_rules(rules) == {}
    result = diagnose_rules(rules, safe_convert_text)
    assert _kinds(result, 0) == {CONVERTED_SOURCE}
    assert "岩→巖" in result[0][0].message
    assert 1 not in result


def test_single_character_sources_are_not_cross_references():
    rules = [{"from": "岩", "to": "石"}, {"from": "甲", "to": "岩石"}]
    assert diagnose_rules(rules) == {}


def test_diagnose_real_rules_file_is_fast_enough_to_run_on_load():
    path = Path(__file__).resolve().parents[1] / "replace_rules.json"
    rules = json.loads(path.read_text(encoding="utf-8"))
    result = diagnose_rules(rules)
    assert all(0 <= idx < len(rules) for idx in result)


def test_converted_source_ignores_phrases_that_conversion_keeps():
    # 對繁體原詞直接跑 s2twp 會得到「巖漿怪」，但簡中「岩浆怪」轉換後是「岩漿怪」，規則可以命中；
    # 單字「岩」不論從哪種簡體寫法轉換都會變成「巖」
    rules = [{"from": "岩漿怪", "to": "岩漿立方怪"}, {"from": "岩", "to": "石"}]
    result = diagnose_rules(rules, safe_convert_text)
    assert 0 not in result
    assert _kinds(result, 1) == {CONVERTED_SOURCE}
