"""不靠名單的文字檔判斷（text_sniffer）與 scanner 的內容判斷匯入。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from translation_tool.core.text_sniffer import (
    is_prose,
    sniff_text_file,
    split_locale,
)
from translation_tool.translation_db.scanner import ScanOptions, scan_jar

GUIDE = "---\nnavigation:\n  title: Wireless\n---\n\n# Wireless\n\nTerminals let you reach the network from far away.\n"


@pytest.mark.parametrize(
    ("parts", "lang", "other", "neutral"),
    [
        (["ae2guide", "a.md"], "en_us", False, "ae2guide/a.md"),
        (["ae2guide", "_zh_cn", "a.md"], "zh_cn", False, "ae2guide/a.md"),
        (["guide", "_ru_ru", "a.md"], None, True, "guide/a.md"),
        (["text", "coral_en_us.txt"], "en_us", False, "text/coral.txt"),
        (["text", "coral_fr_fr.txt"], None, True, "text/coral.txt"),
        (
            ["lang", "bestiary", "en_us_0", "a.txt"],
            "en_us",
            False,
            "lang/bestiary/a.txt",
        ),
        (["lang", "zh_tw.json"], "zh_tw", False, "lang/*.json"),
    ],
)
def test_split_locale(parts, lang, other, neutral):
    found = split_locale(parts)
    assert (found.lang, found.other, found.neutral) == (lang, other, neutral)


@pytest.mark.parametrize(
    "value",
    [
        "minecraft:stone",
        "#c:ores",
        "assets/a/b.png",
        "ff00aa",
        "12",
        "item.mod.thing.name",
    ],
)
def test_ids_paths_and_numbers_are_not_prose(value):
    assert not is_prose(value)


def test_sentences_are_prose():
    assert is_prose("Terminals let you reach the network from far away.")


@pytest.mark.parametrize(
    ("text", "suffix", "name", "expected"),
    [
        (GUIDE, ".md", "assets/m/x/a.md", "guide"),
        (
            "List all attached modules on the smartglasses.",
            ".txt",
            "a/help/x.txt",
            "text",
        ),
        (
            "# Translations\n\nThis page explains how to translate the mod.",
            ".md",
            "lang/README.md",
            None,
        ),
        ('{"type": "minecraft:add", "argument1": 1}', ".txt", "worldgen/a.txt", None),
        (
            "Optional custom models for the dangling lantern body.",
            ".txt",
            "models/block/readme.txt",
            None,
        ),
        (
            "Some long descriptive sentence about textures here.",
            ".txt",
            "assets/m/textures/a.txt",
            None,
        ),
        ("short", ".txt", "assets/m/a.txt", None),
    ],
)
def test_sniff_text_file(text, suffix, name, expected):
    assert sniff_text_file(text, suffix, name) == expected


def _jar(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def test_scan_finds_guides_in_unconfigured_directories_by_content(tmp_path):
    jar = _jar(
        tmp_path / "m.jar",
        {
            "assets/m/anything/at/all/wireless.md": GUIDE,
            "assets/m/anything/at/all/_zh_tw/wireless.md": "---\nnavigation:\n  title: 無線\n---\n\n# 無線\n\n終端機讓你從遠處存取網路。\n",
            "assets/m/textures/readme.txt": "This folder holds the textures of the mod.",
            "assets/m/lang/README.md": "# Translations\n\nHelp us translate this mod on Crowdin.",
        },
    )
    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    keys = {item.key for item in result.items if item.kind == "guide"}
    assert keys
    assert all(k.startswith("anything/at/all/wireless.md#") for k in keys)
    assert any(item.zh_tw for item in result.items)


def test_scan_content_gate_can_be_turned_off_with_the_guide_switch(tmp_path):
    jar = _jar(tmp_path / "m.jar", {"assets/m/x/wireless.md": GUIDE})
    result = scan_jar(
        jar, ScanOptions(version="1.21.1", include_patchouli=False), dir_names=()
    )
    assert result.items == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Hello world, this is a page.", "text"),
        ("Overview", "text"),
        ("$(#3DB53D)Eterna$()", "text"),
        ("§lNooo.§r", "text"),
        ("[#](8B0000)Activation Cost:[#]() 50,000 EV", "text"),
        ("mod:basics/entry", "not"),
        ("patchouli:text", "not"),
        ("&mod:flag,!mod:other", "not"),
        ("#c:ingots", "not"),
        ("#tier#", "not"),
        ("#result->iname", "not"),
        ("minecraft:stone[count=2]", "not"),
        ('{modsRequired:[{modName:"psi"}]}', "not"),
        ("textures/gui/a.png", "not"),
        ("https://example.com/x", "not"),
        ("FFAA00", "not"),
        ("#FFAA00", "not"),
        ("true", "not"),
        ("12", "not"),
        ("alchemy_arrays", "not"),
        ("getCreatorModId", "not"),
        ("已翻譯的文字", "not"),
        ("$(#FC5454)Кванты$()", "not"),
        ("$(br)", "not"),
        ("book.mod.entry.title", "lang_key"),
        ("# book.mod.common.found_in", "lang_key"),
    ],
)
def test_classify_book_string_by_value_shape(value, expected):
    from translation_tool.core.text_sniffer import classify_book_string

    assert classify_book_string(value) == expected


@pytest.mark.parametrize(
    ("data", "parts", "expected"),
    [
        (
            {"name": "E", "pages": [{"type": "patchouli:text", "text": "x"}]},
            ["a"],
            True,
        ),
        ({"landing_text": "Hello there"}, ["book.json"], True),
        ({"name": "B", "creative_tab": "t"}, ["book.json"], True),
        ({"name": "Cat", "icon": "x"}, ["categories", "c.json"], True),
        ({"components": [{"type": "x"}]}, ["templates", "t.json"], True),
        ({"text": [{"text": "A"}], "effects": []}, ["modifiers", "m.json"], True),
        ({"name": "x", "icon": "y"}, ["misc", "c.json"], False),
        ({"parent": "minecraft:block/cube"}, ["models", "b.json"], False),
        ([1, 2], ["a.json"], False),
    ],
)
def test_looks_like_book_json(data, parts, expected):
    from translation_tool.core.text_sniffer import looks_like_book_json

    assert looks_like_book_json(data, parts) is expected


def test_scan_finds_book_json_without_any_directory_setting(tmp_path):
    import json

    book = {"name": "Guide", "landing_text": "Welcome to the guide book."}
    entry = {
        "name": "Basics",
        "category": "mod:basics",
        "pages": [{"type": "patchouli:text", "text": "Plain English text here."}],
    }
    jar = _jar(
        tmp_path / "m.jar",
        {
            "assets/m/whatever/book.json": json.dumps(book),
            "assets/m/whatever/en_us/entries/basics.json": json.dumps(entry),
            "assets/m/models/block/x.json": json.dumps(entry),
            "data/m/recipe/r.json": json.dumps({"type": "minecraft:crafting_shaped"}),
        },
    )
    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    keys = {i.key.split("#")[0] for i in result.items if i.kind == "patchouli"}
    assert keys == {"whatever/book.json", "whatever/entries/basics.json"}


def test_sniffing_does_not_fail_the_jar_when_the_read_budget_runs_out(tmp_path):
    from translation_tool.translation_db import sniff_groups

    files = {
        f"data/m/dir{i}/f.json": "{}"
        for i in range(sniff_groups.SNIFF_MAX_MEMBERS + 50)
    }
    files["assets/m/lang/en_us.json"] = '{"item.m.a": "Alpha"}'
    jar = _jar(tmp_path / "m.jar", files)
    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    assert not result.error
    assert any(i.kind == "lang" for i in result.items)
