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
