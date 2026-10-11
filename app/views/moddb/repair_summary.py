"""User-facing result details for existing-translation repair runs."""

from __future__ import annotations


def repair_summary_detail_lines(summary: dict) -> list[str]:
    """Format representative units, source progress, newline candidates and cache receipts."""
    lines = []
    if "ai_representatives" in summary:
        lines.append(
            f"唯一 AI 修復項目：預計 {summary.get('ai_representatives', 0)} 個；"
            f"實際送入引擎 {summary.get('ai_submitted_items', 0)} 個"
            "（不等於實際 HTTP 請求次數）；"
            f"驗證通過 {summary.get('ai_validated_items', 0)}；"
            f"共用 AI 結果的來源項目 {summary.get('dedup_mapped_candidates', 0)}；"
            f"已成功套用共用結果 {summary.get('dedup_reused_candidates', 0)}"
        )
        lines.append(
            f"來源項目進度 {summary.get('processed_candidates', 0)}/"
            f"{summary.get('candidates', 0)}；未送出的來源項目 "
            f"{summary.get('not_submitted_candidates', 0)}；未處理的來源項目 "
            f"{summary.get('unprocessed_candidates', 0)}"
        )
    if summary.get("sync_other_versions"):
        lines.append(
            f"同步其他版本：{summary.get('synced_other_versions', 0)} 筆"
            "（原文與舊譯文都相同的條目）"
        )
    if summary.get("skipped_newline_only_candidates"):
        lines.append(
            f"略過 {summary.get('skipped_newline_only_candidates', 0)} 筆只有實際換行數不同的項目"
            "（不送 AI、不佔修復名額）"
        )
    if "planned_batches" in summary:
        lines.append(
            f"規劃批次：{summary.get('planned_batches', 0)}；"
            f"實際送出請求：{summary.get('batches', 0)}（重試可能增加）"
        )
    if summary.get("input_newline_mismatch_candidates") is not None:
        lines.append(
            f"候選中有輸入換行數不同 {summary.get('input_newline_mismatch_candidates', 0)} 筆；"
            f"其中另有硬格式問題 "
            f"{summary.get('input_newline_mismatch_with_other_hard_issues', 0)} 筆；"
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
