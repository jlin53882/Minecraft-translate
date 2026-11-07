"""版本差異審核的譯文呈現：格式碼淡化與兩段譯文的差異標示。

只影響畫面：回傳的 ``ft.TextSpan`` 串接後文字與原字串完全相同，複製出來的內容不變。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import flet as ft

from app.ui.design import C

# Minecraft 色碼／格式碼（&a、&l、&r、§a…），以及 printf 風格的佔位符
_CODE_RE = re.compile(r"(?:[&§][0-9a-fk-orA-FK-OR])|(?:%(?:\d+\$)?[sdf%])")
# 兩段文字相似度低於這個值時不標差異（整句都不同，標色只會變成色塊）
MIN_RATIO = 0.5


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


def _runs(text: str, ranges: list[tuple[int, int]]) -> list[tuple[str, bool, bool]]:
    """把文字切成（內容, 是否格式碼, 是否差異）的連續片段。"""
    changed = [False] * len(text)
    for start, end in ranges:
        for index in range(start, min(end, len(text))):
            changed[index] = True
    code = [False] * len(text)
    for match in _CODE_RE.finditer(text):
        for index in range(match.start(), match.end()):
            code[index] = True
    runs: list[tuple[str, bool, bool]] = []
    for index, char in enumerate(text):
        key = (code[index], changed[index])
        if runs and (runs[-1][1], runs[-1][2]) == key:
            runs[-1] = (runs[-1][0] + char, *key)
        else:
            runs.append((char, *key))
    return runs


def text_spans(
    text: str, ranges: list[tuple[int, int]], highlight: str
) -> list[ft.TextSpan]:
    """格式碼用淡色等寬字，差異片段加 ``highlight`` 底色。"""
    spans: list[ft.TextSpan] = []
    for chunk, is_code, is_changed in _runs(text, ranges):
        style = ft.TextStyle(
            color=C.DIM if is_code else None,
            font_family="monospace" if is_code else None,
            bgcolor=highlight if is_changed else None,
        )
        spans.append(ft.TextSpan(chunk, style=style))
    return spans
