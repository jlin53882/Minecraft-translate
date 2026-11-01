"""Shared locale selection rules for KubeJS language JSON files.

KubeJS language assets do not always contain ``en_us``.  The database scanner
and the KubeJS pipeline must make the same deterministic choice in that case:
use the locale with the most non-empty string values as the source snapshot,
while still retaining zh_cn/zh_tw as translation candidates.
"""

from __future__ import annotations

from collections.abc import Mapping

KUBEJS_TRANSLATION_LOCALES = ("zh_tw", "zh_cn")


def normalize_locale(locale: str) -> str:
    """Normalize a locale file stem without changing the source path."""

    return str(locale).strip().lower().replace("-", "_")


def count_language_values(values: Mapping[object, object]) -> int:
    """Count non-empty string values that can act as translation candidates."""

    return sum(
        1 for value in values.values() if isinstance(value, str) and value.strip()
    )


def select_source_locale(locale_counts: Mapping[str, int]) -> str | None:
    """Select the source locale for one KubeJS language group.

    A usable en_us file always wins.  Only when it has no non-empty values do
    we choose the locale with the largest number of values.  ``pt_br`` is the
    explicit tie-breaker because it is the common fallback locale in the
    supported modpacks; all other ties use lexical order for reproducibility.
    """

    normalized = {
        normalize_locale(locale): int(count) for locale, count in locale_counts.items()
    }
    if normalized.get("en_us", 0) > 0:
        return "en_us"

    candidates = [locale for locale, count in normalized.items() if count > 0]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda locale: (
            -normalized[locale],
            0 if locale == "pt_br" else 1,
            locale,
        ),
    )


def selected_locales(
    available_locales: set[str], source_locale: str | None
) -> set[str]:
    """Return the source locale plus available Chinese translation locales."""

    normalized = {normalize_locale(locale) for locale in available_locales}
    selected = set(KUBEJS_TRANSLATION_LOCALES) & normalized
    if source_locale:
        selected.add(normalize_locale(source_locale))
    return selected
