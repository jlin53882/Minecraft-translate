"""PR #104 Finding 4：編譯後的替換規則在規則「就地修改」後也必須失效。

原本快取只看 id(rules) + len(rules)，rules[0]["to"] = ... 這類同長度修改
會繼續沿用舊的編譯結果。
"""

import json

import pytest

from translation_tool.utils import text_processor as tp
from translation_tool.utils.text_processor import apply_replace_rules


def _plain_rules():
    return [{"from": "hello", "to": "FIRST"}]


def _loaded_rules(tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"from": "hello", "to": "FIRST"}]), encoding="utf-8")
    monkeypatch.setattr(tp, "resolve_project_path", lambda p: tmp_path / p)
    return tp.load_replace_rules("rules.json")


@pytest.fixture(params=["plain_list", "load_replace_rules"])
def rules(request, tmp_path, monkeypatch):
    if request.param == "plain_list":
        return _plain_rules()
    return _loaded_rules(tmp_path, monkeypatch)


def test_in_place_edit_of_to_takes_effect(rules):
    assert apply_replace_rules("hello", rules) == "FIRST"
    rules[0]["to"] = "SECOND"
    assert apply_replace_rules("hello", rules) == "SECOND"


def test_in_place_edit_of_from_takes_effect(rules):
    assert apply_replace_rules("hello world", rules) == "FIRST world"
    rules[0]["from"] = "world"
    assert apply_replace_rules("hello world", rules) == "hello FIRST"


def test_replace_rule_object_takes_effect(rules):
    assert apply_replace_rules("hello", rules) == "FIRST"
    rules[0] = {"from": "hello", "to": "THIRD"}
    assert apply_replace_rules("hello", rules) == "THIRD"


def test_append_remove_and_new_list(rules):
    assert apply_replace_rules("hello world", rules) == "FIRST world"
    rules.append({"from": "world", "to": "世界"})
    assert apply_replace_rules("hello world", rules) == "FIRST 世界"
    rules.pop(0)
    assert apply_replace_rules("hello world", rules) == "hello 世界"
    assert (
        apply_replace_rules("hello world", [{"from": "hello", "to": "NEW"}])
        == "NEW world"
    )


def test_loaded_rules_stay_json_serializable(tmp_path, monkeypatch):
    rules = _loaded_rules(tmp_path, monkeypatch)
    rules[0]["to"] = "X"
    assert json.loads(json.dumps(rules)) == [{"from": "hello", "to": "X"}]
    import orjson

    assert orjson.loads(orjson.dumps(rules)) == [{"from": "hello", "to": "X"}]


def test_tracked_rule_is_not_shared_between_replace_rules():
    """同一個 tracked rule 不可同時屬於兩個 ReplaceRules。

    若共用同一個物件，修改只會通知其中一個 owner，另一個的編譯快取就會過期。
    """
    a = tp.ReplaceRules([{"from": "hello", "to": "A"}])
    assert apply_replace_rules("hello", a) == "A"

    b = tp.ReplaceRules()
    b.append(a[0])
    assert apply_replace_rules("hello", b) == "A"

    # 物件隔離
    assert b[0] is not a[0]

    # 改 b 不影響 a（含 a 的編譯快取）
    b[0]["to"] = "B"
    assert apply_replace_rules("hello", b) == "B"
    assert apply_replace_rules("hello", a) == "A"

    # 反向：改 a 必須讓 a 的快取失效，且不影響 b
    a[0]["to"] = "C"
    assert apply_replace_rules("hello", a) == "C"
    assert apply_replace_rules("hello", b) == "B"


def test_tracked_rule_adopted_via_slice_and_insert_is_isolated():
    a = tp.ReplaceRules([{"from": "hello", "to": "A"}])
    b = tp.ReplaceRules([{"from": "x", "to": "y"}])
    assert apply_replace_rules("hello", a) == "A"

    b[0:1] = [a[0]]
    b.insert(0, a[0])
    b.extend([a[0]])
    assert all(r is not a[0] for r in b)

    a[0]["to"] = "C"
    assert apply_replace_rules("hello", a) == "C"
    assert apply_replace_rules("hello", b) == "A"


def test_rule_reused_within_same_replace_rules_keeps_tracking():
    """同一個 ReplaceRules 內重複放入自己的規則仍會正確失效。"""
    a = tp.ReplaceRules([{"from": "hello", "to": "A"}])
    a.append(a[0])
    assert apply_replace_rules("hello", a) == "A"

    a[1]["to"] = "B"
    assert apply_replace_rules("hello", a) == "B"
