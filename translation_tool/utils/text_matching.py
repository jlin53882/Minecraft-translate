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


def term_canonical_key(term: str) -> str:
    """Normalize a glossary source without losing the original display text."""
    return term.strip().casefold() if isinstance(term, str) else ""


def matching_terms(sources: tuple[str, ...], terms: tuple[str, ...]) -> set[str]:
    """Return terms that occur in at least one source using a prefix index.

    The index avoids testing every term against every source.  A final
    ``contains_term`` call keeps the word-boundary contract authoritative.
    """
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
        normalized_source = source.casefold()
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
    "term_canonical_key",
]
