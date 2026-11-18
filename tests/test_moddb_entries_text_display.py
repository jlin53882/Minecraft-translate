"""條目校對右側建議與異動歷史的譯文呈現：標出與目前譯文的差異，但套用值維持原字串。"""

from __future__ import annotations

from types import SimpleNamespace as NS

import flet as ft

from app.ui.design import C
from app.views.moddb.history_renderer import _history_event, _translation_row
from app.views.moddb.suggestions import build_suggestions


def _walk(control):
    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if isinstance(child, list):
            for item in child:
                yield from _walk(item)
        elif child is not None and not isinstance(child, (str, int, float)):
            yield from _walk(child)


def _span_texts(control) -> list[ft.Text]:
    return [n for n in _walk(control) if isinstance(n, ft.Text) and n.spans]


def _has_diff(text: ft.Text) -> bool:
    """有差異底色：一般字元的紅底，或被標記的不可見字元的加深底色（0.35）。"""
    diff_colors = {
        ft.Colors.with_opacity(0.35, C.RED),
        ft.Colors.with_opacity(0.35, C.GOLD),
    }
    return any(s.style.bgcolor in diff_colors for s in text.spans)


def _plain(text: ft.Text) -> str:
    return "".join(s.text for s in text.spans)


def _apply_buttons(control):
    return [n for n in _walk(control) if getattr(n, "content", None) == "套用"]


def _detail(current="分配間隔 [ticks]"):
    return NS(
        entry=NS(zh_tw=current, en_us="Distribution Interval"),
        same_key=[
            NS(
                mc_version="1.20.1",
                source=3,
                zh_tw="分配間隔 [ticks] ",  # 結尾多一個空白
                same_text=True,
                en_us="Distribution Interval",
            )
        ],
        same_text=[
            NS(
                kind="lang",
                mod_id="foo",
                key="item.foo.a",
                mc_version="1.20.1",
                zh_tw="分配間隔［ticks］",  # 全形括號
            )
        ],
    )


def test_same_key_candidate_marks_invisible_space_and_keeps_apply_value():
    applied: list[str] = []
    detail = _detail()
    item = build_suggestions(detail, "key", applied.append)[0]

    candidate = _span_texts(item)[0]
    assert _plain(candidate).endswith("·")  # 結尾空白顯示成標記
    assert _has_diff(candidate)  # 與目前譯文的差異有底色
    buttons = _apply_buttons(item)
    assert len(buttons) == 1
    buttons[0].on_click(None)
    assert applied == ["分配間隔 [ticks] "]  # 套用的是原字串，不含標記


def test_same_key_candidate_has_difference_chips_against_current_translation():
    item = build_suggestions(_detail(), "key", lambda _t: None)[0]
    texts = [n.value for n in _walk(item) if isinstance(n, ft.Text) and n.value]
    assert any(t.startswith("差異：") and "目前譯文" not in t for t in texts)
    assert any("多出空白" in t or "結尾" in t for t in texts)


def test_same_text_candidate_diffs_against_current_translation():
    applied: list[str] = []
    detail = _detail()
    item = build_suggestions(detail, "text", applied.append)[0]
    assert _has_diff(_span_texts(item)[0])
    texts = [n.value for n in _walk(item) if isinstance(n, ft.Text) and n.value]
    assert any(t.startswith("差異：") and "半形" in t for t in texts)
    _apply_buttons(item)[0].on_click(None)
    assert applied == ["分配間隔［ticks］"]


def test_source_line_only_marks_invisible_characters():
    detail = _detail()
    detail.same_key[0].en_us = "Distribution Interval"  # NBSP
    detail.same_key[0].same_text = False
    item = build_suggestions(detail, "key", lambda _t: None)[0]
    source = next(t for t in _span_texts(item) if _plain(t).startswith("原文："))
    assert "⍽" in _plain(source)
    assert not _has_diff(source)


def _event(**kw):
    base = {
        "id": 1,
        "batch": "b",
        "at": "2026-10-07 10:00:00",
        "action": "manual",
        "source_id": None,
        "actor": "me",
        "old_zh_tw": "分配間隔",
        "new_zh_tw": "分配距離",
        "note": "",
        "prev_manual": None,
    }
    base.update(kw)
    return NS(**base)


def test_history_event_diffs_old_and_new_with_prefix_outside_spans():
    column = _history_event(NS(), _event(), {})
    spans = _span_texts(column)
    assert len(spans) == 2
    assert all(_has_diff(t) for t in spans)  # 兩邊都標差異
    labels = [n.value for n in _walk(column) if isinstance(n, ft.Text) and n.value]
    assert "原譯：" in labels and "新譯：" in labels
    assert all(not _plain(t).startswith(("原譯", "新譯")) for t in spans)


def test_history_event_without_old_text_only_marks_invisible():
    column = _history_event(NS(), _event(old_zh_tw=None, new_zh_tw=" 新譯"), {})
    (text,) = _span_texts(column)
    assert _plain(text).startswith("·")
    assert not _has_diff(text)


def test_translation_row_shows_markers_without_diff():
    row = NS(
        source=1,
        revision=1,
        review_status=None,
        created_at="2026-10-07 10:00:00",
        updated_at="2026-10-07 10:00:00",
        zh_tw="譯文 ",
    )
    control = _translation_row(row, {}, [])
    (text,) = _span_texts(control)
    assert _plain(text) == "譯文·" and not _has_diff(text)
