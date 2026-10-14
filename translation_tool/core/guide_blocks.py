"""guide_blocks.py

指南文字檔切成可翻譯區塊：內文段落（與 Markdown 翻譯流程同一套切法），加上 front matter
裡 ``navigation.title``（指南側邊欄的標題）。標題區塊排在所有段落之後，所以段落序號
不會因為標題而位移。資料庫掃描與 LM 翻譯共用這個切法，兩邊的區塊序號才會一致。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from translation_tool.plugins.md.md_extract_qa import extract_blocks

_FRONT_RULE = re.compile(r"^---\s*$")
_NAV_KEY = re.compile(r"^(\s*)navigation:\s*$")
_TITLE_LINE = re.compile(r"^(\s+title:\s*)(.*?)\s*$")
_YAML_SPECIAL = re.compile(r"""^[\[\]{}&*!|>'"%@`#-]|: | #""")


@dataclass(frozen=True)
class GuideBlock:
    text: str
    start_line: int  # 1-based
    end_line: int
    prefix: str = ""  # 只有 front matter 標題用：行首（含縮排與 ``title:``）


def _front_title(lines: Sequence[str]) -> tuple[int, str, str] | None:
    """``navigation.title`` 的 ``(行號 1-based, 行首, 值)``；沒有則 None。"""
    if not lines or not _FRONT_RULE.match(lines[0]):
        return None
    nav_indent: int | None = None
    child_indent: int | None = None
    for number, line in enumerate(lines[1:], start=2):
        if _FRONT_RULE.match(line):
            return None
        nav = _NAV_KEY.match(line)
        if nav:
            nav_indent, child_indent = len(nav.group(1)), None
            continue
        if nav_indent is None or not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= nav_indent:
            nav_indent = None  # 離開 navigation 區塊
            continue
        if child_indent is None:
            child_indent = indent
        title = _TITLE_LINE.match(line)
        if title and indent == child_indent:
            value = title.group(2)
            if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
                value = value[1:-1]
            return (number, title.group(1), value) if value else None
    return None


def extract_guide_blocks(text: str, name: str) -> list[GuideBlock]:
    blocks = [
        GuideBlock(b.text, b.start_line, b.end_line)
        for b in extract_blocks(text, name, "all")
    ]
    title = _front_title(text.splitlines())
    if title is not None:
        number, prefix, value = title
        blocks.append(GuideBlock(value, number, number, prefix))
    return blocks


def front_title_line(prefix: str, translated: str) -> str:
    """組回 front matter 的標題行；譯文含 YAML 特殊字元時加雙引號。"""
    flat = " ".join(translated.split())
    if _YAML_SPECIAL.search(flat):
        flat = '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return prefix + flat
