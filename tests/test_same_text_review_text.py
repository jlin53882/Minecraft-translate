"""同文一致性審核的譯文呈現：不可見字元標記、差異說明、成員分組與勾選狀態（純邏輯）。"""

from __future__ import annotations

from app.views.moddb.same_text_review_text import (
    bucket_members,
    default_selection,
    describe_char,
    difference_notes,
    difference_tooltip,
    format_fullwidth_chars,
    invisible_labels,
    marked_indexes,
    plan_segments,
    selection_state,
    text_control,
)
from translation_tool.translation_db.same_text_review import SameTextMember

NBSP = chr(0xA0)
IDEOGRAPHIC = chr(0x3000)
ZWSP = chr(0x200B)
FULL_PERCENT = chr(0xFF05)
FULL_COLON = chr(0xFF1A)


def _member(eid, text):
    return SameTextMember(eid, "lang", "foo", f"k{eid}", "Close", text, 6, False, 1)


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
    assert difference_notes("%s ", "%s") == ["多出空白"]
    assert difference_notes("%s", "%s ") == ["缺少空白"]
    assert difference_notes("a" + NBSP + "b", "a b") == [
        "多出NBSP（不換行空白）",
        "缺少空白",
    ]


def test_fullwidth_versus_halfwidth_is_explained_both_ways():
    assert difference_notes(f"{FULL_PERCENT}s", "%s") == [
        "全形「％」（統一譯文是半形「%」）"
    ]
    assert difference_notes("%s", f"{FULL_PERCENT}s") == [
        "半形「%」（統一譯文是全形「％」）"
    ]
    assert difference_notes(f"名稱{FULL_COLON}", "名稱:") == [
        "全形「：」（統一譯文是半形「:」）"
    ]


def test_no_notes_without_base_or_difference():
    assert difference_notes("甲", None) == []
    assert difference_notes("甲", "甲") == []
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


def test_bucket_members_group_by_translation_and_put_untranslated_last():
    members = [
        _member(1, "關閉"),
        _member(2, "已停用"),
        _member(3, ""),
        _member(4, "已停用"),
        _member(5, ""),
        _member(6, ""),
        _member(7, "閉合"),
    ]
    buckets, more = bucket_members(members, "關閉", limit=100)
    assert [(b.text, b.total) for b in buckets] == [
        ("已停用", 2),
        ("閉合", 1),
        ("", 3),
    ]
    assert more == 0
    assert 1 not in {m.entry_id for b in buckets for m in b.members}  # 已等於統一譯文


def test_bucket_members_cap_keeps_totals_and_reports_hidden_count():
    members = [_member(i, "甲") for i in range(10)] + [_member(20, "乙")]
    buckets, more = bucket_members(members, "丙", limit=4)
    assert [(b.text, b.total, len(b.members)) for b in buckets] == [
        ("甲", 10, 4),
        ("乙", 1, 0),
    ]
    assert more == 7


def test_bucket_members_without_canonical_lists_everyone():
    members = [_member(1, "甲"), _member(2, "乙")]
    buckets, more = bucket_members(members, None, limit=30)
    assert sum(b.total for b in buckets) == 2 and more == 0


def test_selection_state_is_tristate():
    assert selection_state(set(), {1}) == (False, 0)
    assert selection_state({1, 2, 3}, set()) == (False, 0)
    assert selection_state({1, 2, 3}, {1, 9}) == (None, 1)
    assert selection_state({1, 2, 3}, {1, 2, 3, 9}) == (True, 3)


def test_nothing_is_selected_by_default():
    assert default_selection() == set()


def _bare_dialog():
    """不經 ``__init__``（不需要資料庫與頁面）建立只含卡片所需狀態的對話框。"""
    import flet as ft

    from app.views.moddb.same_text_review_dialog import SameTextReviewDialog
    from translation_tool.translation_db.same_text_review import (
        CanonicalCandidate,
        SameTextGroup,
    )

    members = [
        _member(1, "關閉"),
        _member(2, "閉合"),
        _member(3, ""),
        _member(4, ""),
    ]
    group = SameTextGroup(
        "g",
        "Close",
        ("lang",),
        "en_us",
        tuple(members),
        (CanonicalCandidate("關閉", 1, 1, 1, 1, True),),
    )
    dialog = SameTextReviewDialog.__new__(SameTextReviewDialog)
    dialog.groups = [group]
    dialog.selected = default_selection()
    dialog.expanded = set()
    dialog._cards = {}
    dialog.choice = {}
    dialog._catalog = {}
    dialog._confirm_overwrite = False
    dialog.include_reviewed = ft.Checkbox(value=False)
    dialog._render = lambda: None
    dialog._render_apply_button = lambda: None
    dialog._safe_update = lambda: None
    return dialog, group


def test_member_list_is_built_only_when_expanded_and_header_follows_selection():
    dialog, group = _bare_dialog()
    dialog._group_card(group)
    refs = dialog._cards["g"]
    assert refs["holder"].content is None and not refs["holder"].visible  # 預設收合
    assert refs["check"].value is False and "已勾選 0" in refs["title"].value

    dialog._toggle_expand(group)
    assert refs["holder"].content is not None and refs["holder"].visible
    dialog._toggle_expand(group)
    assert refs["holder"].content is None and "g" not in dialog.expanded

    dialog._toggle_group(group)  # 勾選整組可統一的成員（2、3、4）
    assert dialog.selected == {2, 3, 4}
    dialog._cards = {}
    dialog._group_card(group)
    assert dialog._cards["g"]["check"].value is True
    dialog.selected.discard(3)
    dialog._refresh_group_header("g")
    assert dialog._cards["g"]["check"].value is None  # 部分勾選
    dialog._toggle_group(group)  # 部分勾選時再按會補齊
    assert dialog.selected == {2, 3, 4}
    dialog._toggle_group(group)  # 全部已勾時再按取消
    assert dialog.selected == set()
