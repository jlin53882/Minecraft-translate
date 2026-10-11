"""快取類型狀態的共用文字：總覽卡與查詢頁分片卡使用同一組措辭與數字格式。"""

from __future__ import annotations

DEFAULT_SHARD_CAPACITY = 2500


def dirty_label(dirty: bool) -> str:
    return "有變更" if dirty else "無變更"


def active_shard_usage(state: dict) -> tuple[int, int]:
    """目前寫入中的分片已寫入筆數與容量。"""
    entries = int(state.get("active_shard_entries", 0) or 0)
    capacity = int(state.get("shard_capacity", DEFAULT_SHARD_CAPACITY) or 0)
    return entries, capacity or DEFAULT_SHARD_CAPACITY


def entries_line(state: dict) -> str:
    """整個分類的筆數；本次新增為 0 時不顯示。"""
    text = f"筆數 {int(state.get('entries_count', 0) or 0):,}"
    new_count = int(state.get("session_new_count", 0) or 0)
    if new_count:
        text += f"　本次新增 {new_count:,}"
    return text


def shard_line(state: dict) -> str:
    """目前寫入分片的編號、分片總數與該分片的寫入量（不是整個分類的用量）。"""
    entries, capacity = active_shard_usage(state)
    shard_count = state.get("shard_count")
    total = f"（共 {int(shard_count):,} 片）" if shard_count else ""
    return (
        f"目前寫入分片 {state.get('active_shard_id') or '-'}{total}"
        f"　已寫入 {entries:,} / {capacity:,} 筆"
    )
