"""Safe loading for the known neutral-pages book format.

Some released custom books contain one complete JSON object followed by the
literal tail ``]}``.  Minecraft's book reader can still consume the object,
but the document is not valid JSON for the translation pipeline.  Recovery is
intentionally narrow: strict JSON is always tried first, and only a confirmed
neutral-pages document with that exact trailing tail is accepted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from translation_tool.core.book_formats import (
    FORMAT_NEUTRAL_PAGES,
    is_neutral_pages_candidate,
    resolve_book_format,
)


@dataclass(frozen=True)
class NeutralBookLoad:
    """Parsed neutral-book data and whether a bounded recovery was applied."""

    data: dict[str, Any]
    repaired_tail: str | None = None

    @property
    def repaired(self) -> bool:
        return self.repaired_tail is not None


def load_neutral_book_json(text: str, path: str) -> NeutralBookLoad:
    """Load a neutral-pages book without changing arbitrary JSON text.

    The strict parser remains authoritative.  Recovery is available only for
    a path under ``assets/<namespace>/books/`` whose first JSON document has
    the established neutral-pages shape and whose remaining non-whitespace is
    exactly ``]}``.  Any other trailing data is rejected so a second JSON
    document, a comment, or an unrelated truncation cannot be silently lost.
    """

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        if not is_neutral_pages_candidate(path):
            raise

        decoder = json.JSONDecoder()
        leading = text.lstrip()
        parsed, end = decoder.raw_decode(leading)

        trailing = leading[end:].strip()
        if trailing != "]}":
            raise
        if not isinstance(parsed, dict):
            raise
        if resolve_book_format(path, parsed).name != FORMAT_NEUTRAL_PAGES:
            raise
        return NeutralBookLoad(data=parsed, repaired_tail=trailing)

    if not isinstance(parsed, dict):
        raise TypeError("中性自訂書本 JSON 根節點必須是 object")
    return NeutralBookLoad(data=parsed)


__all__ = ["NeutralBookLoad", "load_neutral_book_json"]
