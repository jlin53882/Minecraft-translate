"""Markdown／Patchouli 的硬結構格式政策。

同一段落內的排版折行是軟折行，可以依繁體中文語意重排；空白行、Markdown
區塊前綴、明確換行、Patchouli 元件與標籤則是硬結構，翻譯後必須保留。
"""

from __future__ import annotations

import re
from collections import Counter

_TAG = re.compile(r"<[^<>\n]+>")
_LINK_TARGET = re.compile(r"\]\(([^)\n]*)\)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")
_BULLET = re.compile(r"^\s*([-*+])\s+\S")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+\S")
_QUOTE = re.compile(r"^\s*>")
_HARD_BREAK = re.compile(r"(?: {2,}|\\)\n")
_COMPONENT_LINE = re.compile(r"^(?:(?:<[^<>\n]+>)|(?:\$\([^\n]*?\)))+$")

_LABELS = {
    "tag": "標籤",
    "heading": "標題前綴",
    "bullet": "清單前綴",
    "ordered": "編號清單前綴",
    "quote": "引用前綴",
    "bold": "粗體標記",
    "code": "行內程式碼標記",
    "link": "連結目標",
}


def _line_marks(line: str) -> list[tuple[str, str]]:
    heading = _HEADING.match(line)
    if heading:
        return [("heading", heading.group(1))]
    bullet = _BULLET.match(line)
    if bullet:
        return [("bullet", bullet.group(1))]
    if _ORDERED.match(line):
        return [("ordered", "1.")]
    if _QUOTE.match(line):
        return [("quote", ">")]
    return []


def _line_prefixes(text: str) -> tuple[tuple[str, str], ...]:
    """Return ordered Markdown block prefixes; ordinary paragraph lines are ignored."""
    return tuple(
        mark for line in (text or "").splitlines() for mark in _line_marks(line)
    )


def _blank_line_count(text: str) -> int:
    """Count blank physical lines, which separate paragraphs and blocks."""
    return sum(not line.strip() for line in (text or "").splitlines())


def _component_lines(text: str) -> tuple[str, ...]:
    """Return standalone Patchouli component lines in their original order."""
    return tuple(
        line.strip()
        for line in (text or "").splitlines()
        if line.strip() and _COMPONENT_LINE.fullmatch(line.strip())
    )


def guide_marks(text: str) -> Counter[tuple[str, str]]:
    """``(種類, 內容)`` 的出現次數。"""
    marks: Counter[tuple[str, str]] = Counter()
    for line in (text or "").splitlines():
        marks.update(_line_marks(line))
    marks.update(("tag", tag) for tag in _TAG.findall(text or ""))
    marks.update(("link", target) for target in _LINK_TARGET.findall(text or ""))
    marks[("bold", "**")] += (text or "").count("**")
    marks[("code", "`")] += (text or "").count("`")
    return +marks  # 去掉次數為 0 的項目


def format_spec(text: str) -> dict[str, object]:
    """Build the compact hard-structure contract sent alongside a batch item."""
    value = text or ""
    return {
        "paragraph_count": sum(
            bool(block.strip()) for block in re.split(r"\n\s*\n", value)
        ),
        "blank_line_count": _blank_line_count(value),
        "markdown_prefixes": [
            {"kind": kind, "value": marker} for kind, marker in _line_prefixes(value)
        ],
        "markdown_hard_break_count": len(_HARD_BREAK.findall(value)),
        "protected_components": list(_TAG.findall(value)),
        "standalone_component_lines": list(_component_lines(value)),
    }


def _count_issue(label: str, wanted: int, actual: int) -> str | None:
    delta = actual - wanted
    if not delta:
        return None
    direction = "多了" if delta > 0 else "少了"
    return f"{direction} {abs(delta)} 個{label}"


def _describe(mark: tuple[str, str]) -> str:
    kind, value = mark
    return f"{_LABELS[kind]} {value}"


def guide_issues(source: str, translated: str) -> list[str]:
    """Compare hard structure while allowing soft paragraph line reflow."""
    wanted, actual = guide_marks(source), guide_marks(translated)
    issues: list[str] = []
    for mark in sorted(wanted.keys() | actual.keys()):
        delta = actual[mark] - wanted[mark]
        if delta < 0:
            issues.append(f"少了 {-delta} 個「{_describe(mark)}」")
        elif delta > 0:
            issues.append(f"多了 {delta} 個「{_describe(mark)}」")

    blank_issue = _count_issue(
        "段落分隔空行",
        _blank_line_count(source or ""),
        _blank_line_count(translated or ""),
    )
    if blank_issue:
        issues.append(blank_issue)

    source_prefixes = _line_prefixes(source)
    translated_prefixes = _line_prefixes(translated)
    if source_prefixes != translated_prefixes and Counter(source_prefixes) == Counter(
        translated_prefixes
    ):
        # Count differences are already reported by guide_marks; this extra
        # issue is only for a reorder, which the counters cannot describe.
        issues.append("Markdown 區塊前綴順序不一致")

    hard_break_issue = _count_issue(
        "Markdown 硬換行",
        len(_HARD_BREAK.findall(source or "")),
        len(_HARD_BREAK.findall(translated or "")),
    )
    if hard_break_issue:
        issues.append(hard_break_issue)

    if _component_lines(source) != _component_lines(translated):
        issues.append("Patchouli 元件獨立行不一致")
    return issues


__all__ = ["format_spec", "guide_issues", "guide_marks"]
