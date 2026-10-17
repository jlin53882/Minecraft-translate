"""Build one quality-repair retry for a translated batch."""

from __future__ import annotations

from dataclasses import dataclass

from translation_tool.core.lm_format_policy import (
    build_payload_item,
    kind_for_item,
)
from translation_tool.plugins.shared.rich_text_shield import unshield_text
from translation_tool.translation_db.quality import (
    guide_structure_issues,
    token_issues_for_kind,
)

QUALITY_RETRY_INSTRUCTION = (
    "這是目前批次唯一一次的翻譯品質修復重試，請同時處理每個項目列出的問題。\n"
    "如果 retry_reasons 表示翻譯結果與原文相同，請重新翻譯，不要直接複製原文。\n"
    "format_issues 是程式依原文與譯文比對產生的硬性問題，不可以忽略或重新解讀；"
    "請依目前 payload 的原文重新翻譯，不要沿用上一版錯誤格式。\n"
    "合法的同一段落軟折行不需要恢復成英文原本的行數；只有 format_issues 指出的硬結構需要修正。\n"
    "請維持原本翻譯語意，不要輸出說明文字，只回傳相同項目 ID 的結構化 JSON 翻譯結果。"
)

# Keep this compatibility name for private/external imports; active batch flow uses
# the combined quality retry instruction below.
FORMAT_MISMATCH_RETRY_INSTRUCTION = QUALITY_RETRY_INSTRUCTION


@dataclass(frozen=True)
class QualityRetryBatch:
    """A candidate-only retry containing all quality failure categories."""

    positions: tuple[int, ...]
    item_ids: tuple[str, ...]
    items: tuple[dict, ...]
    payload: dict
    id_to_item: dict[str, dict]


def _item_kind(item: dict) -> str | None:
    kind = item.get("_kind")
    if not kind:
        ident = item.get("db_ident")
        kind = ident[0] if ident else None
    return str(kind) if kind else None


def quality_issues(source_item: dict, translated_text: str) -> list[str]:
    """LM 結果的品質判斷：佔位符空間的數量標記，加上還原後的指南結構標記。

    ``source_item`` 是送出的批次項目。帶有 ``_shielded`` 的指南段落，數量標記在佔位符
    空間比對；指南結構標記則先還原行內保護，與寫入與審查用的規則相同。
    """
    kind = kind_for_item(source_item)
    issues = token_issues_for_kind(source_item.get("text"), translated_text, kind=kind)
    shielded = source_item.get("_shielded")
    raw_translated = (
        unshield_text(translated_text, shielded.shields)
        if shielded is not None
        else translated_text
    )
    raw_source = str(source_item.get("source_text") or "")
    return [
        *issues,
        *guide_structure_issues(raw_source, raw_translated, kind=kind),
    ]


def build_quality_retry_batch(
    source_items: list[dict],
    translated_items: list[dict],
    item_ids: list[str],
    *,
    retry_same_as_source: bool,
) -> QualityRetryBatch:
    """Combine same-as-source and format-invalid rows into one retry payload."""
    positions: list[int] = []
    candidate_ids: list[str] = []
    candidate_items: list[dict] = []
    payload_items: list[dict[str, object]] = []
    id_to_item: dict[str, dict] = {}

    for position, (source, translated, item_id) in enumerate(
        zip(source_items, translated_items, item_ids, strict=True)
    ):
        source_text = source.get("text")
        translated_text = translated.get("text")
        if not isinstance(source_text, str) or not isinstance(translated_text, str):
            continue

        retry_reasons: list[str] = []
        if (
            retry_same_as_source
            and not translated.get("_untranslated")
            and translated_text == source_text
            and any(char.isalpha() for char in source_text)
        ):
            retry_reasons.append("翻譯結果與原文相同")

        issues = quality_issues(source, translated_text)
        if not retry_reasons and not issues:
            continue

        payload_item = build_payload_item(
            source,
            item_id,
            retry_reasons=retry_reasons,
            format_issues=issues,
        )

        positions.append(position)
        candidate_ids.append(item_id)
        candidate_items.append(source)
        payload_items.append(payload_item)
        id_to_item[item_id] = source

    return QualityRetryBatch(
        positions=tuple(positions),
        item_ids=tuple(candidate_ids),
        items=tuple(candidate_items),
        payload={"items": payload_items},
        id_to_item=id_to_item,
    )
