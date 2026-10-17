import pytest

from app.services_impl.moddb_translate_service import build_items
from translation_tool.core.guide_format import guide_issues, guide_marks
from translation_tool.core.guide_translation import (
    GuidePath,
    finish_guide_item,
    load_guide,
)
from translation_tool.translation_db.schema import KIND_GUIDE, cache_type_for_kind

ENGINEER = (
    "Electromagnet\nUnnaturally attractive\n"
    "<&recipe>The Electromagnet is a large coil.<br>\n"
    "This field attracts nearby items.<br>\n"
    "The magnet runs continuously."
)


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_guide_items_use_md_cache_bucket_for_both_supported_suffixes(tmp_path, suffix):
    path = GuidePath(tmp_path / f"entry{suffix}")
    path.write_text("A translatable guide paragraph.\n", encoding="utf-8")
    path.guide_root = tmp_path
    _doc, items = load_guide(path)
    assert items and items[0]["cache_type"] == cache_type_for_kind(KIND_GUIDE)


def test_moddb_and_guide_producers_share_the_guide_cache_route(tmp_path):
    path = GuidePath(tmp_path / "entry.md")
    path.write_text("A translatable guide paragraph.\n", encoding="utf-8")
    path.guide_root = tmp_path
    _doc, guide_items = load_guide(path)
    moddb_items = build_items([(7, KIND_GUIDE, "foo", "entry.md#0", "Guide")])

    assert guide_items and moddb_items
    assert guide_items[0]["cache_type"] == moddb_items[0]["cache_type"]


def test_identical_marks_have_no_issues():
    assert (
        guide_issues(
            "## Heading\n\nUse **bold** and `code`.",
            "## 標題\n\n使用 **粗體** 和 `code`。",
        )
        == []
    )


def test_missing_heading_prefix_is_reported():
    assert guide_issues("## Advanced Devices", "進階裝置") == [
        "少了 1 個「標題前綴 ##」"
    ]


def test_heading_level_change_is_reported():
    issues = guide_issues("## A heading", "# 標題")
    assert "少了 1 個「標題前綴 ##」" in issues
    assert "多了 1 個「標題前綴 #」" in issues


def test_dropped_br_tags_from_the_pasted_engineer_page_are_reported():
    merged = (
        "電磁鐵\n非自然吸引力\n<&recipe> 電磁鐵是大型線圈。\n"
        "該磁場能吸附物品。\n磁鐵持續執行。"
    )
    assert guide_issues(ENGINEER, merged) == ["少了 2 個「標籤 <br>」"]


def test_bold_code_list_quote_and_link_targets_are_counted():
    marks = guide_marks("- a\n1. b\n> q\n**x** `y` [t](ae2:z.md)")
    assert marks[("bullet", "-")] == 1
    assert marks[("ordered", "1.")] == 1
    assert marks[("quote", ">")] == 1
    assert marks[("bold", "**")] == 2
    assert marks[("code", "`")] == 2
    assert marks[("link", "ae2:z.md")] == 1


def test_changed_link_target_is_reported_as_missing_and_extra():
    issues = guide_issues("[t](ae2:a.md)", "[t](ae2:b.md)")
    assert len(issues) == 2


def test_lm_finish_keeps_source_when_a_heading_prefix_is_lost():
    item = {"file": "x", "text": "進階裝置", "source_text": "## Advanced Devices"}
    from translation_tool.plugins.shared.rich_text_shield import shield_text

    item["_shielded"] = shield_text(item["source_text"])
    finish_guide_item(item)
    assert item["text"] == "## Advanced Devices"
    assert item["_untranslated"] is True


def test_lm_finish_accepts_a_translation_with_all_marks():
    from translation_tool.plugins.shared.rich_text_shield import shield_text

    source = "## Advanced Devices"
    item = {"file": "x", "text": "## 進階裝置", "source_text": source}
    item["_shielded"] = shield_text(source)
    finish_guide_item(item)
    assert item["text"] == "## 進階裝置"
    assert "_untranslated" not in item


def test_extra_translated_lines_are_merged_instead_of_dropped():
    from translation_tool.core.guide_translation import render_guide
    from translation_tool.plugins.shared.rich_text_shield import shield_text

    source = "The machine consumes energy."
    item = {
        "file": "x",
        "text": "這台機器需要\n消耗能量。",
        "source_text": source,
    }
    item["_shielded"] = shield_text(source)
    finish_guide_item(item)
    assert item["text"] == "這台機器需要 消耗能量。"
    assert "_untranslated" not in item

    doc = {
        "source": f"# T\n\n{source}\n",
        "ranges": [(3, 3)],
        "prefixes": [""],
        "originals": [source],
        "blocks": [item["text"]],
    }
    assert "消耗能量。" in render_guide(doc)


def test_translation_with_fewer_or_equal_lines_is_unchanged():
    from translation_tool.core.guide_translation import fit_to_source_lines

    assert fit_to_source_lines("a b\nc d", "甲\n乙") == "甲\n乙"
    assert fit_to_source_lines("a b\nc d", "甲乙") == "甲乙"
    assert fit_to_source_lines("a\nb\nc", "1\n2\n3\n4") == "1\n2\n3 4"
