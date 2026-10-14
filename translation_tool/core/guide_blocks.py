"""guide_blocks.py

指南文字檔切成可翻譯區塊：內文段落（與 Markdown 翻譯流程同一套切法），加上 front matter
裡 ``navigation.title``（指南側邊欄的標題）。標題區塊排在所有段落之後，所以段落序號
不會因為標題而位移。資料庫掃描與 LM 翻譯共用這個切法，兩邊的區塊序號才會一致。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from translation_tool.core.text_sniffer import is_placeholder_text
from translation_tool.plugins.md.md_extract_qa import extract_blocks

_FRONT_RULE = re.compile(r"^---\s*$")
_NAV_KEY = re.compile(r"^(\s*)navigation:\s*$")
_TITLE_LINE = re.compile(r"^(\s+title:\s*)(.*?)\s*$")
_ANY_TAG = re.compile(r"<[^<>]*>")
_LETTER = re.compile(r"[^\W\d_]")
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


_COMMENT_LINE = re.compile(r"^\s*\[//\]:\s*#")
_LICENSE_PHRASE = re.compile(
    r"permission is hereby granted|without warranty of any kind"
    r"|the above copyright notice|copyright \(c\)|©"
    r"|licensed under the (?:apache|mit|gnu)|gnu (?:lesser |affero )?general public license"
    r"|creative commons|redistribution and use in source and binary forms"
    r"|^\s*(?:mit|bsd|isc|apache|mozilla public|gnu)[\w \s.,-]*licen[sc]e\s*$",
    re.IGNORECASE,
)
_KEY_VALUE_LINE = re.compile(r"^\s*[A-Za-z_][\w.\-]*(?:[ ][\w.\-]+){0,2}\s*[:=]\s*\S")
_SENTENCE_END = re.compile(r"[.!?。！？:;]\s*$")
_LIST_OR_MARKUP_LINE = re.compile(r"^\s*(?:[-*+>|#]|\d+[.)])")
_KEY_VALUE_SHARE = 0.6
_NAME_LIST_MIN_LINES = 8
_KEY_VALUE_MIN_LINES = 3


def _is_comment_block(lines: list[str]) -> bool:
    """``[//]: #`` 開頭的區塊是 Markdown 註解（不顯示給玩家）。"""
    return bool(lines) and _COMMENT_LINE.match(lines[0]) is not None


_CODE_SYMBOLS = re.compile(r"[=(){}\[\]_;\"\d]|\w\.\w")
_PROSE_WORD = re.compile(r"[A-Za-z']+")
_PROSE_MIN_WORDS = 5
_PROSE_LEAD_WORDS = 4  # 設定值前後夾的說明句，字數門檻較低


def _is_prose_line(text: str, min_words: int = _PROSE_MIN_WORDS) -> bool:
    """純英文單字夠多、沒有程式碼符號：是敘述句，不是程式碼或設定值。"""
    return (
        len(_PROSE_WORD.findall(text)) >= min_words
        and _CODE_SYMBOLS.search(text) is None
    )


def _is_setting_line(line: str) -> bool:
    """``key: value`` ／ ``key=value`` 且值不是敘述句（``true: will set the entity …`` 是說明）。"""
    if not _KEY_VALUE_LINE.match(line):
        return False
    value = re.split(r"[:=]", line, maxsplit=1)[1]
    return not _is_prose_line(value)


def _is_key_value_block(lines: list[str]) -> bool:
    """多數行都是設定值，且沒有夾著敘述句：設定內容。

    設定檔說明或程式碼範例前後的英文敘述（``To get bundled inputs:``）
    是玩家會讀的文字，整段不算設定。
    """
    if len(lines) < _KEY_VALUE_MIN_LINES:
        return False
    hits = sum(1 for line in lines if _is_setting_line(line))
    if hits / len(lines) < _KEY_VALUE_SHARE:
        return False
    return not any(
        not _is_setting_line(line) and _is_prose_line(line, _PROSE_LEAD_WORDS)
        for line in lines
    )


def _is_name_list(lines: list[str]) -> bool:
    """很多行、每行一到三個字、沒有句末標點、也不是清單或表格：名字清單。"""
    if len(lines) < _NAME_LIST_MIN_LINES:
        return False
    return all(
        not _LIST_OR_MARKUP_LINE.match(line)
        and not _SENTENCE_END.search(line)
        and len(line.split()) <= 3
        for line in lines
    )


_DATA_MIN_LINES = 5
_DATA_SHARE = 0.6
_DATA_MAX_WORDS = 6
_CODE_MIN_LINES = 3
_CODE_SHARE = 0.7
_NAME_LIST_MIN_ITEMS = 8
_LEADING_LABEL = re.compile(r"^\s*[\w ]{1,30}:\s+(?=\S+\s*,)")
_CODE_LINE = re.compile(
    r"^\s*(?:local\s|function\b|end\b|if\b.*\bthen\b|for\b.*\bdo\b|return\b"
    r"|[\w.:]+\(.*\)\s*$|[\w.]+(?:\s*,\s*[\w.]+)*\s*=\s*[\w.:]+\()"
)


def _is_data_block(lines: list[str]) -> bool:
    """多數行只有標籤或符號、可見英文字寥寥無幾：結構資料（手冊的版面指令、ASCII 圖）。"""
    if len(lines) < _DATA_MIN_LINES:
        return False
    if any(line.lstrip().startswith("|") for line in lines):
        return False  # 表格的標題欄是要翻譯的文字
    stripped =[_ANY_TAG.sub("", line) for line in lines]
    empty = sum(1 for line in stripped if _LETTER.search(line) is None)
    words = sum(len(line.split()) for line in stripped if _LETTER.search(line))
    return empty / len(lines) >= _DATA_SHARE and words < _DATA_MAX_WORDS


def _is_comma_name_list(text: str) -> bool:
    """一行以逗號隔開八個以上的短名稱（可有開頭的 ``Normal:`` 標籤），沒有句末標點。"""
    body = _LEADING_LABEL.sub("", text.strip(), count=1)
    if "\n" in body.strip() or _SENTENCE_END.search(body):
        return False
    parts = [part.strip() for part in body.split(",")]
    return len(parts) >= _NAME_LIST_MIN_ITEMS and all(
        part and len(part.split()) <= 3 and not part.endswith(".") for part in parts
    )


def _is_code_block(lines: list[str]) -> bool:
    """多數行是程式碼（Lua 範例、函式呼叫）：不是敘述文字。"""
    if len(lines) < _CODE_MIN_LINES:
        return False
    hits = sum(1 for line in lines if _CODE_LINE.match(line))
    return hits / len(lines) >= _CODE_SHARE


NON_TEXT_REASONS = {
    "tag_only": "只有標籤或符號",
    "comment": "Markdown 註解",
    "license": "授權條款",
    "placeholder": "佔位字（TODO／Lorem ipsum）",
    "key_value": "設定內容（key: value）",
    "name_list": "名字清單",
    "data": "結構資料（版面指令、符號圖）",
    "code": "程式碼範例",
}


def non_text_reason(text: str) -> str | None:
    """區塊不是敘述文字時的原因代碼（見 ``NON_TEXT_REASONS``）；是敘述文字回傳 None。

    只看內容形狀：去掉所有 ``<…>`` 標籤後要有文字；Markdown 註解、授權條款、設定內容
    （``key: value``）、名字清單、``TODO``／Lorem ipsum 佔位字不是玩家會讀到的敘述文字。
    """
    visible = _ANY_TAG.sub("", text)
    if _LETTER.search(visible) is None:
        return "tag_only"
    lines = [line for line in text.splitlines() if line.strip()]
    if _is_comment_block(lines):
        return "comment"
    if _LICENSE_PHRASE.search(text):
        return "license"
    if is_placeholder_text(visible):
        return "placeholder"
    if _is_key_value_block(lines):
        return "key_value"
    if _is_name_list(lines) or _is_comma_name_list(text):
        return "name_list"
    if _is_data_block(lines):
        return "data"
    if _is_code_block(lines):
        return "code"
    return None


def has_translatable_text(text: str) -> bool:
    """這個區塊是不是玩家會讀到的敘述文字、值得送去翻譯。區塊序號不因此位移，
    呼叫端略過不是敘述文字的區塊即可。"""
    return non_text_reason(text) is None


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
