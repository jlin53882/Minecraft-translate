"""guide_format.py

指南文字（``.md``／``.txt`` 段落）的格式標記比對：原文有的標記，譯文要留著。

只看會讓內容壞掉的標記：元件／換行標籤（``<br>``、``<ItemLink …/>``）、行首的標題／清單／
引用前綴、粗體 ``**``、行內程式碼 `` ` ``、連結目標 ``](…)``。LM 翻譯完成後與資料庫條目的
編輯畫面共用這套比對，所以兩邊看到的問題一致。
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


def _describe(mark: tuple[str, str]) -> str:
    kind, value = mark
    return f"{_LABELS[kind]} {value}"


def guide_issues(source: str, translated: str) -> list[str]:
    """譯文與原文標記的差異，穩定排序的繁體中文說明；沒有差異回傳空串列。"""
    wanted, actual = guide_marks(source), guide_marks(translated)
    issues: list[str] = []
    for mark in sorted(wanted.keys() | actual.keys()):
        delta = actual[mark] - wanted[mark]
        if delta < 0:
            issues.append(f"少了 {-delta} 個「{_describe(mark)}」")
        elif delta > 0:
            issues.append(f"多了 {delta} 個「{_describe(mark)}」")
    return issues
