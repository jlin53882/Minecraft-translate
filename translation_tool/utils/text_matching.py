"""Small, shared text-matching helpers used by translation references."""

from __future__ import annotations


def contains_term(source: str, term: str) -> bool:
    """Return whether ``term`` occurs in ``source`` as a case-insensitive term.

    ASCII and Unicode word characters at the ends of an English term must not be
    part of a larger word. Punctuation inside or around a term remains matchable,
    which keeps Minecraft names such as ``<ItemLink>`` usable as references.
    """
    if not isinstance(source, str) or not isinstance(term, str):
        return False
    normalized_source = source.casefold()
    normalized_term = term.strip().casefold()
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


def _is_word_char(char: str) -> bool:
    return bool(char) and (char.isalnum() or char == "_")


__all__ = ["contains_term"]
