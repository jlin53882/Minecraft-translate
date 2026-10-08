"""Utilities for retrying translations that initially match their source text."""

from __future__ import annotations

from dataclasses import dataclass

SAME_SOURCE_RETRY_INSTRUCTION = """以下項目上一次翻譯結果與原文完全相同，請再次確認是否確實應保持原文。
若是 Minecraft 模組名稱、專有名詞、縮寫、技術名稱，或本來就不應翻譯的內容，可以保持原文。
若內容具有可翻譯的人類語意，請翻譯成繁體中文（台灣用語）；不要為了和原文不同而強行翻譯專有名詞。
所有 ID、格式符號與 structured-output contract 必須保持原規則。"""


@dataclass(frozen=True)
class SameSourceRetryBatch:
    """A candidate-only request and the positions needed to restore batch order."""

    positions: tuple[int, ...]
    item_ids: tuple[str, ...]
    items: tuple[dict, ...]
    payload: dict
    id_to_item: dict[str, dict]


def build_same_source_retry_batch(
    source_items: list[dict],
    translated_items: list[dict],
    item_ids: list[str],
) -> SameSourceRetryBatch:
    """Select alphabetic same-as-source results, excluding existing failure fallbacks."""
    positions: list[int] = []
    candidate_ids: list[str] = []
    candidate_items: list[dict] = []
    payload_items: list[dict[str, str]] = []
    id_to_item: dict[str, dict] = {}

    for position, (source, translated, item_id) in enumerate(
        zip(source_items, translated_items, item_ids, strict=True)
    ):
        source_text = source.get("text")
        if (
            translated.get("_untranslated")
            or not isinstance(source_text, str)
            or translated.get("text") != source_text
            or not any(char.isalpha() for char in source_text)
        ):
            continue
        positions.append(position)
        candidate_ids.append(item_id)
        candidate_items.append(source)
        payload_items.append({"id": item_id, "value": source_text})
        id_to_item[item_id] = source

    return SameSourceRetryBatch(
        positions=tuple(positions),
        item_ids=tuple(candidate_ids),
        items=tuple(candidate_items),
        payload={"items": payload_items},
        id_to_item=id_to_item,
    )


def merge_same_source_retry_results(
    original_results: list[dict],
    candidate_positions: tuple[int, ...],
    retry_results: list[dict],
) -> list[dict]:
    """Replace only candidate positions while preserving original result order."""
    if len(candidate_positions) != len(retry_results):
        raise ValueError("Retry results must match the candidate count")
    merged = list(original_results)
    for position, retry_item in zip(candidate_positions, retry_results, strict=True):
        merged[position] = retry_item
    return merged
