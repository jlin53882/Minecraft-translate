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
        ({"landing_text": "Hello there"}, ["book.json"], False),
        ({"name": "B", "creative_tab": "t"}, ["book.json"], False),
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
    assert keys == {"whatever/entries/basics.json"}  # book.json 不處理


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


def test_legacy_lang_files_are_imported_and_json_wins_over_lang(tmp_path):
    jar = _jar(
        tmp_path / "m.jar",
        {
            "assets/m/lang/en_US.lang": "# comment\nitem.m.a=Alpha Item\ntile.m.b=Beta Block\n\n",
            "assets/m/lang/zh_CN.lang": "item.m.a=阿尔法物品\n",
            "assets/old/lang/en_us.lang": "item.old.a=Old\n",
            "assets/old/lang/en_us.json": '{"item.old.a": "Old From Json"}',
            "data/dl/lang/en_us.json": '{"command.dl.hello": "Hello there friend"}',
        },
    )
    result = scan_jar(jar, ScanOptions(version="1.12.2"), dir_names=())
    rows = {(i.mod_id, i.key): (i.en_us, i.zh_cn) for i in result.items}
    assert rows[("m", "item.m.a")] == ("Alpha Item", "阿尔法物品")
    assert rows[("m", "tile.m.b")][0] == "Beta Block"
    assert rows[("old", "item.old.a")][0] == "Old From Json"
    assert rows[("dl", "command.dl.hello")][0] == "Hello there friend"


def test_advancement_literal_display_text_is_imported(tmp_path):
    import json

    literal = {
        "criteria": {"x": {"trigger": "minecraft:tick"}},
        "display": {
            "title": {"text": "Stone Age Begins"},
            "description": "Mine your first stone block.",
            "icon": {"id": "minecraft:stone"},
        },
    }
    keyed = {
        "criteria": {"x": {"trigger": "minecraft:tick"}},
        "display": {
            "title": {"translate": "adv.m.a.title"},
            "description": {"translate": "adv.m.a.desc"},
        },
    }
    recipe_unlock = {"criteria": {"has": {"trigger": "minecraft:inventory_changed"}}}
    jar = _jar(
        tmp_path / "m.jar",
        {
            "data/m/advancement/story/start.json": json.dumps(literal),
            "data/m/advancement/story/keyed.json": json.dumps(keyed),
            "data/m/advancement/recipes/a.json": json.dumps(recipe_unlock),
        },
    )
    result = scan_jar(jar, ScanOptions(version="1.21.1"), dir_names=())
    rows = {i.key: i.en_us for i in result.items if i.kind == "advancement"}
    assert rows == {
        "story/start.json#display.title.text": "Stone Age Begins",
        "story/start.json#display.description": "Mine your first stone block.",
    }


def test_book_extraction_also_pulls_content_detected_books_and_guides(tmp_path):
    import json

    from translation_tool.core.jar_processor import extract_book_files_generator

    entry = {
        "name": "Basics",
        "pages": [{"type": "patchouli:text", "text": "Plain English text here."}],
    }
    mods = tmp_path / "mods"
    mods.mkdir()
    _jar(
        mods / "demo-1.0.jar",
        {
            "assets/demo/anyname/en_us/entries/basics.json": json.dumps(entry),
            "assets/demo/guides/demo/guide/wireless.md": GUIDE,
            "assets/demo/guides/demo/guide/_zh_cn/wireless.md": "# 无线\n\n终端让你远程访问。\n",
            "assets/demo/textures/readme.txt": "This folder holds the textures of the mod.",
        },
    )
    out = tmp_path / "out"
    list(
        extract_book_files_generator(str(mods), str(out), lang_codes=["en_us", "zh_cn"])
    )
    got = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert "assets/demo/anyname/en_us/entries/basics.json" in got
    assert "assets/demo/guides/demo/guide/wireless.md" in got
    assert "assets/demo/guides/demo/guide/_zh_cn/wireless.md" in got
    assert not any("readme" in g for g in got)


