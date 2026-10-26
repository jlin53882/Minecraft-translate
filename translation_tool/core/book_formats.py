"""Shared book/guide format detection and safe custom-book traversal.

Minecraft mods do not all use Patchouli's resource layout.  This module keeps
format detection in one place so the database scanner, directory translator,
and merge pipeline cannot silently apply the generic JSON walker to an
unrelated document.

The neutral-pages profile is intentionally small.  It accepts only fields
whose meaning is established by the format (``summary`` and
``literal_text``), plus display fields nested in a page/element.  Everything
else remains structural or unsupported until a profile provides evidence.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from translation_tool.core.book_dirs import DEFAULT_BOOK_DIRS, find_book_dir_index
from translation_tool.core.text_sniffer import (
    BOOK_ID_FIELDS,
    VANILLA_NON_TEXT_DIRS,
    classify_book_string,
)

FORMAT_STANDARD_PATCHOULI = "standard_patchouli"
FORMAT_NEUTRAL_PAGES = "neutral_pages"
FORMAT_MARKDOWN_GUIDE = "markdown_guide"
FORMAT_ETERNAL_STARLIGHT = "eternal_starlight"
FORMAT_MODONOMICON = "modonomicon"
FORMAT_ORACLE_INDEX = "oracle_index"
FORMAT_UNSUPPORTED = "unsupported"
FORMAT_UNKNOWN = "unknown"

BOOK_EXCLUDED_DIRS = frozenset(
    {
        *VANILLA_NON_TEXT_DIRS,
        "api",
        "common",
        "datagen",
        "gui",
    }
)

_RESOURCE_ROOTS = frozenset({"assets", "data"})
_NEUTRAL_ROOT = "books"
_NEUTRAL_REQUIRED_FIELDS = frozenset({"summary", "literal_text"})
_NEUTRAL_OPTIONAL_DISPLAY_FIELDS = frozenset({"caption", "text", "title"})
_NEUTRAL_CONTAINER_FIELDS = frozenset({"pages", "elements"})


@dataclass(frozen=True)
class BookFormatResolution:
    """The format decision for one resource member/document."""

    name: str
    supported: bool
    reason: str

    @property
    def is_patchouli_compatible(self) -> bool:
        return self.name in {FORMAT_STANDARD_PATCHOULI, FORMAT_NEUTRAL_PAGES}


def _resource_parts(path: str | PurePosixPath) -> tuple[str, ...]:
    parts = tuple(str(path).replace("\\", "/").split("/"))
    for index, part in enumerate(parts):
        if part.lower() in _RESOURCE_ROOTS and len(parts) - index >= 3:
            return tuple(parts[index:])
    return ()


def _has_neutral_pages_shape(document: object) -> bool:
    if not isinstance(document, Mapping):
        return False
    pages = document.get("pages")
    if not isinstance(pages, list) or not pages:
        return False
    return any(
        isinstance(page, Mapping)
        and any(field in page for field in ("type", "summary", "elements", "literal_text"))
        for page in pages
    )


def is_neutral_pages_candidate(path: str | PurePosixPath) -> bool:
    """Return whether a path is in a no-locale custom ``books`` tree."""

    parts = _resource_parts(path)
    if len(parts) < 4 or parts[0].lower() != "assets":
        return False
    return parts[2].lower() == _NEUTRAL_ROOT


def resolve_book_format(
    path: str | PurePosixPath,
    document: object | None = None,
    *,
    dir_names: tuple[str, ...] | list[str] = DEFAULT_BOOK_DIRS,
) -> BookFormatResolution:
    """Resolve a book-like path without guessing from arbitrary JSON strings.

    Standard configured Patchouli paths retain their existing semantics.  The
    known custom layouts are explicitly classified as unsupported unless a
    safe profile exists; that prevents them from falling through to the
    generic recursive JSON translator.
    """

    parts = _resource_parts(path)
    if len(parts) < 3:
        return BookFormatResolution(FORMAT_UNKNOWN, False, "resource_root_missing")

    lower = tuple(part.lower() for part in parts)
    content_dirs = lower[2:-1]
    if any(part in BOOK_EXCLUDED_DIRS for part in content_dirs):
        return BookFormatResolution(FORMAT_UNKNOWN, False, "known_non_book_directory")
    if "oracle_index" in content_dirs:
        return BookFormatResolution(FORMAT_ORACLE_INDEX, False, "oracle_index_is_mdx_docs")
    if "modonomicon" in content_dirs:
        return BookFormatResolution(FORMAT_MODONOMICON, False, "modonomicon_requires_own_schema")
    if "eternal_starlight" in content_dirs:
        return BookFormatResolution(
            FORMAT_ETERNAL_STARLIGHT,
            False,
            "eternal_starlight_requires_own_schema",
        )

    book_index = find_book_dir_index(parts, dir_names)
    if book_index is not None:
        return BookFormatResolution(
            FORMAT_STANDARD_PATCHOULI,
            True,
            "configured_patchouli_directory",
        )

    if is_neutral_pages_candidate(path):
        if document is None:
            return BookFormatResolution(
                FORMAT_NEUTRAL_PAGES,
                True,
                "neutral_books_path_requires_content_check",
            )
        if _has_neutral_pages_shape(document):
            return BookFormatResolution(
                FORMAT_NEUTRAL_PAGES,
                True,
                "neutral_pages_schema",
            )
        return BookFormatResolution(
            FORMAT_UNKNOWN,
            False,
            "neutral_books_shape_not_confirmed",
        )

    return BookFormatResolution(FORMAT_UNKNOWN, False, "no_known_book_profile")


def _field_name(path: str) -> str:
    return path.rsplit(".", 1)[-1].split("[", 1)[0].lower()


def _has_page_or_element_parent(path: str) -> bool:
    return any(
        segment.split("[", 1)[0].lower() in _NEUTRAL_CONTAINER_FIELDS
        for segment in path.replace("#", ".").split(".")
    )


def neutral_pages_field_allowed(path: str) -> bool:
    """Return whether a string path has a safe neutral-pages meaning."""

    field = _field_name(path)
    if field in _NEUTRAL_REQUIRED_FIELDS:
        return True
    return field in _NEUTRAL_OPTIONAL_DISPLAY_FIELDS and _has_page_or_element_parent(path)


def neutral_pages_value_kind(path: str, value: str) -> str:
    """Classify an allowed custom-book value using the shared shape guard."""

    if not neutral_pages_field_allowed(path) or _field_name(path) in BOOK_ID_FIELDS:
        return "not"
    # Use a known display field for custom display fields.  The shape checker
    # still rejects resource IDs, lang keys, URLs, placeholders, and numbers.
    return classify_book_string(value, "text")


def iter_neutral_pages_values(document: object) -> tuple[tuple[str, str, str], ...]:
    """Return ``(json_path, value, verdict)`` for safe custom-book strings."""

    found: list[tuple[str, str, str]] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if isinstance(value, str):
                    verdict = neutral_pages_value_kind(child, value)
                    if verdict != "not":
                        found.append((child, value, verdict))
                else:
                    walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                child = f"{path}[{index}]"
                if isinstance(value, str):
                    verdict = neutral_pages_value_kind(child, value)
                    if verdict != "not":
                        found.append((child, value, verdict))
                else:
                    walk(value, child)

    walk(document, "")
    return tuple(found)


def map_neutral_pages_values(
    document: object,
    transform: Callable[[str], str],
) -> object:
    """Copy a neutral-pages document while transforming only safe text paths."""

    result = copy.deepcopy(document)

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                child = f"{path}.{key}" if path else str(key)
                if isinstance(value, str):
                    if neutral_pages_value_kind(child, value) == "text":
                        node[key] = transform(value)
                else:
                    walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                child = f"{path}[{index}]"
                if isinstance(value, str):
                    if neutral_pages_value_kind(child, value) == "text":
                        node[index] = transform(value)
                else:
                    walk(value, child)

    walk(result, "")
    return result
