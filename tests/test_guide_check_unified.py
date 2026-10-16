"""同一譯文在各入口必須得到同一個指南結構判斷（差異預覽、寫入、修復、LM 重試共用）。"""

from __future__ import annotations

from app.services_impl.moddb_format_diff_service import compare_format_diff
from translation_tool.core.lm_format_retry import (
    build_quality_retry_batch,
    quality_issues,
)
from translation_tool.plugins.shared.rich_text_shield import shield_text
from translation_tool.translation_db.quality import (
    guide_structure_issues,
    translation_issues,
)
from translation_tool.translation_db.schema import KIND_GUIDE, KIND_LANG

SOURCE = "[Crafting](../items/crafting.md)"
BROKEN = "[合成](/../items/crafting.md)"


def test_guide_structure_rule_only_applies_to_guide_entries():
    assert guide_structure_issues(SOURCE, BROKEN, kind=KIND_GUIDE)
    assert guide_structure_issues(SOURCE, BROKEN, kind=KIND_LANG) == []
    assert guide_structure_issues(SOURCE, BROKEN, kind=None) == []


def test_translation_issues_adds_guide_rule_for_guide_entries_only():
    assert translation_issues(SOURCE, BROKEN, kind=KIND_GUIDE) != []
    assert translation_issues(SOURCE, BROKEN, kind=KIND_LANG) == []
    assert translation_issues(SOURCE, SOURCE, kind=KIND_GUIDE) == []


def test_repair_mode_keeps_its_lf_allowance_and_still_checks_guide_links():
    source = "Line one\nLine two\n[Crafting](../items/crafting.md)"
    translated = "第一行第二行 [合成](/../items/crafting.md)"
    issues = translation_issues(source, translated, kind=KIND_GUIDE, repair=True)
    assert issues and all("換行" not in issue for issue in issues)


def test_diff_panel_and_write_gate_agree_on_guide_breakage():
    result = compare_format_diff(SOURCE, BROKEN, kind=KIND_GUIDE)
    assert result.has_issues
    assert result.guide_issues == tuple(
        guide_structure_issues(SOURCE, BROKEN, kind=KIND_GUIDE)
    )
    assert compare_format_diff(SOURCE, BROKEN).has_issues is False


def test_lm_retry_candidates_include_broken_guide_links_from_db_ident():
    item = {
        "path": "guide.crafting",
        "text": SOURCE,
        "source_text": SOURCE,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }
    translated = {**item, "text": BROKEN}
    batch = build_quality_retry_batch(
        [item], [translated], ["0"], retry_same_as_source=False
    )
    assert batch.positions == (0,)
    assert batch.payload["items"][0]["format_issues"]


def test_shielded_guide_placeholders_do_not_create_false_issues():
    """行內保護的佔位符還原後應與原文一致，不能因為佔位符本身被判成問題。"""
    source = "&aUse #minecraft:stone here [A](../x.md)"
    shielded = shield_text(source)
    assert shielded.clean != source
    item = {
        "path": "blocks[0]",
        "text": shielded.clean,
        "source_text": source,
        "_shielded": shielded,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }
    assert quality_issues(item, shielded.clean) == []


def test_shielded_guide_link_breakage_is_still_caught_after_unshielding():
    source = "&aUse #minecraft:stone here [A](../x.md)"
    shielded = shield_text(source)
    broken = shielded.clean.replace("](../x.md)", "](/../x.md)")
    assert broken != shielded.clean
    item = {
        "path": "blocks[0]",
        "text": shielded.clean,
        "source_text": source,
        "_shielded": shielded,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }
    assert quality_issues(item, broken)
