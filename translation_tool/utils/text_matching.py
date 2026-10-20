"""官方術語與 glossary 共用的文字正規化、詞界匹配與前綴索引工具。"""

from __future__ import annotations

import re

_TERM_SEPARATOR_RE = re.compile(r"[-_]+")
_TERM_WHITESPACE_RE = re.compile(r"\s+")


def normalize_term_text(value: str, *, casefold: bool = False) -> str:
    """正規化 Minecraft 識別字與英文術語常用的分隔符。"""
    if not isinstance(value, str):
        return ""
    normalized = _TERM_SEPARATOR_RE.sub(" ", value.strip())
    normalized = _TERM_WHITESPACE_RE.sub(" ", normalized)
    return normalized.casefold() if casefold else normalized


def contains_term(source: str, term: str) -> bool:
    """以不分大小寫且遵守詞界的方式判斷來源是否包含術語。"""
    if not isinstance(source, str) or not isinstance(term, str):
        return False
    normalized_source = normalize_term_text(source, casefold=True)
    normalized_term = normalize_term_text(term, casefold=True)
    if not normalized_source or not normalized_term:
        return False

    starts_with_word = _is_word_char(normalized_term[0])
    ends_with_word = _is_word_char(normalized_term[-1])
    start = 0
    while True:
        index = normalized_source.find(normalized_term, start)
        if index < 0:
            return False
        before = normalized_source[index - 1] if index else ""
        after_index = index + len(normalized_term)
        after = (
            normalized_source[after_index]
            if after_index < len(normalized_source)
            else ""
        )
        if (not starts_with_word or not _is_word_char(before)) and (
            not ends_with_word or not _is_word_char(after)
        ):
            return True
        start = index + 1


def term_canonical_key(term: str) -> str:
    """正規化術語來源以建立穩定比對 key，同時保留顯示文字。"""
    return normalize_term_text(term, casefold=True)


def matching_terms(sources: tuple[str, ...], terms: tuple[str, ...]) -> set[str]:
    """使用前綴索引找出至少出現在一個來源中的術語，並以詞界檢查確認結果。"""
    normalized_terms: dict[str, list[str]] = {}
    for term in terms:
        if not isinstance(term, str):
            continue
        normalized = term_canonical_key(term)
        if normalized:
            normalized_terms.setdefault(normalized, []).append(term)
    if not normalized_terms:
        return set()

    prefix_index: dict[str, list[tuple[str, str]]] = {}
    for normalized, originals in normalized_terms.items():
        prefix_index.setdefault(normalized[:3], []).extend(
            (original, normalized) for original in originals
        )

    matched: set[str] = set()
    for source in sources:
        if not isinstance(source, str):
            continue
        normalized_source = normalize_term_text(source, casefold=True)
        prefixes = {
            normalized_source[index : index + size]
            for size in (1, 2, 3)
            for index in range(len(normalized_source) - size + 1)
        }
        for prefix in prefixes:
            for original, normalized in prefix_index.get(prefix, ()):
                if contains_term(source, original):
                    matched.add(original)
    return matched


def _is_word_char(char: str) -> bool:
    return bool(char) and (char.isalnum() or char == "_")


__all__ = [
    "contains_term",
    "matching_terms",
    "normalize_term_text",
    "term_canonical_key",
]
