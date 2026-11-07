"""版本差異審核的文字呈現：差異區間與格式碼片段。"""

from __future__ import annotations

from app.views.moddb.version_diff_text import changed_ranges, text_spans


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


def test_completely_different_text_is_not_marked():
    assert changed_ranges("黃銅外殼", "完全不一樣的另一段很長的說明文字") == ([], [])


def test_spans_keep_the_original_text_and_isolate_format_codes():
    text = "&l&n破壞詛咒&r&N這是 %s 個&1附魔&0"
    spans = text_spans(text, [(4, 6)], "#ff0")  # 「破壞」
    assert "".join(span.text for span in spans) == text
    codes = [span.text for span in spans if span.style.font_family == "monospace"]
    assert "&l&n" in codes and "&r&N" in codes and "%s" in codes
    assert any(span.style.bgcolor == "#ff0" for span in spans)
