"""app/views/extractor/extract_summary.py：JAR 提取的預覽與結果文字（提取頁與一鍵流水線共用）。

預覽與實際提取用同一組單位：成功與跳過算檔案、失敗算 JAR。預覽列出檔案數（Lang／Book 分列）
與 JAR 分類（有可提取檔案、沒有可提取檔案、讀取失敗）；「內容相同而跳過」只有實際寫入時才知道。
純函式，不依賴 Flet。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

FAILED_NAMES_SHOWN = 5


def preview_file_count(row: Mapping[str, Any], mode: str) -> int:
    if mode == "dual":
        return int(row.get("lang_count", 0) or 0) + int(row.get("book_count", 0) or 0)
    return int(row.get("count", 0) or 0)


def preview_summary_lines(result: Mapping[str, Any], mode: str) -> list[str]:
    """預覽結果的摘要行：檔案數、大小、JAR 分類（含讀取失敗）與實際提取時的差異提醒。"""
    rows: Sequence[Mapping[str, Any]] = result.get("preview_results", []) or []
    failed: Sequence[Mapping[str, Any]] = result.get("failed_jars", []) or []
    failed_names = {str(item.get("jar")) for item in failed}
    total_files = int(result.get("total_files", 0) or 0)
    if mode == "dual":
        lang = sum(int(r.get("lang_count", 0) or 0) for r in rows)
        book = sum(int(r.get("book_count", 0) or 0) for r in rows)
        files = f"預計提取 {total_files:,} 個檔案（Lang {lang:,}／Book {book:,}）"
    else:
        files = f"預計提取 {total_files:,} 個檔案"
    size = float(result.get("total_size_mb", 0) or 0)
    with_files = sum(1 for r in rows if preview_file_count(r, mode) > 0)
    empty = sum(
        1
        for r in rows
        if preview_file_count(r, mode) == 0 and str(r.get("jar")) not in failed_names
    )
    total_jars = int(result.get("total_jars", len(rows)) or 0)
    lines = [
        f"{files}，約 {size:.2f} MB",
        (
            f"掃描 {total_jars:,} 個 JAR：{with_files:,} 個有可提取檔案、"
            f"{empty:,} 個沒有、{len(failed):,} 個讀取失敗"
        ),
    ]
    if failed:
        shown = "、".join(
            f"{item.get('jar')}（{item.get('error')}）"
            for item in failed[:FAILED_NAMES_SHOWN]
        )
        more = "…" if len(failed) > FAILED_NAMES_SHOWN else ""
        lines.append(f"讀取失敗：{shown}{more}")
    lines.append("實際提取時，內容與輸出相同的檔案會跳過（不計入成功）")
    return lines
