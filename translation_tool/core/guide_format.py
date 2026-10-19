"""Markdown／Patchouli 的硬結構格式政策。

同一段落內的排版折行是軟折行，可以依繁體中文語意重排；空白行、Markdown
區塊前綴、明確換行、Patchouli 元件與標籤則是硬結構，翻譯後必須保留。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# 標籤必須以英文字母或 / 開頭；「<0°C = 0, >」這類比較符號文字不是標籤。
_TAG = re.compile(r"<[A-Za-z/][^<>\n]*>")
_LINK_TARGET = re.compile(r"\]\(([^)\n]*)\)")
# <link;分類;顯示文字[;start]>：第三欄是玩家看到的文字，可以翻譯；其餘欄位是硬結構。
_LINK_TAG = re.compile(r"<link;([^;<>\n]*);[^;<>\n]*((?:;[^<>\n]*)?)>")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")
_BULLET = re.compile(r"^\s*([-*+])\s+\S")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+\S")
_QUOTE = re.compile(r"^\s*>")
_HARD_BREAK = re.compile(r"(?: {2,}|\\)\n")
_COMPONENT_LINE = re.compile(
    r"^(?:(?:<[A-Za-z/][^<>\n]*>)|(?:\$\((?:[^()\n]|\([^()\n]*\))*\)))+$"
)

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


@dataclass(frozen=True)
class GuideMark:
    """A structured Guide/Markdown mark with an exact source span."""

    kind: str
    value: str
    start: int
    end: int
    line: int
    column: int

    @property
    def identifier(self) -> str:
        return f"{self.kind}:{self.value}"


def _tag_identity(tag: str) -> str:
    """標籤比對用的身分：<link> 的顯示文字可翻譯，不納入比對。"""
    return _LINK_TAG.sub(r"<link;\1;…\2>", tag)


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
    """Count blank physical lines between content, which separate paragraphs and blocks.

    文字開頭與結尾的空行只是空白（Patchouli JSON 常以空行結尾），不分隔任何段落。"""
    return sum(not line.strip() for line in (text or "").strip().splitlines())


def _component_lines(text: str) -> tuple[str, ...]:
    """Return standalone Patchouli component lines in their original order."""
    return tuple(
        line.strip()
        for line in (text or "").splitlines()
        if line.strip() and _COMPONENT_LINE.fullmatch(line.strip())
    )


def describe_guide_mark(mark: tuple[str, str]) -> str:
    """Stable human-facing label shared by diagnostics and legacy messages."""
    return _describe(mark)


def collect_guide_marks(text: str) -> tuple[GuideMark, ...]:
    """Collect Guide/Markdown structure with offsets for UI highlighting."""
    text = text or ""
    marks: list[GuideMark] = []
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        content = line.rstrip("\r\n")
        line_mark = _line_marks(content)
        if line_mark:
            kind, value = line_mark[0]
            start = len(content) - len(content.lstrip())
            if kind == "heading":
                end = start + len(value) + 1
            elif kind == "bullet":
                end = start + 1
            elif kind == "ordered":
                match = _ORDERED.match(content)
                end = match.end() if match else start + len(value)
            else:
                end = start + 1
            marks.append(
                GuideMark(
                    kind, value, offset + start, offset + end, line_number, start + 1
                )
            )
        for pattern, kind, group in (
            (_TAG, "tag", 0),
            (_LINK_TARGET, "link", 1),
        ):
            for match in pattern.finditer(content):
                value = match.group(group)
                if kind == "tag":
                    value = _tag_identity(value)
                start, end = match.span(group)
                marks.append(
                    GuideMark(
                        kind,
                        value,
                        offset + start,
                        offset + end,
                        line_number,
                        start + 1,
                    )
                )
        for kind, value, needle in (("bold", "**", "**"), ("code", "`", "`")):
            start = 0
            while True:
                found = content.find(needle, start)
                if found < 0:
                    break
                marks.append(
                    GuideMark(
                        kind,
                        value,
                        offset + found,
                        offset + found + len(needle),
                        line_number,
                        found + 1,
                    )
                )
                start = found + len(needle)
        offset += len(line)
    return tuple(marks)


def guide_marks(text: str) -> Counter[tuple[str, str]]:
    """``(種類, 內容)`` 的出現次數。"""
    marks: Counter[tuple[str, str]] = Counter(
        (mark.kind, mark.value) for mark in collect_guide_marks(text)
    )
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
        "protected_components": [_tag_identity(tag) for tag in _TAG.findall(value)],
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


def collect_guide_diagnostics(
    source: str,
    translated: str,
    *,
    source_marks: tuple[GuideMark, ...] | None = None,
    target_marks: tuple[GuideMark, ...] | None = None,
):
    """Return immutable structured diagnostics for Guide/Markdown marks.

    Callers that already collected the marks pass them in to avoid rescanning.
    """
    from translation_tool.core.format_diagnostics import FormatDiagnostic, FormatSpan

    if source_marks is None:
        source_marks = collect_guide_marks(source)
    if target_marks is None:
        target_marks = collect_guide_marks(translated)
    wanted = Counter(mark.identifier for mark in source_marks)
    actual = Counter(mark.identifier for mark in target_marks)
    source_by_id: dict[str, list[FormatSpan]] = {}
    target_by_id: dict[str, list[FormatSpan]] = {}
    for mark in source_marks:
        source_by_id.setdefault(mark.identifier, []).append(
            FormatSpan(mark.identifier, mark.start, mark.end, mark.line, mark.column)
        )
    for mark in target_marks:
        target_by_id.setdefault(mark.identifier, []).append(
            FormatSpan(mark.identifier, mark.start, mark.end, mark.line, mark.column)
        )
    diagnostics = []
    for identifier in sorted(set(wanted) | set(actual)):
        delta = actual[identifier] - wanted[identifier]
        if not delta:
            continue
        mark_kind, value = identifier.split(":", 1)
        diagnostics.append(
            FormatDiagnostic(
                identifier=identifier,
                category="guide",
                direction="extra" if delta > 0 else "missing",
                source_count=wanted[identifier],
                target_count=actual[identifier],
                delta=delta,
                location_confidence="exact",
                source_occurrences=tuple(source_by_id.get(identifier, ())),
                target_occurrences=tuple(target_by_id.get(identifier, ())),
                display_label=_describe((mark_kind, value)),
            )
        )
    return tuple(diagnostics)


def _component_line_issues(source: str, translated: str) -> list[str]:
    """說明獨立成行的元件（例如 <ItemLink .../>）在哪一邊被移動或改動。"""
    wanted = Counter(_component_lines(source))
    actual = Counter(_component_lines(translated))
    issues = [
        f"原文把 {component} 單獨放在一行，譯文卻把它接進句子或改動了內容；"
        "請讓它在譯文中也單獨成一行"
        for component in sorted(wanted - actual)
    ]
    issues += [
        f"譯文把 {component} 單獨成一行，但原文它是接在句子中間；"
        "請把它放回句子裡，不要獨立成行"
        for component in sorted(actual - wanted)
    ]
    if not issues and _component_lines(source) != _component_lines(translated):
        issues.append("獨立成行的 Patchouli 元件，先後順序與原文不同")
    return issues


def _mark_counts(marks: tuple[GuideMark, ...]) -> Counter[tuple[str, str]]:
    return +Counter((mark.kind, mark.value) for mark in marks)


def guide_issues(
    source: str,
    translated: str,
    *,
    source_marks: tuple[GuideMark, ...] | None = None,
    target_marks: tuple[GuideMark, ...] | None = None,
) -> list[str]:
    """Compare hard structure while allowing soft paragraph line reflow."""
    wanted = guide_marks(source) if source_marks is None else _mark_counts(source_marks)
    actual = (
        guide_marks(translated) if target_marks is None else _mark_counts(target_marks)
    )
    issues: list[str] = []
    for mark in sorted(wanted.keys() | actual.keys()):
        delta = actual[mark] - wanted[mark]
        if delta < 0:
            issues.append(f"少了 {-delta} 個「{_describe(mark)}」")
        elif delta > 0:
            issues.append(f"多了 {delta} 個「{_describe(mark)}」")

    blank_issue = _count_issue(
        "段落分隔空行（段落之間的空白行）",
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
        issues.append(
            "Markdown 區塊前綴（# 標題、- 清單、1. 編號等）的先後順序與原文不同"
        )

    hard_break_issue = _count_issue(
        "Markdown 硬換行（行尾兩個空白或反斜線）",
        len(_HARD_BREAK.findall(source or "")),
        len(_HARD_BREAK.findall(translated or "")),
    )
    if hard_break_issue:
        issues.append(hard_break_issue)

    issues.extend(_component_line_issues(source, translated))
    return issues


__all__ = [
    "GuideMark",
    "collect_guide_diagnostics",
    "collect_guide_marks",
    "describe_guide_mark",
    "guide_issues",
    "guide_marks",
]
