"""KubeJS 腳本文字的自訂標記（例如 <imp>、<r>、<item>）必須依數量保留。"""

from __future__ import annotations

from translation_tool.core.format_diagnostics import analyze_format_diagnostics
from translation_tool.translation_db.quality import translation_issues


def test_dropped_markup_tag_is_reported_for_kubejs_script():
    issues = translation_issues(
        "<imp>Download<r> the mod", "<imp>下載模組", kind="kubejs_script"
    )
    assert issues == ["少了 1 個「<r>」"]


def test_translated_or_renamed_tag_is_reported_for_kubejs_script():
    issues = translation_issues(
        "<item>Drill<r> dyed", "<物品>鑽頭<r> 染色", kind="kubejs_script"
    )
    assert issues == ["少了 1 個「<item>」"]


def test_matching_markup_tags_pass_for_kubejs_script():
    assert (
        translation_issues(
            "<item>Drill<r> dyed", "<item>鑽頭<r> 染色", kind="kubejs_script"
        )
        == []
    )


def test_extra_markup_tag_is_reported():
    issues = translation_issues("Drill dyed", "<r>鑽頭染色", kind="kubejs_script")
    assert issues == ["多了 1 個「<r>」"]


def test_markup_check_is_scoped_to_kubejs_script():
    # 其他 kind 的行為不變：自訂標記不影響 lang 的既有檢查結果
    assert translation_issues("<item>Drill<r>", "<物品>鑽頭", kind="lang") == []


def test_editor_hint_path_reports_the_same_markup_issue():
    result = analyze_format_diagnostics(
        "<imp>Download<r>", "<imp>下載", kind="kubejs_script"
    )
    assert result.has_issues
    assert any(item.category == "markup" for item in result.diagnostics)


def test_placeholders_still_checked_alongside_markup_tags():
    issues = translation_issues(
        "<imp>Level %s<r>", "<imp>等級<r>", kind="kubejs_script"
    )
    assert "少了 1 個「%s」" in issues


def test_same_tag_order_passes():
    assert (
        translation_issues("<imp>A<r> B", "<imp>甲<r> 乙", kind="kubejs_script") == []
    )


def test_swapped_tag_order_is_reported():
    issues = translation_issues(
        "<imp>A<r> <item>B<r>", "<item>乙<r> <imp>甲<r>", kind="kubejs_script"
    )
    assert issues == ["標記順序與原文不同"]


def test_order_is_not_reported_when_tags_are_also_missing():
    # 數量已經不同時只回報數量問題，避免同一個錯誤被重複計算
    issues = translation_issues(
        "<imp>A<r> <item>B<r>", "<imp>甲<r>", kind="kubejs_script"
    )
    assert "標記順序與原文不同" not in issues
    assert "少了 1 個「<item>」" in issues


def test_order_diagnostic_has_its_own_action_wording():
    from translation_tool.core.format_diagnostics import diagnostic_action

    result = analyze_format_diagnostics(
        "<imp>A<r> <item>B<r>", "<item>乙<r> <imp>甲<r>", kind="kubejs_script"
    )
    order = [d for d in result.diagnostics if d.category == "markup_order"]
    assert len(order) == 1
    assert diagnostic_action(order[0]) == "順序不同"
    assert order[0].summary == "標記順序與原文不同"


def test_order_check_is_scoped_to_kubejs_script():
    assert (
        translation_issues(
            "<imp>A<r> <item>B<r>", "<item>乙<r> <imp>甲<r>", kind="lang"
        )
        == []
    )
