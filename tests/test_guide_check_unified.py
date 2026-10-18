"""同一譯文在各入口必須得到同一個指南結構判斷（差異預覽、寫入、修復、LM 重試共用）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services_impl.moddb_format_diff_service import compare_format_diff
from app.services_impl.moddb_retranslate_service import (
    _filter_newline_only_repair_entries,
)
from translation_tool.core.lm_format_policy import build_translation_payload
from translation_tool.core.lm_format_retry import (
    build_quality_retry_batch,
    quality_issues,
)
from translation_tool.plugins.shared import rich_text_shield
from translation_tool.plugins.shared.rich_text_shield import (
    SHIELD_PLACEHOLDER_PATTERN,
    shield_text,
)
from translation_tool.translation_db.quality import (
    guide_structure_issues,
    repair_input_issues_for_kind,
    translation_issues,
)
from translation_tool.translation_db.schema import (
    KIND_ADVANCEMENT,
    KIND_GUIDE,
    KIND_LANG,
    KIND_PATCHOULI,
)

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


def test_soft_paragraph_line_reflow_is_valid_for_guide_and_patchouli():
    source = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight"
    translated = "一二\n三四\n五六\n七\n八"

    assert translation_issues(source, translated, kind=KIND_GUIDE) == []
    assert translation_issues(source, translated, kind=KIND_PATCHOULI) == []


def test_sentence_boundary_newline_can_be_preserved_for_guide_and_patchouli():
    source = "First sentence.\nSecond sentence."
    translated = "第一個句子。\n第二個句子。"

    for kind in (KIND_GUIDE, KIND_PATCHOULI):
        assert translation_issues(source, translated, kind=kind) == []


def test_sentence_internal_soft_line_reflow_can_be_merged():
    source = "This sentence is split\nin the middle."
    translated = "這個句子在中間合併。"

    for kind in (KIND_GUIDE, KIND_PATCHOULI):
        assert translation_issues(source, translated, kind=kind) == []


def test_hard_structure_issues_are_precise_and_patchouli_uses_them():
    source = '# Title\n\n<ItemLink id="example:item" />\n\nText<br>'
    broken = '# 標題\n<ItemLink id="example:other" />\nText'

    issues = translation_issues(source, broken, kind=KIND_PATCHOULI)

    assert any("段落分隔空行" in issue for issue in issues)
    assert any("ItemLink" in issue for issue in issues)
    assert any("<br>" in issue for issue in issues)


def test_normal_and_retry_payloads_share_patchouli_format_spec():
    item = {
        "text": '# Title\n\n<ItemLink id="example:item" />',
        "source_text": '# Title\n\n<ItemLink id="example:item" />',
        "cache_type": "patchouli",
    }

    payload = build_translation_payload([item])
    retry = build_quality_retry_batch(
        [item], [{**item, "text": "# 標題"}], ["0"], retry_same_as_source=False
    )

    assert payload["items"][0]["format_spec"]
    assert (
        retry.payload["items"][0]["format_spec"] == payload["items"][0]["format_spec"]
    )


def test_empty_guide_translation_keeps_missing_structure_visible():
    assert guide_structure_issues(SOURCE, "", kind=KIND_GUIDE)
    assert translation_issues(SOURCE, "", kind=KIND_GUIDE)


def test_repair_mode_keeps_its_lf_allowance_and_still_checks_guide_links():
    source = "Line one\nLine two\n[Crafting](../items/crafting.md)"
    translated = "第一行第二行 [合成](/../items/crafting.md)"
    issues = translation_issues(source, translated, kind=KIND_GUIDE, repair=True)
    assert issues and all("換行" not in issue for issue in issues)


def test_repair_filter_keeps_mixed_guide_mismatch_for_ai_repair():
    entry = SimpleNamespace(
        kind=KIND_GUIDE,
        en_us="[Crafting](../items/crafting.md)\nNext",
        current_ai_translation="[合成](/../items/crafting.md)",
    )

    selected, skipped = _filter_newline_only_repair_entries([entry])

    assert selected == [entry]
    assert skipped == 0


def test_soft_line_reflow_is_not_a_quality_repair_candidate():
    for kind in (KIND_GUIDE, KIND_PATCHOULI):
        eligible, issues, mixed = repair_input_issues_for_kind(
            "one\ntwo\nthree", "一二\n三", kind=kind
        )
        assert (eligible, issues, mixed) == (False, (), False)


def test_lang_keeps_actual_newline_count_as_a_hard_quality_token():
    assert "換行" in " ".join(translation_issues("one\ntwo", "一二", kind=KIND_LANG))


def test_advancement_keeps_actual_newline_count_when_using_patchouli_cache():
    item = {
        "text": "one\ntwo",
        "source_text": "one\ntwo",
        "cache_type": "patchouli",
        "_kind": KIND_ADVANCEMENT,
    }

    assert "換行" in " ".join(quality_issues(item, "一二"))


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


def test_quality_retry_detects_missing_shield_placeholders_before_unshielding():
    """品質 retry 與最終快取閘門必須使用同一套 Shield 完整性規則。"""
    source = "&aUse #minecraft:stone here"
    shielded = shield_text(source)
    item = {
        "path": "blocks[0]",
        "text": shielded.clean,
        "source_text": source,
        "_shielded": shielded,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }
    translated = {**item, "text": "使用物品"}

    issues = quality_issues(item, translated["text"])
    batch = build_quality_retry_batch(
        [item], [translated], ["0"], retry_same_as_source=False
    )

    assert any("保護格式" in issue for issue in issues)
    assert batch.positions == (0,)
    assert any(
        "保護格式" in issue for issue in batch.payload["items"][0]["format_issues"]
    )


@pytest.mark.parametrize(
    "translated_builder",
    [
        lambda shielded: shielded.clean.replace(
            shielded.shields[0].placeholder, shielded.shields[1].placeholder
        ),
        lambda shielded: shielded.clean.replace(
            shielded.shields[0].placeholder, "$P999$"
        ),
    ],
)
def test_quality_retry_rejects_shield_identity_changes(translated_builder):
    """目前 AI 回應不可遺失、重複或新增 Shield 身分。"""
    source = "Use #minecraft:stone then #minecraft:dirt"
    shielded = shield_text(source)
    item = {
        "path": "blocks[0]",
        "text": shielded.clean,
        "source_text": source,
        "_shielded": shielded,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }

    issues = quality_issues(item, translated_builder(shielded))

    assert issues
    assert any("保護格式" in issue or "placeholder" in issue for issue in issues)


def test_quality_retry_allows_exact_shield_ids_in_natural_language_order():
    """合法中文語序可以重排，但每個 Shield ID 必須完整保留。"""
    source = "You need #minecraft:stone to craft #minecraft:dirt."
    shielded = shield_text(source)
    first, second = SHIELD_PLACEHOLDER_PATTERN.findall(shielded.clean)
    item = {
        "path": "blocks[0]",
        "text": shielded.clean,
        "source_text": source,
        "_shielded": shielded,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }

    translated = f"製作 {second} 需要 {first}。"

    assert quality_issues(item, translated) == []


def test_literal_placeholder_text_is_not_used_as_shield_mapping():
    """原文中的普通 $P99$ 不得造成 Shield 驗證例外。"""
    source = "Keep $P99$ and &aHello"
    shielded = shield_text(source)
    color_placeholder = next(
        piece.placeholder for piece in shielded.shields if piece.category == "color"
    )
    item = {
        "path": "blocks[0]",
        "text": shielded.clean,
        "source_text": source,
        "_shielded": shielded,
        "db_ident": (KIND_GUIDE, "foo", "crafting.md#0"),
    }

    translated = f"保留文字，然後 {color_placeholder}你好"

    assert quality_issues(item, translated) == []


def test_shield_generated_placeholder_avoids_literal_collision(monkeypatch):
    """程式產生的 placeholder 不得覆蓋原文既有的同名文字。"""
    candidates = iter(("$C17$", "$C18$"))
    monkeypatch.setattr(
        rich_text_shield,
        "_next_color_placeholder",
        lambda: next(candidates),
    )

    shielded = rich_text_shield.shield_text("Keep $C17$ and &aHello")

    assert shielded.clean == "Keep $C17$ and $C18$Hello"
    assert [piece.placeholder for piece in shielded.shields] == ["$C18$"]
