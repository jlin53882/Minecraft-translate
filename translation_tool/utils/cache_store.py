from typing import Any, Optional


def get_cache_type_dict(cache_state: dict[str, dict[str, Any]], cache_type: str) -> dict[str, Any]:
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


def get_session_entries(session_new_entries: dict[str, dict[str, Any]], cache_type: str) -> dict[str, Any]:
    """取得本次執行階段的暫存條目容器（不存在就建立）。"""
    return get_cache_type_dict(session_new_entries, cache_type)


def flush_session_entries(session_new_entries: dict[str, dict[str, Any]], cache_type: str) -> dict[str, Any]:
    """回傳暫存條目的淺拷貝並清空原容器。"""
    bucket = get_session_entries(session_new_entries, cache_type)
    data = bucket.copy()
    bucket.clear()
    return data
