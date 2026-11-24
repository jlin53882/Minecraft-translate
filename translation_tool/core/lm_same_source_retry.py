"""Utilities for retrying translations that initially match their source text."""

from __future__ import annotations

SAME_SOURCE_RETRY_INSTRUCTION = """以下項目上一次翻譯結果與原文完全相同，請再次確認是否確實應保持原文。
若是 Minecraft 模組名稱、專有名詞、縮寫、技術名稱，或本來就不應翻譯的內容，可以保持原文。
若內容具有可翻譯的人類語意，請翻譯成繁體中文（台灣用語）；不要為了和原文不同而強行翻譯專有名詞。
所有 ID、格式符號與 structured-output contract 必須保持原規則。"""


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
