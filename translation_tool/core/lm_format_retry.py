"""Utilities for repairing format-token and newline mismatches."""

from __future__ import annotations

from dataclasses import dataclass

from translation_tool.translation_db.quality import token_issues

FORMAT_MISMATCH_RETRY_INSTRUCTION = """Repair only the format mismatches listed in each item.
For newline issues, inspect the source yourself and output the required number and
placement of real newline characters. Do not replace real newlines with the text
\\n. For special-format issues, retranslate the item as needed while preserving
every Minecraft formatting code, printf-style placeholder, Patchouli macro, and
literal backslash-n sequence (\\n) from the source.
Do not add explanations. Return only the structured JSON response with the same
item IDs and one translated value for each item."""


@dataclass(frozen=True)
class FormatMismatchRetryBatch:
    """A candidate-only format repair request and its original batch positions."""

    positions: tuple[int, ...]
    item_ids: tuple[str, ...]
    items: tuple[dict, ...]
    payload: dict
    id_to_item: dict[str, dict]
    issues_by_id: dict[str, tuple[str, ...]]


def build_format_mismatch_retry_batch(
    source_items: list[dict],
    translated_items: list[dict],
    item_ids: list[str],
) -> FormatMismatchRetryBatch:
    """Select translated rows whose format-token or newline counts are invalid."""
    positions: list[int] = []
    candidate_ids: list[str] = []
    candidate_items: list[dict] = []
    payload_items: list[dict[str, object]] = []
    id_to_item: dict[str, dict] = {}
    issues_by_id: dict[str, tuple[str, ...]] = {}

    for position, (source, translated, item_id) in enumerate(
        zip(source_items, translated_items, item_ids, strict=True)
    ):
        source_text = source.get("text")
        translated_text = translated.get("text")
        if not isinstance(source_text, str) or not isinstance(translated_text, str):
            continue
        issues = tuple(token_issues(source_text, translated_text))
        if not issues:
            continue
        positions.append(position)
        candidate_ids.append(item_id)
        candidate_items.append(source)
        payload_items.append(
            {
                "id": item_id,
                "value": source_text,
                "format_issues": list(issues),
            }
        )
        id_to_item[item_id] = source
        issues_by_id[item_id] = issues

    return FormatMismatchRetryBatch(
        positions=tuple(positions),
        item_ids=tuple(candidate_ids),
        items=tuple(candidate_items),
        payload={"items": payload_items},
        id_to_item=id_to_item,
        issues_by_id=issues_by_id,
    )
