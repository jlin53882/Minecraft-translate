"""批次取代預覽與批次審核候選列的譯文呈現：差異底色與不可見字元標記，值本身不變。"""

from __future__ import annotations

import flet as ft

from app.views.moddb.batch_replace_preview import _diff_line
from app.views.moddb.bulk_review_dialog import BulkReviewDialog
from app.views.moddb.formatting import clip_text


def _text_of(control: ft.Control) -> ft.Text:
    """取出 Row 內承載譯文的 ft.Text（可能包在 Container 內）。"""
    found: list[ft.Text] = []

    def walk(node) -> None:
        if isinstance(node, ft.Text) and node.spans:
            found.append(node)
        for attr in ("content", "controls"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                for item in child:
                    walk(item)
            elif child is not None and not isinstance(child, str):
                walk(child)

    walk(control)
    assert found, "找不到使用 spans 的文字控制項"
    return found[0]


def _shown(control: ft.Control) -> str:
    return "".join(span.text for span in _text_of(control).spans)


def test_diff_line_marks_added_trailing_space_and_changed_characters():
    new_line = _diff_line("新", "鋼鐵 ", "鋼鐵", "em")
    old_line = _diff_line("舊", "鋼鐵", "鋼鐵 ", "red")
    assert _shown(new_line) == "鋼鐵·"  # 多出來的結尾空白以標記呈現
    assert _shown(old_line) == "鋼鐵"
    marks = [s for s in _text_of(new_line).spans if s.style.font_family == "monospace"]
    assert marks and marks[0].text == "·"
    assert marks[0].style.bgcolor is not None


def test_diff_line_highlights_only_the_replaced_fragment():
    old_spans = _text_of(_diff_line("舊", "舊譯文abc", "新譯文abc", "red")).spans
    new_spans = _text_of(_diff_line("新", "新譯文abc", "舊譯文abc", "em")).spans
    assert [s.text for s in old_spans if s.style.bgcolor] == ["舊"]
    assert [s.text for s in new_spans if s.style.bgcolor] == ["新"]
    assert "".join(s.text for s in old_spans) == "舊譯文abc"


def test_diff_line_unchanged_text_has_no_highlight():
    spans = _text_of(_diff_line("新", "相同", "相同", "em")).spans
    assert [s.text for s in spans] == ["相同"]
    assert all(s.style.bgcolor is None for s in spans)


def test_bulk_review_text_line_marks_trailing_space_without_diff():
    line = BulkReviewDialog._text_line("譯文", "鋼鐵 ", main=True)
    assert _shown(line) == "鋼鐵·"
    # 不是比對：標記以外的字元不加差異底色
    plain = [s for s in _text_of(line).spans if s.style.font_family != "monospace"]
    assert plain and all(s.style.bgcolor is None for s in plain)


def test_bulk_review_text_line_empty_text_shows_placeholder():
    line = BulkReviewDialog._text_line("原文", "")
    assert line.controls[1].value == "（無原文）"


def test_clip_text_truncates_without_folding_whitespace():
    assert clip_text("a  b ", 80) == "a  b "  # 不折疊空白、保留結尾空白
    long = "x" * 100
    assert clip_text(long, 80) == "x" * 79 + "…"
    assert len(clip_text(long, 80)) == 80
    assert clip_text("a\nb") == "a\nb"
