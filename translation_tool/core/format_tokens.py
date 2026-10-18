"""Low-level token scanning for translation format contracts.

This module owns the format-token grammar used by the diagnostics layer and
the database quality filters.  It deliberately has no database dependency so
the core diagnostics can be imported by storage, services, and UI code without
creating a core-to-database dependency cycle.
"""

from __future__ import annotations

import re
from collections import Counter

_SCAN_RE = re.compile(
    r"§[0-9a-fk-orA-FK-OR]|%(?:\d+\$)?[sdfxXeEgGcb%]|\$\(|\{\d*\}|\\n|\n"
)
_TOOLTIP_PREFIX = "$(t:"
_TOOLTIP_TOKEN = "$(t:…)"


def _macro_end(text: str, start: int) -> int | None:
    depth = 1
    for i in range(start, len(text)):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    first = text.find(")", start)
    return None if first < 0 else first + 1


def _normalize_token(raw: str) -> str:
    token = raw.replace("\r", "")
    return _TOOLTIP_TOKEN if token.startswith(_TOOLTIP_PREFIX) else token


def _iter_token_spans(text: str):
    text = text or ""
    pos = 0
    while True:
        match = _SCAN_RE.search(text, pos)
        if match is None:
            return
        if match.group(0) == "$(":
            end = _macro_end(text, match.end())
            if end is None:
                pos = match.end()
                continue
            yield text[match.start() : end], match.start(), end
            pos = end
        else:
            yield match.group(0), match.start(), match.end()
            pos = match.end()


def format_token_spans(text: str) -> tuple[tuple[str, int, int], ...]:
    """Return normalized format tokens with source offsets."""
    return tuple(
        (_normalize_token(raw), start, end)
        for raw, start, end in _iter_token_spans(text)
    )


def format_tokens(text: str) -> Counter[str]:
    """Count placeholders, Minecraft codes, Patchouli macros and line breaks."""
    tokens: Counter[str] = Counter()
    for raw, _start, _end in _iter_token_spans(text or ""):
        tokens[_normalize_token(raw)] += 1
    return tokens


def token_category(token: str) -> str:
    """Return the stable diagnostic category for one token identifier."""
    if token.startswith("§"):
        return "minecraft"
    if token.startswith("$("):
        return "patchouli"
    if token.startswith("%") or (token.startswith("{") and token.endswith("}")):
        return "placeholder"
    if token in ("\n", "\\n"):
        return "newline"
    return "other"


__all__ = ["format_token_spans", "format_tokens", "token_category"]
