"""同文一致性審核的譯文呈現：不可見字元標記、與統一譯文的差異、成員依譯法分組與勾選狀態。

候選譯文常只差一個空白、NBSP 或全形字元，肉眼看起來完全相同；這裡把這些差異明確標出來。
只影響畫面：標記是顯示用的替代符號，實際寫入的譯文不受影響。純邏輯部分（``plan_segments``、
``difference_notes``、``bucket_members``、``selection_state`` 等）不需要建立畫面即可測試；
格式碼淡化與差異區間沿用 ``version_diff_text`` 的公開函式，不改變它對版本差異審核的行為。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import flet as ft

from app.ui.design import C
from app.views.moddb.version_diff_text import MIN_RATIO, changed_ranges, text_spans

# 譯文很短時（例如只有 "%s"），即使整句都不同也要標差異，不套用版本差異審核的相似度門檻
SHORT_TEXT = 16
TOOLTIP_CHARS = 8  # 提示最多列出的差異字元數

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


@dataclass(frozen=True)
class MemberBucket:
    """同一種目前譯文的成員：``total`` 是整組筆數，``members`` 只含有限制內要列出的。"""

    text: str
    total: int
    members: tuple


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
    """文字內的全形 ASCII 變體字元（不重複）；全形標點是中文常態，只在與統一譯文不同時才提醒。"""
    return list(dict.fromkeys(c for c in text if is_fullwidth_form(c)))


def format_fullwidth_chars(text: str) -> list[str]:
    """全形的字母、數字與佔位符常用符號（例如 ％、＆）：很可能是誤植，不同於中文標點。"""
    return [
        c
        for c in width_variant_chars(text)
        if halfwidth_of(c).isalnum() or halfwidth_of(c) in "%&$#@*+-=/\\_<>{}[]|~^'\"."
    ]


def diff_ranges(text: str, base: str | None) -> list[tuple[int, int]]:
    """``text`` 與 ``base`` 不同的區間；沒有基準或相同時為空。短譯文也標差異。"""
    if not base or not text or text == base:
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


def difference_notes(text: str, base: str | None) -> list[str]:
    """說明 ``text`` 與統一譯文 ``base`` 差在哪（多出／缺少的不可見字元、全形半形互換）。"""
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
            add(f"全形「{char}」（統一譯文是半形「{halfwidth_of(char)}」）")
        elif fullwidth_of(char) != char and fullwidth_of(char) in theirs:
            add(f"半形「{char}」（統一譯文是全形「{fullwidth_of(char)}」）")
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


def bucket_members(
    members, canonical_text: str | None, limit: int
) -> tuple[list[MemberBucket], int]:
    """把需要統一的成員依目前譯文分組（筆數多的在前、未翻譯放最後），最多列 ``limit`` 筆。

    回傳（分組, 因上限而未列出的筆數）。統一譯文已相同的成員不列；沒有統一譯文時全部列出。
    """
    targets = [
        m for m in members if canonical_text is None or m.zh_tw != canonical_text
    ]
    grouped: dict[str, list] = {}
    for member in targets:
        grouped.setdefault(member.zh_tw, []).append(member)
    order = sorted(grouped, key=lambda t: (not t, -len(grouped[t]), t))
    buckets: list[MemberBucket] = []
    room = limit
    for text in order:
        rows = grouped[text]
        buckets.append(MemberBucket(text, len(rows), tuple(rows[: max(room, 0)])))
        room -= len(rows)
    return buckets, max(len(targets) - limit, 0)


def default_selection() -> set[int]:
    """開啟時預設不勾選任何條目：使用者明確勾選群組或按「全選」才會統一。"""
    return set()


def selection_state(
    selectable: set[int], selected: set[int]
) -> tuple[bool | None, int]:
    """群組勾選方塊的三態與已勾選筆數：全部勾 True、全沒勾 False、部分 None；沒有可統一的為 False。"""
    chosen = len(selectable & selected)
    if not selectable or not chosen:
        return False, chosen
    return (True if chosen == len(selectable) else None), chosen


# ---------------------------------------------------------------------- 畫面片段
def text_control(
    text: str,
    base: str | None = None,
    *,
    size: float = 13,
    color=None,
    mark_invisible: bool = True,
    show_diff: bool = True,
) -> ft.Text:
    """譯文文字控制項：不可見字元顯示成標記、與 ``base`` 不同的字元加底色、提示列出字元編號。"""
    reference = base if show_diff else None
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
            spans += text_spans(
                segment.text,
                [(0, len(segment.text))] if segment.changed else [],
                ft.Colors.with_opacity(0.35, C.RED),
            )
    return ft.Text(
        spans=spans,
        size=size,
        color=color or C.TEXT,
        selectable=True,
        tooltip=difference_tooltip(text, reference),
    )
