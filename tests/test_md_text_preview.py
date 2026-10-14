import flet as ft

from app.ui.md_text import md_text_spans


def _joined(spans):
    return "".join(span.text or "" for span in spans)


def _span_of(spans, text):
    return next(span for span in spans if span.text == text)


def test_heading_is_bold_larger_and_marker_is_hidden():
    spans = md_text_spans("## Advanced Devices", None, 14)
    assert _joined(spans) == "Advanced Devices"
    style = spans[0].style
    assert style.weight == ft.FontWeight.BOLD
    assert style.size > 14


def test_bold_italic_code_and_link_text_are_rendered_without_markers():
    spans = md_text_spans(
        "Use **bold**, *soft*, `code` and [a link](ae2:x.md).", None, 14
    )
    assert _joined(spans) == "Use bold, soft, code and a link."
    assert _span_of(spans, "bold").style.weight == ft.FontWeight.BOLD
    assert _span_of(spans, "soft").style.italic
    assert _span_of(spans, "code").style.font_family == "Consolas"
    assert _span_of(spans, "a link").style.decoration == ft.TextDecoration.UNDERLINE


def test_br_becomes_newline_and_component_tags_stay_visible_but_dimmed():
    spans = md_text_spans('One<br>Two <ItemLink id="x" />', None, 14)
    assert _joined(spans) == 'One\nTwo <ItemLink id="x" />'
    assert _span_of(spans, '<ItemLink id="x" />').style.size < 14


def test_list_and_quote_prefixes_are_replaced():
    assert (
        _joined(md_text_spans("- one\n1. two\n> three", None, 14))
        == "• one\n1. two\n▎ three"
    )


def test_a_lone_asterisk_list_marker_is_not_italic():
    assert _joined(md_text_spans("* item", None, 14)) == "• item"
