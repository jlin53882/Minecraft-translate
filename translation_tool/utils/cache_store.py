"""cache_store.py

本模組處理兩件事：
1. 翻譯快取狀態（runtime state）的持有與測試重置
2. 對快取 dict 的最小讀寫語意 helper

設計原則：
- 狀態由這裡持有，`cache_manager.py` 僅作 façade
- 不碰 IO、不碰搜尋索引實作
- `get_cache_dict_ref()` 必須能回傳這裡持有的 live object
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, Optional


@dataclass
class CacheRuntimeState:
    """CacheRuntimeState 類別。

    用途：封裝與 CacheRuntimeState 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    translation_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_file_path: dict[str, Path] = field(default_factory=dict)
    initialized: bool = False
    session_new_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    is_dirty: dict[str, bool] = field(default_factory=dict)
    cache_lock: threading.RLock = field(default_factory=threading.RLock)


_RUNTIME = CacheRuntimeState()


def get_runtime_state() -> CacheRuntimeState:
    """取得此函式的工作（細節以程式碼為準）。

    回傳：依函式內 return path。
    """
    return _RUNTIME


def reset_runtime_state(cache_types: list[str]) -> CacheRuntimeState:
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`get_runtime_state`

    回傳：依函式內 return path。
    """
    state = get_runtime_state()
    state.translation_cache = {}
    state.cache_file_path = {}
    state.initialized = False
    state.session_new_entries = {k: {} for k in cache_types}
    state.is_dirty = {k: False for k in cache_types}
    return state


def ensure_runtime_maps(cache_types: list[str]) -> CacheRuntimeState:
    """確保此函式的工作（細節以程式碼為準）。

    - 主要包裝：`get_runtime_state`

    回傳：依函式內 return path。
    """
    state = get_runtime_state()
    if not state.session_new_entries:
        state.session_new_entries = {k: {} for k in cache_types}
    else:
        for k in cache_types:
            state.session_new_entries.setdefault(k, {})
    if not state.is_dirty:
        state.is_dirty = {k: False for k in cache_types}
    else:
        for k in cache_types:
            state.is_dirty.setdefault(k, False)
    return state


def get_cache_type_dict(
    cache_state: dict[str, dict[str, Any]], cache_type: str
) -> dict[str, Any]:
    """取得指定 cache_type 的可變字典（不存在就建立）。"""
    bucket = cache_state.get(cache_type)
    if not isinstance(bucket, dict):
        bucket = {}
        cache_state[cache_type] = bucket
    return bucket


def get_entry(cache_dict: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    """讀取單筆快取條目；若型別不正確則視為未命中。"""
    entry = cache_dict.get(key)
    return entry if isinstance(entry, dict) else None


def get_value(cache_dict: dict[str, Any], key: str) -> Optional[str]:
    """讀取條目的 dst 欄位，僅在其為字串時回傳。"""
    entry = get_entry(cache_dict, key)
    if not entry:
        return None
    value = entry.get("dst")
    return value if isinstance(value, str) else None


def add_entry(cache_dict: dict[str, Any], key: str, entry: dict[str, Any]) -> bool:
    """寫入或覆蓋條目；僅當 ``dst`` 發生變化時回傳 True。"""
    before = get_value(cache_dict, key)
    after = entry.get("dst") if isinstance(entry, dict) else None
    if before == after:
        return False
    cache_dict[key] = entry
    return True


def mark_dirty(is_dirty: dict[str, bool], cache_type: str) -> None:
    """將指定類型標記為已變更。"""
    is_dirty[cache_type] = True


def clear_dirty(is_dirty: dict[str, bool], cache_type: str) -> None:
    """清除指定類型的已變更標記。"""
    is_dirty[cache_type] = False


def get_session_entries(
    session_new_entries: dict[str, dict[str, Any]], cache_type: str
) -> dict[str, Any]:
    """取得本次執行階段的暫存條目容器（不存在就建立）。"""
    return get_cache_type_dict(session_new_entries, cache_type)


def flush_session_entries(
    session_new_entries: dict[str, dict[str, Any]], cache_type: str
) -> dict[str, Any]:
    """回傳暫存條目的淺拷貝並清空原容器。"""
    bucket = get_session_entries(session_new_entries, cache_type)
    data = bucket.copy()
    bucket.clear()
    return data
