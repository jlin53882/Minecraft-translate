from __future__ import annotations

from pathlib import Path

import pytest

from translation_tool.core import lm_translator_main as lm_main


def test_safe_json_loads_parses_fenced_json_object() -> None:
    raw = '```json\n{"a": 1, "b": "測試"}\n```'

    parsed = lm_main.safe_json_loads(raw)

    assert parsed == {"a": 1, "b": "測試"}


def test_safe_json_loads_raises_for_invalid_payload() -> None:
    with pytest.raises(RuntimeError, match="JSON 解析失敗"):
        lm_main.safe_json_loads("not valid json at all")


def test_find_lang_json_finds_only_assets_lang_json(tmp_path: Path) -> None:
    good = tmp_path / "assets" / "demo_mod" / "lang" / "en_us.json"
    ignored_model = tmp_path / "assets" / "demo_mod" / "models" / "foo.json"
    ignored_root = tmp_path / "lang" / "zh_tw.json"

    good.parent.mkdir(parents=True, exist_ok=True)
    ignored_model.parent.mkdir(parents=True, exist_ok=True)
    ignored_root.parent.mkdir(parents=True, exist_ok=True)

    good.write_text("{}", encoding="utf-8")
    ignored_model.write_text("{}", encoding="utf-8")
    ignored_root.write_text("{}", encoding="utf-8")

    found = lm_main.find_lang_json(tmp_path)

    assert found == [good]


def test_extract_translatables_for_lang_file_handles_top_level_nested_text_and_lists(monkeypatch) -> None:
    monkeypatch.setattr(lm_main, "is_value_translatable", lambda value, is_lang=False: isinstance(value, str) and bool(value.strip()))

    data = {
        "item.demo.name": "鑽石劍",
        "component": {"text": "描述文字", "color": "red", "translate": "demo.key"},
        "pages": ["第一頁", {"text": "第二頁"}, 123],
        "blank": "   ",
    }

    items = lm_main.extract_translatables(data, "assets/demo/lang/en_us.json")
    paths = {item["path"] for item in items}

    assert paths == {
        "item.demo.name",
        "component.text",
        "pages[0]",
        "pages[1].text",
    }


def test_extract_translatables_for_patchouli_uses_translatable_field_rules(monkeypatch) -> None:
    monkeypatch.setattr(lm_main, "is_translatable_field", lambda key: key in {"title", "text"})
    monkeypatch.setattr(lm_main, "is_value_translatable", lambda value, is_lang=False: isinstance(value, str) and bool(value.strip()))

    data = {
        "title": "章節標題",
        "text": "內文",
        "icon": "minecraft:book",
        "nested": {"text": "巢狀內文", "color": "blue"},
    }

    items = lm_main.extract_translatables(data, "assets/demo/patchouli_books/book/en_us/entry.json")
    paths = {item["path"] for item in items}

    assert paths == {"title", "text", "nested.text"}


def test_set_by_path_supports_nested_list_path() -> None:
    root = {"pages": [{"text": "old"}]}

    lm_main.set_by_path(root, "pages[0].text", "new")

    assert root["pages"][0]["text"] == "new"


def test_set_by_path_supports_flat_key_followed_by_index() -> None:
    root = {"pages": [{"multiblock.pattern": ["AAA", "BBB"]}]}

    lm_main.set_by_path(root, "pages[0].multiblock.pattern[1]", "CCC")

    assert root["pages"][0]["multiblock.pattern"] == ["AAA", "CCC"]
