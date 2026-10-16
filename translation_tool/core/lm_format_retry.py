"""Utilities for repairing format-token and newline mismatches."""

from __future__ import annotations

from dataclasses import dataclass

from translation_tool.translation_db.quality import token_issues

FORMAT_MISMATCH_RETRY_INSTRUCTION = """請只修正每個項目列出的格式不一致問題。
如果是換行問題，請自行比對原文，判斷並輸出正確數量與位置的實際換行字元；不要把實際換行改成文字形式的 \\n。
如果是特殊格式問題，請視需要重新翻譯該項目，同時完整保留原文中的每一個 Minecraft 格式碼、printf 佔位符、Patchouli 巨集，以及文字形式的 \\n。
不要輸出說明文字。只回傳符合結構化 JSON 格式的結果，保留相同的項目 ID，並為每個項目輸出一個翻譯值。"""


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
