"""Modonomicon 書籍 JSON 內直接寫成英文的文字（字面英文）。

Modonomicon 把書裡每個文字欄位當成語系鍵交給遊戲查詢；查不到鍵時，畫面顯示的就是欄位值本身。
所以書籍作者直接把英文寫進 JSON 時，只要語系檔有一筆「鍵 = 那句英文」的條目，遊戲就會顯示譯文，
不必改書籍檔。這個模組只負責找出這些字面英文，不讀檔案、不翻譯、不寫檔。

規則：

- 只看文字欄位（``TEXT_FIELDS``）；``id``、``icon``、``type``、連結與條件等識別字一律不碰。
- 值已經是該模組語系檔的鍵時是正常的鍵引用，不算字面英文。
- 長得像鍵或資源位置的值（``a.b.c``、``mod:name``）不算。
- 含 ``%`` 的值不收：部分顯示路徑會經過 ``String.format``，譯文裡的 ``%`` 需要成對寫成 ``%%``，
  無法只靠複製字串處理，留給人工。
- 換行與 ``[#]`` 等標記屬於字串本身，原樣保留（鍵必須與書籍檔內的字串逐位元相同）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from typing import Any

# 書籍 JSON 在 jar 內的位置：``data/<模組>/modonomicon/books/<書>/…``
BOOK_JSON_RE = re.compile(
    r"^data/(?P<mod>[^/]+)/modonomicon/books/(?P<rest>.+\.json)$", re.IGNORECASE
)

# Modonomicon 頁面與條目會當成可翻譯文字的欄位
TEXT_FIELDS = frozenset(
    {
        "name",
        "title",
        "title1",
        "title2",
        "text",
        "description",
        "multiblock_name",
        "tooltip",
        "subtitle",
        "caption",
    }
)

_KEY_LIKE = re.compile(r"^[a-z0-9_.\-/]+(:[a-z0-9_.\-/]+)?$")
_HAS_WORD = re.compile(r"[A-Za-z]{2,}")


def is_book_json(member: str) -> bool:
    """jar 成員是否是 Modonomicon 書籍 JSON。"""
    return BOOK_JSON_RE.match(member.replace("\\", "/")) is not None


def book_mod_id(member: str) -> str | None:
    """書籍 JSON 所屬的模組 id；不是書籍 JSON 回傳 None。"""
    m = BOOK_JSON_RE.match(member.replace("\\", "/"))
    return m.group("mod") if m else None


def is_literal_english(value: str, lang_keys: Container[str]) -> bool:
    """文字欄位的值是否是要當字串本身翻譯的字面英文。"""
    if not value.strip() or value in lang_keys:
        return False
    if "%" in value or _KEY_LIKE.match(value):
        return False
    return _HAS_WORD.search(value) is not None


def _walk(node: Any, field: str | None) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, key if isinstance(key, str) else None)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, field)
    elif isinstance(node, str) and field in TEXT_FIELDS:
        yield node


def extract_literals(book: Any, lang_keys: Container[str]) -> dict[str, str]:
    """一份書籍 JSON 內的字面英文，回傳 ``{字串: 字串}``（語系檔的鍵與原文相同）。

    ``lang_keys`` 是該模組 ``en_us`` 的鍵集合，用來排除正常的鍵引用。依出現順序，重複字串只留一筆。
    """
    out: dict[str, str] = {}
    for value in _walk(book, None):
        if value not in out and is_literal_english(value, lang_keys):
            out[value] = value
    return out


_MAIN_EN_US = re.compile(r"^assets/(?P<mod>[^/]+)/lang/en_us\.json$", re.IGNORECASE)


def main_lang_mod_id(member: str) -> str | None:
    """``assets/<模組>/lang/en_us.json`` 的模組 id；其他路徑回傳 None。

    書籍字面英文只補進模組的主語系檔：書籍 JSON 屬於整個模組，不屬於某個附屬語系檔。
    """
    m = _MAIN_EN_US.match(member.replace("\\", "/"))
    return m.group("mod") if m else None


def collect_mod_literals(
    names: Iterable[str],
    read_book: Callable[[str], Any],
    mod_id: str,
    en: Mapping[str, Any],
) -> dict[str, str]:
    """模組所有書籍內、``en`` 沒有的字面英文。

    ``names`` 是 jar 的成員清單；``read_book(成員)`` 回傳解析後的 JSON，讀不到或解析失敗回傳 None。
    """
    prefix = f"data/{mod_id}/modonomicon/books/".lower()
    out: dict[str, str] = {}
    for name in names:
        if not name.lower().startswith(prefix) or not is_book_json(name):
            continue
        book = read_book(name)
        if book is None:
            continue
        for key, value in extract_literals(book, en).items():
            if key not in en:
                out.setdefault(key, value)
    return out
