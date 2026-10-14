"""指南（.md／.txt）：資料夾名稱相對匹配、辨識身分、匯入資料庫條目。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from translation_tool.core.book_dirs import find_book_dir_index
from translation_tool.translation_db.identity import classify_member
from translation_tool.translation_db.scanner import ScanOptions, scan_jar

GUIDE_DIRS = ("ae2guide", "guide")

EN_MD = """# Wireless Terminal

Open it with a right click.

Needs a charged battery.
"""
ZH_MD = """# 無線終端

用右鍵開啟。

需要充滿電的電池。
"""


def _jar(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("assets", "m", "guide", "a.md"), 2),
        (("assets", "m", "guides", "m", "guide", "_zh_cn", "a.md"), 4),
        (("assets", "m", "AE2Guide", "a.md"), 2),  # 不分大小寫
        (("assets", "m", "textures", "a.md"), None),
        (("assets", "guide", "textures", "a.md"), None),  # 模組 id 本身不算
        (("assets", "m", "guide"), None),  # 最後一段是檔名，不是資料夾
    ],
)
def test_find_book_dir_index_matches_the_name_at_any_depth(parts, expected):
    assert find_book_dir_index(parts, GUIDE_DIRS) == expected


def test_default_names_still_match_patchouli_books_at_the_top_level():
    ident = classify_member("assets/m/patchouli_books/b/en_us/entries/x.json")
    assert (ident.kind, ident.mod_id, ident.file_key, ident.lang) == (
        "patchouli",
        "m",
        "patchouli_books/b/entries/x.json",
        "en_us",
    )


def test_nested_guide_name_matches_patchouli_style_json_too():
    ident = classify_member("assets/m/guides/m/guide/en_us/entries/x.json", GUIDE_DIRS)
    assert (ident.kind, ident.file_key) == (
        "patchouli",
        "guides/m/guide/entries/x.json",
    )


@pytest.mark.parametrize(
    ("member", "lang", "file_key"),
    [
        (
            "assets/ae2/ae2guide/items/wireless.md",
            "en_us",
            "ae2guide/items/wireless.md",
        ),
        (
            "assets/ae2/ae2guide/_zh_tw/items/wireless.md",
            "zh_tw",
            "ae2guide/items/wireless.md",
        ),
        ("assets/lbr/guides/lbr/guide/_zh_cn/a.txt", "zh_cn", "guides/lbr/guide/a.txt"),
        ("pack/assets/ae2/ae2guide/_en_us/a.md", "en_us", "ae2guide/a.md"),
    ],
)
def test_guide_files_are_classified_with_the_language_folder_removed(
    member, lang, file_key
):
    ident = classify_member(member, GUIDE_DIRS)
    assert (ident.kind, ident.lang, ident.file_key) == ("guide", lang, file_key)
    assert ident.item_key("3") == f"{file_key}#3"


@pytest.mark.parametrize(
    "member",
    [
        "assets/ae2/ae2guide/_pt_br/a.md",  # 其他語言不處理
        "assets/ae2/ae2guide/README.md",
        "assets/ae2/textures/a.md",  # 不在指南資料夾
        "assets/ae2/ae2guide/a.png",
    ],
)
def test_other_languages_readme_and_unrelated_files_are_ignored(member):
    assert classify_member(member, GUIDE_DIRS) is None


def test_guides_are_not_found_unless_the_directory_name_is_configured():
    assert classify_member("assets/ae2/ae2guide/a.md") is None  # 預設名稱不含 ae2guide


def _scan(jar: Path, **kw):
    return scan_jar(jar, ScanOptions(version="1.21.1", **kw), dir_names=GUIDE_DIRS)


def test_scan_imports_blocks_with_translations_by_paragraph(tmp_path):
    jar = _jar(
        tmp_path / "ae2.jar",
        {
            "assets/ae2/ae2guide/wireless.md": EN_MD,
            "assets/ae2/ae2guide/_zh_tw/wireless.md": ZH_MD,
        },
    )
    result = _scan(jar)
    rows = {i.key: (i.en_us, i.zh_tw) for i in result.items}
    assert rows == {
        "ae2guide/wireless.md#0": ("# Wireless Terminal", "# 無線終端"),
        "ae2guide/wireless.md#1": ("Open it with a right click.", "用右鍵開啟。"),
        "ae2guide/wireless.md#2": ("Needs a charged battery.", "需要充滿電的電池。"),
    }
    assert {i.kind for i in result.items} == {"guide"}


def test_scan_reads_txt_files_too(tmp_path):
    jar = _jar(
        tmp_path / "m.jar",
        {"assets/m/guide/notes.txt": "First paragraph.\n\nSecond paragraph.\n"},
    )
    result = _scan(jar)
    assert [i.en_us for i in result.items] == ["First paragraph.", "Second paragraph."]
    assert all(i.zh_tw == "" for i in result.items)


def test_translation_with_a_different_paragraph_count_is_not_imported(tmp_path):
    jar = _jar(
        tmp_path / "ae2.jar",
        {
            "assets/ae2/ae2guide/wireless.md": EN_MD,
            "assets/ae2/ae2guide/_zh_tw/wireless.md": "# 無線終端\n\n只剩一段。\n",
        },
    )
    result = _scan(jar)
    assert len(result.items) == 3  # 原文照常匯入
    assert all(i.zh_tw == "" for i in result.items)  # 譯文段落數不同：不匯入以免錯位


def test_code_blocks_and_chinese_source_are_not_importable_text(tmp_path):
    jar = _jar(
        tmp_path / "m.jar",
        {
            "assets/m/guide/a.md": "Real text.\n\n```\ncode here\n```\n\n已經是中文。\n",
        },
    )
    result = _scan(jar)
    assert [i.en_us for i in result.items] == ["Real text."]


def test_include_patchouli_off_skips_guides_as_well(tmp_path):
    jar = _jar(tmp_path / "m.jar", {"assets/m/guide/a.md": "Some text.\n"})
    assert _scan(jar, include_patchouli=False).items == []


def test_other_language_folders_without_underscore_are_not_source_text():
    assert classify_member("assets/ie/manual/cs_cz/a.txt", GUIDE_DIRS_MANUAL) is None
    found = classify_member("assets/ie/manual/en_us/a.txt", GUIDE_DIRS_MANUAL)
    assert found is not None and found.lang == "en_us"


GUIDE_DIRS_MANUAL = ("manual",)


def test_guide_blocks_include_front_matter_navigation_title_after_paragraphs():
    from translation_tool.core.guide_blocks import extract_guide_blocks

    text = (
        "---\nnavigation:\n  title: Energy Storage\n  icon: x\n---\n\n"
        "# Energy\n\nBody text here for the guide.\n"
    )
    blocks = extract_guide_blocks(text, "energy.md")
    assert [b.text for b in blocks][-1] == "Energy Storage"
    assert blocks[-1].prefix == "  title: "
    assert blocks[-1].start_line == 3
    assert all(not b.prefix for b in blocks[:-1])


def test_guide_without_navigation_title_has_no_extra_block():
    from translation_tool.core.guide_blocks import extract_guide_blocks

    text = "---\ntitle: Not Navigation\n---\n\n# Head\n\nBody text here.\n"
    assert not any(b.prefix for b in extract_guide_blocks(text, "a.md"))


def test_front_title_line_quotes_yaml_special_characters():
    from translation_tool.core.guide_blocks import front_title_line

    assert front_title_line("  title: ", "能源儲存") == "  title: 能源儲存"
    assert front_title_line("  title: ", "能源: 儲存") == '  title: "能源: 儲存"'


def test_tag_only_blocks_are_not_translatable_but_keep_block_indexes():
    from translation_tool.core.guide_blocks import (
        extract_guide_blocks,
        has_translatable_text,
    )

    assert not has_translatable_text(
        '<ImportStructure src="../structure/x.snbt"></ImportStructure>'
    )
    assert not has_translatable_text('<ItemImage id="x" scale="2" />')
    assert has_translatable_text('<ItemLink id="ae2:x" /> is great')
    text = (
        "# Title\n\n"
        '<ImportStructure src="../s.snbt"></ImportStructure>\n\n'
        "Real paragraph of English text here.\n"
    )
    blocks = extract_guide_blocks(text, "a.md")
    assert [has_translatable_text(b.text) for b in blocks] == [True, False, True]


def test_non_prose_guide_blocks_are_not_translatable():
    from translation_tool.core.guide_blocks import has_translatable_text

    comment = "[//]: # (navigation:)\n[//]: # (  title: X)"
    license_text = "Permission is hereby granted, free of charge, to any person."
    copyright_line = "Copyright (c) 2023 Someone"
    settings = (
        "Dragon ShouldSpawn: true\nDragon SpawnDebug: false\nDragon SpawnYFloor: -50"
    )
    names = "\n".join(f"Name{i}" for i in range(10))
    todo = "[TODO: screenshot goes here]"
    for text in (comment, license_text, copyright_line, settings, names, todo):
        assert not has_translatable_text(text), text


def test_prose_that_looks_a_little_like_the_excluded_shapes_is_kept():
    from translation_tool.core.guide_blocks import has_translatable_text

    keep = [
        (
            "- Blue cube: Normal ME devices, they have enough channel.\n"
            "- Yellow cube: Dense ME devices, they can pass 32 channels.\n"
            "- Red cube: Broken devices."
        ),
        "Note: the terminal needs power to work properly.",
        "Alpha\nBeta\nGamma",  # 少於 8 行不算名字清單
        "Use the **Terminal** to see items.",
    ]
    for text in keep:
        assert has_translatable_text(text), text


def test_placeholder_book_strings_are_not_text():
    from translation_tool.core.text_sniffer import classify_book_string

    for value in (
        "TODO",
        "[TODO: photo of GUI goes here]",
        "$(italic)TODO: heat pipe image/$",
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
    ):
        assert classify_book_string(value) == "not", value
    assert classify_book_string("The todo list is on the board.") == "text"


def test_key_value_ignores_explanations_and_code_examples():
    from translation_tool.core.guide_blocks import has_translatable_text

    prose = [
        'To get bundled inputs:\nc = rs.getBundledInput( "right" )\nred = colors.test( c, colors.red )',
        (
            "Helpful when trying out different spawn configs for the weights\n"
            "    true: will set the entity to glowing and add information to the logs.\n"
            "    false: will not enable spawn debug features."
        ),
        (
            "water: anywhere on the world's surface in water\n"
            "subterranean: in caves only for the deep parts\n"
            "nether: nether only and not in lava places"
        ),
    ]
    for text in prose:
        assert has_translatable_text(text), text
    assert not has_translatable_text(
        "ShouldSpawn: true\nSpawnBiomes: Jungle; Jungle Hills;\nSpawnWeight: 12"
    )


def test_text_starting_with_a_bracket_is_not_mistaken_for_json():
    from translation_tool.core.text_sniffer import sniff_text_file

    story = "\n\nOnce upon a time there was a long story told right here."
    assert sniff_text_file("[c=#2dba69]The Coral Chant" + story, ".txt") == "text"
    assert sniff_text_file("{b}XNet Manual" + story, ".txt") == "text"
    assert (
        sniff_text_file('["a long enough string value for the sniffer"]', ".txt")
        is None
    )


def test_horizontal_rule_inside_a_guide_is_not_front_matter():
    from translation_tool.core.guide_blocks import extract_guide_blocks

    text = "# T\n\nFirst paragraph.\n\n---\n\nSecond paragraph.\n\nThird one.\n"
    assert [b.text for b in extract_guide_blocks(text, "a.md")][1:] == [
        "First paragraph.",
        "Second paragraph.",
        "Third one.",
    ]


def test_readme_with_a_suffix_is_a_project_doc():
    from translation_tool.core.text_sniffer import is_project_doc_name

    assert is_project_doc_name("x/examples/README_Browser_Actions.md")
    assert not is_project_doc_name("x/guide/notice_board.md")


def test_lang_file_with_uppercase_name_loses_the_tie_to_the_lowercase_one(tmp_path):
    jar = _jar(
        tmp_path / "m.jar",
        {
            "assets/m/lang/en_US.json": '{"a": "Fewer items"}',
            "assets/m/lang/en_us.json": '{"a": "Fewer items", "b": "More items"}',
        },
    )
    result = scan_jar(jar, ScanOptions(version="1.21.1"))
    assert sorted(i.key for i in result.items) == ["a", "b"]


def test_structure_data_code_and_comma_name_lists_are_not_text():
    from translation_tool.core.guide_blocks import non_text_reason

    layout = (
        "\n".join(
            f"<block><iceandfire:dragon_bone_block 0 {i} 38 1>" for i in range(10)
        )
        + "\nLayer 1"
    )
    pools = "Normal: " + ", ".join(
        [
            "Rattata",
            "Raticate",
            "Meowth",
            "Persian",
            "Pikachu",
            "Raichu",
            "Sandshrew",
            "Sandslash",
        ]
    )
    lua = (
        "Functions in the HTTP API:\nhttp.checkURL( url )\nhttp.request( url, post )\n"
        "http.get( url )\nhttp.post( url, data )"
    )
    assert non_text_reason(layout) == "data"
    assert non_text_reason(pools) == "name_list"
    assert non_text_reason(lua) == "code"
    table = "| Layers | Range |\n|---|---|\n| 1 | 30 |\n| 2 | 50 |\n| 3 | 70 |"
    assert non_text_reason(table) is None
    sentence = "We mine Iron, Gold, Copper, Tin, Lead, Silver, Zinc, Nickel, Cobalt."
    assert non_text_reason(sentence) is None
