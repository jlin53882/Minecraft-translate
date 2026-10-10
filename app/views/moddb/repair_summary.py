"""User-facing result details for existing-translation repair runs."""

from __future__ import annotations


def repair_summary_detail_lines(summary: dict) -> list[str]:
    """Format representative units, source progress, newline skips and cache receipts."""
    lines = []
    if "ai_representatives" in summary:
        lines.append(
            f"AI 代表計劃 {summary.get('ai_representatives', 0)}；"
            f"實際送入引擎 {summary.get('ai_submitted_items', 0)} items"
            "（不是 API/HTTP 次數）；"
            f"驗證通過 {summary.get('ai_validated_items', 0)}；"
            f"等價映射候選 {summary.get('dedup_mapped_candidates', 0)}；"
            f"已共用驗證結果 {summary.get('dedup_reused_candidates', 0)}"
        )
        lines.append(
            f"來源列進度 {summary.get('processed_candidates', 0)}/"
            f"{summary.get('candidates', 0)}；未送出候選 "
            f"{summary.get('not_submitted_candidates', 0)}；未處理候選 "
            f"{summary.get('unprocessed_candidates', 0)}"
        )
    if summary.get("skipped_input_newline_mismatch") is not None:
        lines.append(
            f"輸入實體換行異常跳過 {summary.get('skipped_input_newline_mismatch', 0)} 筆；"
            f"其中同時有其他硬格式問題 "
            f"{summary.get('skipped_newline_with_other_hard_issues', 0)} 筆；"
            f"可逐筆審查 AI 結果 {summary.get('reviewable_results', 0)} 筆"
        )
    if "cache_keys_changed" in summary:
        changed = summary.get("cache_keys_changed")
        saved = summary.get("cache_keys_saved")
        lines.append(
            "快取 key 變更 "
            f"{changed if changed is not None else '未知'}；成功落盤 "
            f"{saved if saved is not None else '未知'}"
            + (
                f"（{summary['cache_stats_note']}）"
                if summary.get("cache_stats_note")
                else ""
            )
            + f"；新增失敗來源列 {summary.get('cache_add_failed', 0)}；"
            f"尚未確認落盤 keys {summary.get('cache_save_failed', 0)}"
        )
    return lines