def test_find_patchouli_json_detects_books_by_content_outside_configured_dirs(tmp_path):
    import json

    from translation_tool.core.translatable_extractor import find_patchouli_json

    entry = {"name": "E", "pages": [{"type": "patchouli:text", "text": "Hello there."}]}
    (tmp_path / "assets/m/anyname/en_us/entries").mkdir(parents=True)
    (tmp_path / "assets/m/anyname/en_us/entries/e.json").write_text(json.dumps(entry))
    (tmp_path / "assets/m/anyname/book.json").write_text(
        json.dumps({"landing_text": "hi"})
    )
    (tmp_path / "assets/m/lang").mkdir(parents=True)
    (tmp_path / "assets/m/lang/en_us.json").write_text('{"a": "b"}')
    (tmp_path / "demo_extracted/data/m/books/en_us").mkdir(parents=True)
    (tmp_path / "demo_extracted/data/m/books/en_us/f.json").write_text(
        json.dumps(entry)
    )

    found = {p.relative_to(tmp_path).as_posix() for p in find_patchouli_json(tmp_path)}
    assert found == {
        "assets/m/anyname/en_us/entries/e.json",
        "demo_extracted/data/m/books/en_us/f.json",
    }
    # 明確指定資料夾名稱時只用名稱規則
    assert find_patchouli_json(tmp_path, dir_names=["nothing"]) == []


def test_lm_directory_translation_translates_guides_and_writes_zh_tw_folder(
    tmp_path, monkeypatch
):
    from translation_tool.core import lm_translator

    root = tmp_path / "in"
    guide = root / "assets/demo/ae2guide/wireless.md"
    guide.parent.mkdir(parents=True)
    guide.write_text(
        "---\nnavigation:\n  title: Wireless\n---\n\n# Wireless\n\n"
        "Terminals reach the network from far away.\n\n"
        '<ItemLink id="demo:terminal" />\n',
        encoding="utf-8",
    )
    (guide.parent / "_ru_ru").mkdir()
    (guide.parent / "_ru_ru/wireless.md").write_text("# Privet\n\nTekst tut.\n")

    def fake_translate(batch, total=None):
        for item in batch:
            item["text"] = "譯：" + item["text"]
        return batch, "DONE"

    monkeypatch.setattr(lm_translator, "validate_api_keys", lambda: None)
    monkeypatch.setattr(lm_translator, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(lm_translator, "get_cache_dict_ref", lambda _t: {})
    monkeypatch.setattr(lm_translator, "translate_batch_smart", fake_translate)
    monkeypatch.setattr(lm_translator, "add_to_cache", lambda *a, **k: None)
    monkeypatch.setattr(lm_translator, "save_translation_cache", lambda *a, **k: None)

    out = tmp_path / "out"
    list(
        lm_translator.translate_directory_generator(
            str(root), str(out), dry_run=False, use_translation_db=False
        )
    )
    written = out / "assets/demo/ae2guide/_zh_tw/wireless.md"
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "譯：Terminals reach the network from far away." in text
    assert '<ItemLink id="demo:terminal" />' in text
    assert text.startswith("---\nnavigation:\n  title: 譯：Wireless\n---")
    assert not (out / "assets/demo/ae2guide/_zh_tw/_ru_ru").exists()


def test_finish_guide_item_keeps_source_when_tags_or_links_change():
    from translation_tool.core.guide_translation import finish_guide_item
    from translation_tool.plugins.shared.rich_text_shield import shield_text

    source = 'See <ItemLink id="demo:a" /> and [docs](https://x.y/z).'
    ok = {"text": source.replace("See", "見"), "source_text": source}
    ok["_shielded"] = shield_text(source)
    ok["_shielded"].shields.clear()
    finish_guide_item(ok)
    assert ok["text"].startswith("見") and not ok.get("_untranslated")

    bad = {"text": source.replace("demo:a", "demo:b"), "source_text": source}
    bad["_shielded"] = shield_text(source)
    bad["_shielded"].shields.clear()
    finish_guide_item(bad)
    assert bad["text"] == source and bad["_untranslated"] is True
