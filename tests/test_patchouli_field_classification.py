"""手冊 JSON 字串依欄位分類：id 欄位一律不翻、顯示欄位放寬、未知欄位的單一 token 保守不翻。

掃描（``extract_translatables``）與清理（``entry_cleanup_category``）共用
``classify_book_string``，同一組 (鍵, 值) 兩邊的結論必須互補。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from translation_tool.core.text_sniffer import advancement_texts, classify_book_string
from translation_tool.core.translatable_extractor import extract_translatables
from translation_tool.translation_db.cleanup import entry_cleanup_category

BOOK_FILE = Path("assets/m/patchouli_books/b/en_us/entries/x.json")
LANG_FILE = Path("assets/m/lang/en_us.json")


def _book_paths(doc: object) -> set[str]:
    return {item["path"] for item in extract_translatables(doc, BOOK_FILE)}


def _lang_paths(doc: object) -> set[str]:
    return {item["path"] for item in extract_translatables(doc, LANG_FILE)}


def test_anchor_is_an_id_but_name_with_the_same_value_is_text():
    assert _book_paths({"anchor": "QuickStart", "name": "QuickStart"}) == {"name"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anchor", "CreeBee"),
        ("anchor", "Quick Start"),
        ("flag", "IsUnlocked"),
        ("type", "SomePageType"),
        ("item", "IronIngot"),
        ("recipe", "MyRecipe"),
        ("recipe2", "OtherRecipe"),
        ("entity", "CreeBee"),
        ("advancement", "FirstSteps"),
        ("multiblock_id", "Altar"),
        ("trigger", "GetWood"),
        ("icon", "Compass"),
        ("category", "Basics"),
        ("template", "Header"),
        ("url", "Website"),
    ],
)
def test_id_fields_are_never_text(field, value):
    """官方文件明寫是 id／路徑／結構的欄位，值長得像名稱也不翻。"""
    assert _book_paths({field: value}) == set()
    assert classify_book_string(value, field) == "not"


def test_image_paths_and_relation_entries_are_not_text():
    doc = {
        "images": ["m:textures/a.png", "textures/gui/Page.png", "Overview"],
        "entries": ["m:basics/intro", "Intro"],
    }
    assert _book_paths(doc) == set()


def test_text_override_ids_stay_excluded_but_multi_word_labels_are_text():
    doc = {"text_overrides": [["Raw Will", "raw"]]}
    assert _book_paths(doc) == {"text_overrides[0][0]"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "pollen_sifter"),
        ("name", "Semiblocks/Gadgets"),
        ("title", "CreeBee"),
        ("text", "addArea()"),
        ("link_text", "Wiki"),
    ],
)
def test_display_fields_keep_single_tokens(field, value):
    assert _book_paths({field: value}) == {field}


def test_tooltip_list_items_are_display_text():
    doc = {"tooltip": ["CreeBee", "pollen_sifter"]}
    assert _book_paths(doc) == {"tooltip[0]", "tooltip[1]"}


def test_patchouli_page_type_fields_keep_entity_ids_and_multiblock_structure_safe():
    """頁型／entity id／多方塊結構不翻；可見 name 與未知自然語句仍可辨識。"""
    doc = {
        "type": "patchouli:entity",
        "entity": "minecraft:zombie",
        "name": "Zombie",
        "multiblock": {
            "pattern": [[" A "], [" B "]],
            "mapping": {"A": "minecraft:stone", "B": "minecraft:dirt"},
        },
        "entity_data": {"custom": "This visible explanation is safe."},
    }

    assert _book_paths(doc) == {"name", "entity_data.custom"}
    assert classify_book_string("minecraft:zombie", "entity") == "not"
    assert classify_book_string(" A ", "pattern") == "not"
    assert classify_book_string("minecraft:stone", "mapping") == "not"


@pytest.mark.parametrize(
    "value", ["minecraft:stone", "https://example.com/x", "12", "#FFAA00", "#tier#"]
)
def test_display_fields_still_exclude_strong_structure_shapes(value):
    assert classify_book_string(value, "name") == "not"
    assert _book_paths({"name": value}) == set()


@pytest.mark.parametrize("value", ["MasterXort", "someThing", "FOO_BAR", "raw"])
def test_unknown_field_single_tokens_are_not_text(value):
    assert classify_book_string(value) == "not"
    assert _book_paths({"custom": value}) == set()


def test_unknown_field_sentences_are_still_text():
    doc = {"custom": "MasterXort is the boss of this place."}
    assert _book_paths(doc) == {"custom"}


def test_list_items_use_the_parent_display_field():
    assert _book_paths({"text": ["raw"]}) == {"text[0]"}
    assert _book_paths({"pages": [{"title": ["CreeBee"]}]}) == {"pages[0].title[0]"}


def test_lang_keys_are_reported_as_lang_key_except_in_id_fields():
    assert classify_book_string("book.mod.entry", "name") == "lang_key"
    assert classify_book_string("book.mod.entry") == "lang_key"
    assert classify_book_string("book.mod.entry", "anchor") == "not"
    assert _book_paths({"name": "book.mod.entry"}) == set()


def test_advancement_title_is_judged_as_a_display_field():
    adv = {
        "criteria": {"x": {}},
        "display": {
            "title": {"text": "MasterXort"},
            "description": "pollen_sifter",
        },
    }
    assert advancement_texts(adv) == {
        "display.title.text": "MasterXort",
        "display.description": "pollen_sifter",
    }


def test_lang_display_snake_case_is_extractable_but_identifier_is_not():
    """顯示欄位的 snake_case 可保留；沒有語意的 tooltip 仍維持保守排除。"""
    doc = {
        "block.createaddition.barbed_wire.tooltip": "BARBED_WIRE",
        "text.autoconfig.simpleplanes.option.FLY_TICKS_PER_COAL": (
            "FLY_TICKS_PER_COAL"
        ),
        "subtitles.clogger_roaring": "clogger_roaring",
        "effect.undead_revamp2.tankleak": "tank_leaks",
        "item.m.oil.tooltip": "SEED_OIL_BUCKET",
    }
    assert {item["path"] for item in extract_translatables(doc, LANG_FILE)} == {
        "block.createaddition.barbed_wire.tooltip",
        "text.autoconfig.simpleplanes.option.FLY_TICKS_PER_COAL",
        "subtitles.clogger_roaring",
        "effect.undead_revamp2.tankleak",
    }


def test_advancement_text_components_without_a_writer_are_explicitly_not_claimed():
    """目前 ModDB 沒有 Advancement JSON writer，不能半途宣稱支援巢狀元件。"""
    adv = {
        "criteria": {"x": {}},
        "display": {
            "title": {
                "translate": "advancements.story.mine_stone",
                "fallback": "Mine Stone",
                "with": [{"text": "literal"}],
                "extra": [{"text": "suffix", "bold": True}],
                "clickEvent": {
                    "action": "open_url",
                    "value": "https://example.com",
                },
            },
            "description": {"text": "Literal description"},
        },
    }
    # 只有現有的直接字面 text 路徑會進 ModDB；translate／fallback／with／extra
    # 尚未接上完整 JSON round-trip，因此必須保持原始 JSON 不被誤寫入。
    assert advancement_texts(adv) == {"display.description.text": "Literal description"}


@pytest.mark.parametrize("value", ["← →", "180°", "TODO"])
def test_lang_nested_text_and_lists_skip_symbol_only_and_placeholder_values(value):
    doc = {"a.nested": {"text": value}, "a.list": [value]}
    assert _lang_paths(doc) == set()


def test_lang_nested_text_and_lists_keep_values_with_format_codes():
    doc = {
        "a.nested": {"text": "%s → %s"},
        "a.list": ["%s → %s", "Deals %s damage"],
    }
    assert _lang_paths(doc) == {"a.nested.text", "a.list[0]", "a.list[1]"}


_CONSISTENCY_CASES = [
    ({"anchor": "QuickStart"}, "anchor"),
    ({"name": "QuickStart"}, "name"),
    ({"flag": "IsUnlocked"}, "flag"),
    ({"type": "SomePageType"}, "type"),
    ({"item": "IronIngot"}, "item"),
    ({"entity": "CreeBee"}, "entity"),
    ({"images": ["textures/gui/Page.png"]}, "images[0]"),
    ({"text_overrides": [["Raw Will", "raw"]]}, "text_overrides[0][1]"),
    ({"text_overrides": [["Raw Will", "raw"]]}, "text_overrides[0][0]"),
    ({"name": "pollen_sifter"}, "name"),
    ({"name": "Semiblocks/Gadgets"}, "name"),
    ({"title": "CreeBee"}, "title"),
    ({"text": "addArea()"}, "text"),
    ({"text": ["raw"]}, "text[0]"),
    ({"tooltip": ["CreeBee"]}, "tooltip[0]"),
    ({"custom": "MasterXort"}, "custom"),
    ({"custom": "someThing"}, "custom"),
    ({"custom": "FOO_BAR"}, "custom"),
    ({"custom": "MasterXort is the boss of this place."}, "custom"),
    ({"pages": [{"anchor": "CreeBee", "title": "CreeBee"}]}, "pages[0].anchor"),
    ({"pages": [{"anchor": "CreeBee", "title": "CreeBee"}]}, "pages[0].title"),
]


@pytest.mark.parametrize(("doc", "path"), _CONSISTENCY_CASES)
def test_scan_and_cleanup_agree(doc, path):
    """被掃描收進來的條目清理不會刪；沒收的條目清理會歸到某個不收的類別。"""
    extracted = path in _book_paths(doc)
    value = doc
    for part in path.replace("]", "").replace("[", ".").split("."):
        value = value[int(part)] if part.isdigit() else value[part]
    category = entry_cleanup_category("patchouli", f"b/x.json#{path}", value)
    if extracted:
        assert category is None
    else:
        assert category in {"not_text", "lang_key", "placeholder", "multiblock"}
