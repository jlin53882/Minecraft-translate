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
import time
from typing import Any, Dict, Optional


@dataclass
class CacheMetrics:
    """Cache 效能指標（PR66-A 監控用）。

    用於追蹤 cache 效能瓶頸，不影響業務邏輯。
    """

    # 計數器
    hit_count: Dict[str, int] = field(default_factory=dict)
    miss_count: Dict[str, int] = field(default_factory=dict)
    add_count: Dict[str, int] = field(default_factory=dict)

    # 耗時（毫秒）
    load_ms: Dict[str, float] = field(default_factory=dict)
    save_ms: Dict[str, float] = field(default_factory=dict)

    # 檔案資訊
    file_size_bytes: Dict[str, int] = field(default_factory=dict)

    # 可疑 collision（只記錄，不阻擋）
    collision_suspect_count: int = 0


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

    # PR66-A: 效能監控指標
    metrics: CacheMetrics = field(default_factory=CacheMetrics)


def get_metrics() -> CacheMetrics:
    """取得 cache metrics 實例。"""
    return _RUNTIME.metrics


def record_hit(cache_type: str) -> None:
    """記錄 cache 命中。"""
    metrics = get_metrics()
    metrics.hit_count[cache_type] = metrics.hit_count.get(cache_type, 0) + 1


def record_miss(cache_type: str) -> None:
    """記錄 cache 未命中。"""
    metrics = get_metrics()
    metrics.miss_count[cache_type] = metrics.miss_count.get(cache_type, 0) + 1


def record_add(cache_type: str) -> None:
    """記錄 cache 新增。"""
    metrics = get_metrics()
    metrics.add_count[cache_type] = metrics.add_count.get(cache_type, 0) + 1


def record_load_time(cache_type: str, ms: float) -> None:
    """記錄載入耗時。"""
    metrics = get_metrics()
    metrics.load_ms[cache_type] = ms


def record_save_time(cache_type: str, ms: float) -> None:
    """記錄儲存耗時。"""
    metrics = get_metrics()
    metrics.save_ms[cache_type] = ms


def record_file_size(cache_type: str, bytes: int) -> None:
    """記錄檔案大小。"""
    metrics = get_metrics()
    metrics.file_size_bytes[cache_type] = bytes


def record_collision_suspect() -> None:
    """記錄可疑的 key collision。"""
    metrics = get_metrics()
    metrics.collision_suspect_count += 1


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
