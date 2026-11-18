"""可比對的唯讀譯文呈現：不可見字元標記、與參照譯文的字元級差異、格式碼淡化與差異說明。

同文一致性審核與版本差異審核等需要「肉眼看起來相同、其實差一個空白或全形字元」的畫面共用這裡。
只影響畫面：標記是顯示用的替代符號，只存在於 ``ft.TextSpan``，函式不會修改、也不回傳加了標記的
字串當作值，實際寫入的譯文不受影響。純邏輯部分（``plan_segments``、``compare_notes`` 等）不需要建立
畫面即可測試。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

import flet as ft

from app.ui import kit
from app.ui.design import C

# 譯文很短時（例如只有 "%s"），即使整句都不同也要標差異，不套用相似度門檻
SHORT_TEXT = 16
# 兩段較長的文字相似度低於這個值時不標差異（整句都不同，標色只會變成色塊）
MIN_RATIO = 0.5
# 任一邊超過這個字數就不做字元級比對（SequenceMatcher 成本隨長度平方成長，列表頁會大量呼叫）
MAX_DIFF_CHARS = 2000
TOOLTIP_CHARS = 8  # 提示最多列出的差異字元數

# Minecraft 色碼／格式碼（&a、&l、&r、§a…），以及 printf 風格的佔位符
_CODE_RE = re.compile(r"(?:[&§][0-9a-fk-orA-FK-OR])|(?:%(?:\d+\$)?[sdf%])")

# 連續的格式碼合併成一個片段
_CODE_RUN_RE = re.compile(f"(?:{_CODE_RE.pattern})+")
_FULLWIDTH_FIRST, _FULLWIDTH_LAST, _FULLWIDTH_OFFSET = 0xFF01, 0xFF5E, 0xFEE0
_IDEOGRAPHIC_SPACE = "\u3000"
_NBSP = "\u00a0"
_ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"

_KIND_PLAIN = "plain"
_KIND_MARK = "mark"


@dataclass(frozen=True)
class Segment:
    """顯示片段：``mark`` 是不可見字元的標記符號（``text`` 為替代符號），``plain`` 為原文字。"""

    text: str
    kind: str
    changed: bool = False


def is_fullwidth_form(char: str) -> bool:
    """全形 ASCII 變體（U+FF01～U+FF5E，例如 ％、：）。"""
    return _FULLWIDTH_FIRST <= ord(char) <= _FULLWIDTH_LAST


def halfwidth_of(char: str) -> str:
    return chr(ord(char) - _FULLWIDTH_OFFSET) if is_fullwidth_form(char) else char


def fullwidth_of(char: str) -> str:
    return chr(ord(char) + _FULLWIDTH_OFFSET) if _is_ascii_graphic(char) else char


def _is_ascii_graphic(char: str) -> bool:
    return "!" <= char <= "~"


def _is_space_like(char: str) -> bool:
    return char != " " and unicodedata.category(char) == "Zs"


def _is_invisible(char: str) -> bool:
    """不論位置都看不見（或會被誤認成空白）的字元。"""
    return (
        char in "\t\n\r"
        or char in _ZERO_WIDTH
        or _is_space_like(char)
        or unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp")
    )


def invisible_label(char: str) -> str:
    """不可見字元的簡短名稱，用在提示與差異說明。"""
    if char == " ":
        return "空白"
    if char == _NBSP:
        return "NBSP（不換行空白）"
    if char == _IDEOGRAPHIC_SPACE:
        return "全形空白"
    if char in _ZERO_WIDTH:
        return "零寬字元"
    if char == "\t":
        return "Tab"
    if char in "\n\r":
        return "換行"
    if _is_space_like(char):
        return "特殊空白"
    return "控制字元"


def marker_glyph(char: str) -> str:
    """不可見字元的替代符號（換行另外保留實際換行，由 ``plan_segments`` 處理）。"""
    if char == " ":
        return "·"
    if char == _NBSP:
        return "⍽"
    if char == _IDEOGRAPHIC_SPACE:
        return "▭"
    if char in _ZERO_WIDTH:
        return "▯"
    if char == "\t":
        return "⇥"
    if char == "\n":
        return "⏎"
    if char == "\r":
        return "␍"
    if _is_space_like(char):
        return "⍽"
    return chr(0x2400 + ord(char)) if ord(char) < 0x20 else "▯"


def marked_indexes(text: str) -> set[int]:
    """要用標記符號顯示的字元位置：不可見字元，以及前後、連續或貼著換行的普通空白。"""
    marked: set[int] = set()
    for index, char in enumerate(text):
        if char == " ":
            before = text[index - 1] if index else None
            after = text[index + 1] if index + 1 < len(text) else None
            if before in (None, " ", "\n") or after in (None, " ", "\n"):
                marked.add(index)
        elif _is_invisible(char):
            marked.add(index)
    return marked


def describe_char(char: str) -> str:
    """提示用：``U+FF05 FULLWIDTH PERCENT SIGN``。"""
    name = unicodedata.name(char, "")
    return f"U+{ord(char):04X} {name}".rstrip()


def invisible_labels(text: str) -> list[str]:
    """文字內含的不可見字元種類（依出現順序、不重複）。"""
    labels: list[str] = []
    for index in sorted(marked_indexes(text)):
        label = invisible_label(text[index])
        if label == "空白":
            label = _space_label(text, index)
        if label not in labels:
            labels.append(label)
    return labels


def _space_label(text: str, index: int) -> str:
    if index == 0 or text[index - 1] == "\n":
        return "開頭空白"
    if index == len(text) - 1 or text[index + 1] == "\n":
        return "結尾空白"
    return "連續空白"


def width_variant_chars(text: str) -> list[str]:
    """文字內的全形 ASCII 變體字元（不重複）；全形標點是中文常態，只在與參照譯文不同時才提醒。"""
    return list(dict.fromkeys(c for c in text if is_fullwidth_form(c)))


def format_fullwidth_chars(text: str) -> list[str]:
    """全形的字母、數字與佔位符常用符號（例如 ％、＆）：很可能是誤植，不同於中文標點。"""
    return [
        c
        for c in width_variant_chars(text)
        if halfwidth_of(c).isalnum() or halfwidth_of(c) in "%&$#@*+-=/\\_<>{}[]|~^'\"."
    ]


def changed_ranges(
    a: str, b: str, *, min_ratio: float = MIN_RATIO
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """回傳 ``a``、``b`` 各自與對方不同的區間；差異太大或完全相同時回傳空清單。"""
    if not a or not b or a == b:
        return [], []
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.ratio() < min_ratio:
        return [], []
    left: list[tuple[int, int]] = []
    right: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            left.append((i1, i2))
        if j2 > j1:
            right.append((j1, j2))
    return left, right


def diff_ranges(text: str, base: str | None) -> list[tuple[int, int]]:
    """``text`` 與 ``base`` 不同的區間；沒有基準、相同或太長時為空。短譯文也標差異。"""
    if not base or not text or text == base:
        return []
    if max(len(text), len(base)) > MAX_DIFF_CHARS:
        return []
    ratio = 0.0 if max(len(text), len(base)) <= SHORT_TEXT else MIN_RATIO
    return changed_ranges(text, base, min_ratio=ratio)[0]


def plan_segments(
    text: str, base: str | None = None, *, mark_invisible: bool = True
) -> list[Segment]:
    """把文字切成顯示片段：不可見字元換成標記符號，差異區間標記 ``changed``。"""
    changed = [False] * len(text)
    for start, end in diff_ranges(text, base):
        for index in range(start, min(end, len(text))):
            changed[index] = True
    marked = marked_indexes(text) if mark_invisible else set()
    segments: list[Segment] = []
    for index, char in enumerate(text):
        is_mark = index in marked
        shown = marker_glyph(char) + ("\n" if char == "\n" else "") if is_mark else char
        kind = _KIND_MARK if is_mark else _KIND_PLAIN
        if (
            segments
            and segments[-1].kind == kind
            and segments[-1].changed == changed[index]
            and not is_mark
        ):
            segments[-1] = Segment(segments[-1].text + shown, kind, changed[index])
        else:
            segments.append(Segment(shown, kind, changed[index]))
    return segments


def compare_notes(
    text: str, base: str | None, *, base_label: str = "統一譯文"
) -> list[str]:
    """說明 ``text`` 與參照譯文 ``base`` 差在哪（多出／缺少的不可見字元、全形半形互換）。"""
    if not base or not text or text == base:
        return []
    mine = [text[i] for s, e in diff_ranges(text, base) for i in range(s, e)]
    theirs = [base[i] for s, e in diff_ranges(base, text) for i in range(s, e)]
    notes: list[str] = []

    def add(note: str) -> None:
        if note not in notes:
            notes.append(note)

    for char in mine:
        if is_fullwidth_form(char) and halfwidth_of(char) in theirs:
            add(f"全形「{char}」（{base_label}是半形「{halfwidth_of(char)}」）")
        elif fullwidth_of(char) != char and fullwidth_of(char) in theirs:
            add(f"半形「{char}」（{base_label}是全形「{fullwidth_of(char)}」）")
        elif _is_invisible(char) or char == " ":
            add(f"多出{invisible_label(char)}")
    for char in theirs:
        if (_is_invisible(char) or char == " ") and char not in mine:
            add(f"缺少{invisible_label(char)}")
    return notes


def difference_tooltip(text: str, base: str | None) -> str | None:
    """差異字元與不可見字元的 Unicode 編號與名稱，供提示顯示。"""
    chars: list[str] = []
    for start, end in diff_ranges(text, base):
        chars += list(text[start:end])
    chars += [text[i] for i in sorted(marked_indexes(text))]
    unique = list(dict.fromkeys(chars))
    if not unique:
        return None
    lines = [
        f"「{c if c.isprintable() and c != ' ' else '·'}」{describe_char(c)}"
        for c in unique[:TOOLTIP_CHARS]
    ]
    if len(unique) > TOOLTIP_CHARS:
        lines.append(f"…另 {len(unique) - TOOLTIP_CHARS} 種字元")
    return "\n".join(lines)


# ---------------------------------------------------------------------- 畫面片段
def _plain_spans(
    text: str, changed: bool, highlight: str, code_dim: bool
) -> list[ft.TextSpan]:
    """一般文字片段：格式碼用淡色等寬字，差異片段加 ``highlight`` 底色。"""
    bgcolor = highlight if changed else None
    if not code_dim:
        return [ft.TextSpan(text, style=ft.TextStyle(bgcolor=bgcolor))]
    spans: list[ft.TextSpan] = []
    position = 0
    for match in _CODE_RUN_RE.finditer(text):
        if match.start() > position:
            spans.append(
                ft.TextSpan(
                    text[position : match.start()],
                    style=ft.TextStyle(bgcolor=bgcolor),
                )
            )
        style = ft.TextStyle(color=C.DIM, font_family="monospace", bgcolor=bgcolor)
        spans.append(ft.TextSpan(match.group(), style=style))
        position = match.end()
    if position < len(text):
        spans.append(ft.TextSpan(text[position:], style=ft.TextStyle(bgcolor=bgcolor)))
    return spans


def text_control(
    text: str,
    base: str | None = None,
    *,
    size: float = 13,
    color=None,
    mark_invisible: bool = True,
    show_diff: bool = True,
    highlight: str | None = None,
    code_dim: bool = True,
    max_lines: int | None = None,
    selectable: bool = True,
) -> ft.Text:
    """可比對的唯讀文字控制項：不可見字元顯示成標記、與 ``base`` 不同的字元加底色（預設紅底，
    ``highlight`` 可換色）、格式碼淡化、提示列出字元編號。``text`` 本身不會被修改。"""
    reference = base if show_diff else None
    diff_color = highlight or ft.Colors.with_opacity(0.35, C.RED)
    spans: list[ft.TextSpan] = []
    for segment in plan_segments(text, reference, mark_invisible=mark_invisible):
        if segment.kind == _KIND_MARK:
            style = ft.TextStyle(
                color=C.GOLD,
                bgcolor=ft.Colors.with_opacity(
                    0.35 if segment.changed else 0.15, C.GOLD
                ),
                font_family="monospace",
            )
            spans.append(ft.TextSpan(segment.text, style=style))
        else:
            spans += _plain_spans(segment.text, segment.changed, diff_color, code_dim)
    return ft.Text(
        spans=spans,
        size=size,
        color=color or C.TEXT,
        selectable=selectable,
        tooltip=difference_tooltip(text, reference)
        if mark_invisible or show_diff
        else None,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if max_lines else None,
    )


def compare_chips(
    text: str,
    base: str | None = None,
    *,
    base_label: str = "統一譯文",
    lead: Sequence[ft.Control] = (),
) -> list[ft.Control]:
    """譯文下方的說明控制項：「含不可見字元」「含全形 ％」標籤列，以及一行「差異：…」。

    ``lead`` 是要排在標籤列前面的控制項（例如來源標籤、筆數）；沒有任何可說的內容且沒有 ``lead``
    時回傳空清單，呼叫端不需要另外判斷。
    """
    chips: list[ft.Control] = list(lead)
    labels = invisible_labels(text)
    fullwidth = format_fullwidth_chars(text)
    if labels:
        chips.append(kit.chip("含不可見字元", "gold", tooltip="、".join(labels)))
    if fullwidth:
        chips.append(kit.chip(f"含全形 {''.join(fullwidth)}", "gold"))
    controls: list[ft.Control] = [ft.Row(chips, spacing=6, wrap=True)] if chips else []
    notes = compare_notes(text, base, base_label=base_label)
    if notes:
        controls.append(ft.Text("差異：" + "、".join(notes), size=11, color=C.GOLD))
    return controls
