"""app/ui/md_text.py：指南文字（Markdown／含 ``<br>`` 標籤的文字）轉成 Flet 的文字片段。

純函式、不依賴 Page。只做預覽用的近似：標題加粗放大、粗體、斜體、行內程式碼、連結文字、
清單與引用前綴、``<br>`` 換行；元件標籤（``<ItemLink …/>``）用淡色原樣顯示。
不等於遊戲內 GuideME 實際畫出來的樣子。
"""

from __future__ import annotations

import re

import flet as ft

_HEADING = re.compile(r"^(\s{0,3})(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^(\s*)>\s?(.*)$")
_INLINE = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<code>[^`\n]+)`"
    r"|\[(?P<link>[^\]\n]+)\]\((?P<target>[^)\n]*)\)"
    r"|(?<![*\w])\*(?P<italic>[^*\n]+)\*(?![*\w])"
    r"|(?P<br><br\s*/?>)"
    r"|(?P<tag><[^<>\n]+>)"
)
_LINK_COLOR = "#1F5FA8"
_CODE_BG = "#E6D9B3"
_TAG_COLOR = "#8A7F66"
_QUOTE_COLOR = "#6B6252"


def _span(text: str, color: str | None, size: float, **style) -> ft.TextSpan:
    return ft.TextSpan(text, style=ft.TextStyle(size=size, color=color, **style))


def _inline_spans(
    text: str, color: str | None, size: float, *, bold: bool = False
) -> list[ft.TextSpan]:
    base_weight = ft.FontWeight.BOLD if bold else None
    spans: list[ft.TextSpan] = []
    pos = 0
    for match in _INLINE.finditer(text):
        if match.start() > pos:
            spans.append(
                _span(text[pos : match.start()], color, size, weight=base_weight)
            )
        pos = match.end()
        if match.group("bold") is not None:
            spans.append(
                _span(match.group("bold"), color, size, weight=ft.FontWeight.BOLD)
            )
        elif match.group("code") is not None:
            spans.append(
                _span(
                    match.group("code"),
                    color,
                    size,
                    font_family="Consolas",
                    bgcolor=_CODE_BG,
                )
            )
        elif match.group("link") is not None:
            spans.append(
                _span(
                    match.group("link"),
                    _LINK_COLOR,
                    size,
                    weight=base_weight,
                    decoration=ft.TextDecoration.UNDERLINE,
                )
            )
        elif match.group("italic") is not None:
            spans.append(
                _span(
                    match.group("italic"), color, size, weight=base_weight, italic=True
                )
            )
        elif match.group("br") is not None:
            spans.append(_span("\n", color, size))
        else:
            spans.append(_span(match.group("tag"), _TAG_COLOR, size - 1))
    if pos < len(text):
        spans.append(_span(text[pos:], color, size, weight=base_weight))
    return spans


def _line_spans(line: str, color: str | None, size: float) -> list[ft.TextSpan]:
    heading = _HEADING.match(line)
    if heading:
        level = len(heading.group(2))
        return _inline_spans(
            heading.group(3), color, size + max(7 - level * 1.5, 0), bold=True
        )
    bullet = _BULLET.match(line)
    if bullet:
        return [
            _span(f"{bullet.group(1)}• ", color, size),
            *_inline_spans(bullet.group(2), color, size),
        ]
    ordered = _ORDERED.match(line)
    if ordered:
        return [
            _span(f"{ordered.group(1)}{ordered.group(2)} ", color, size),
            *_inline_spans(ordered.group(3), color, size),
        ]
    quote = _QUOTE.match(line)
    if quote:
        return [
            _span("▎ ", _QUOTE_COLOR, size),
            *_inline_spans(quote.group(2), _QUOTE_COLOR, size),
        ]
    return _inline_spans(line, color, size)


def md_text_spans(
    text: str, default_color: str | None = None, size: float = 14
) -> list[ft.TextSpan]:
    """把指南文字轉成 ``ft.TextSpan`` 列表（逐行處理，行與行之間補換行）。"""
    spans: list[ft.TextSpan] = []
    for index, line in enumerate((text or "").split("\n")):
        if index:
            spans.append(_span("\n", default_color, size))
        spans.extend(_line_spans(line, default_color, size))
    return spans
