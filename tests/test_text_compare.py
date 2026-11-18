"""可比對譯文呈現（``text_compare``）：不可見字元標記、字元級差異、格式碼淡化與說明控制項。"""

from __future__ import annotations

import flet as ft

from app.views.moddb.text_compare import (
    changed_ranges,
    compare_chips,
    compare_notes,
    describe_char,
    difference_tooltip,
    format_fullwidth_chars,
    invisible_labels,
    marked_indexes,
    plan_segments,
    text_control,
)

NBSP = chr(0xA0)
IDEOGRAPHIC = chr(0x3000)
ZWSP = chr(0x200B)
FULL_PERCENT = chr(0xFF05)
FULL_COLON = chr(0xFF1A)


def _span_text(control: ft.Text) -> str:
    return "".join(span.text for span in control.spans)


def _shown(text, base=None):
    return "".join(s.text for s in plan_segments(text, base))


def test_ordinary_text_is_untouched_and_single_inner_space_is_not_marked():
    assert _shown("打開 %s 個") == "打開 %s 個"
    assert marked_indexes("a b") == set()


def test_leading_trailing_and_double_spaces_are_marked():
    assert _shown(" %s") == "·%s"
    assert _shown("%s ") == "%s·"
    assert _shown("a  b") == "a··b"
    assert invisible_labels(" a  b ") == ["開頭空白", "連續空白", "結尾空白"]


def test_special_whitespace_and_zero_width_characters_get_distinct_glyphs():
    text = f"a{NBSP}b{IDEOGRAPHIC}c{ZWSP}d\te"
    assert _shown(text) == "a⍽b▭c▯d⇥e"
    assert invisible_labels(text) == [
        "NBSP（不換行空白）",
        "全形空白",
        "零寬字元",
        "Tab",
    ]


def test_newline_keeps_the_line_break_after_its_marker():
    assert _shown("a\nb") == "a⏎\nb"
    assert "".join(s.text for s in plan_segments("a\nb")).replace("⏎", "") == "a\nb"


def test_identical_looking_candidates_differ_in_marks_and_notes():
    # "%s" 與 "%s "（結尾空白）：肉眼相同，標記與差異說明要指出
    assert _shown("%s ", "%s") == "%s·"
    assert compare_notes("%s ", "%s") == ["多出空白"]
    assert compare_notes("%s", "%s ") == ["缺少空白"]
    assert compare_notes("a" + NBSP + "b", "a b") == [
        "多出NBSP（不換行空白）",
        "缺少空白",
    ]


def test_fullwidth_versus_halfwidth_is_explained_both_ways():
    assert compare_notes(f"{FULL_PERCENT}s", "%s") == [
        "全形「％」（統一譯文是半形「%」）"
    ]
    assert compare_notes("%s", f"{FULL_PERCENT}s") == [
        "半形「%」（統一譯文是全形「％」）"
    ]
    assert compare_notes(f"名稱{FULL_COLON}", "名稱:") == [
        "全形「：」（統一譯文是半形「:」）"
    ]


def test_no_notes_without_base_or_difference():
    assert compare_notes("甲", None) == []
    assert compare_notes("甲", "甲") == []
    assert difference_tooltip("甲", "甲") is None


def test_diff_segments_mark_changed_characters_even_for_short_text():
    changed = [s.text for s in plan_segments(f"{FULL_PERCENT}s", "%s") if s.changed]
    assert changed == [FULL_PERCENT]
    assert not any(s.changed for s in plan_segments("%s", None))
    assert not any(s.changed for s in plan_segments("%s", "%s"))


def test_tooltip_lists_code_points_of_differing_and_invisible_characters():
    tip = difference_tooltip(f"{FULL_PERCENT}s", "%s")
    assert tip is not None and "U+FF05 FULLWIDTH PERCENT SIGN" in tip
    tip = difference_tooltip("a" + NBSP, None)
    assert tip is not None and "U+00A0" in tip
    assert describe_char(FULL_COLON).startswith("U+FF1A")


def test_fullwidth_format_characters_are_flagged_but_chinese_punctuation_is_not():
    assert format_fullwidth_chars(f"{FULL_PERCENT}s 個") == [FULL_PERCENT]
    assert format_fullwidth_chars(f"你好{FULL_COLON}世界" + chr(0xFF0C)) == []


def test_text_control_keeps_the_original_text_apart_from_markers():
    control = text_control("a b ", "a b")
    assert "".join(span.text for span in control.spans) == "a b·"
    assert control.tooltip


def test_identical_or_empty_text_has_no_marks():
    assert changed_ranges("同樣的譯文", "同樣的譯文") == ([], [])
    assert changed_ranges("", "有譯文") == ([], [])


def test_changed_ranges_mark_only_the_differing_fragments():
    a = "這個附魔有三個階段，看起來它的效果似乎完全與耐久附魔相反。"
    b = "該魔咒有三個等級，它的作用似乎與耐久魔咒完全相反。"
    left, right = changed_ranges(a, b)
    assert left and right
    assert all(0 <= s < e <= len(a) for s, e in left)
    assert all(0 <= s < e <= len(b) for s, e in right)


def test_completely_different_long_text_is_not_marked():
    assert changed_ranges("黃銅外殼", "完全不一樣的另一段很長的說明文字") == ([], [])
    long_a, long_b = "甲" * 30, "乙" * 30
    assert not any(s.changed for s in plan_segments(long_a, long_b))


def test_short_text_is_marked_even_when_entirely_different():
    assert [s.text for s in plan_segments("甲乙", "丙丁") if s.changed] == ["甲乙"]


def test_text_control_isolates_format_codes_and_keeps_text():
    text = "&l&n破壞詛咒&r&N這是 %s 個&1附魔&0"
    control = text_control(text, None)
    assert _span_text(control) == text
    codes = [s.text for s in control.spans if s.style.font_family == "monospace"]
    assert "&l&n" in codes and "&r&N" in codes and "%s" in codes
    plain = text_control(text, None, code_dim=False)
    assert not any(s.style.font_family == "monospace" for s in plain.spans)


def test_text_control_uses_custom_highlight_and_limits_lines():
    control = text_control("開啟 %s", "開起 %s", highlight="#ff0", max_lines=2)
    assert any(s.style.bgcolor == "#ff0" for s in control.spans)
    assert control.max_lines == 2 and control.selectable


def test_version_diff_target_marks_leading_space_against_reference():
    control = text_control(" 打開箱子", "打開箱子")
    assert _span_text(control) == "·打開箱子"
    assert control.spans[0].text == "·"
    assert "U+0020" in (control.tooltip or "")


def test_text_control_never_alters_the_input_string():
    text = f" a{NBSP}b\n{FULL_PERCENT}s "
    original = str(text)
    text_control(text, "a b\n%s")
    assert text == original


def test_compare_chips_empty_when_identical_and_plain():
    assert compare_chips("打開 %s", "打開 %s") == []
    assert compare_chips("打開 %s") == []


def test_compare_chips_report_invisible_fullwidth_and_difference():
    controls = compare_chips(f"%s {FULL_PERCENT}s ", "%s %s", base_label="目標")
    assert len(controls) == 2
    chips = controls[0].controls
    assert len(chips) == 2
    assert controls[1].value.startswith("差異：") and "目標" in controls[1].value


def test_compare_chips_lead_controls_come_first():
    lead = ft.Text("來源")
    controls = compare_chips("甲", None, lead=[lead])
    assert controls[0].controls == [lead]
