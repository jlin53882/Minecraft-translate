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
