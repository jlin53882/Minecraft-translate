from typing import Any, Optional


def get_cache_type_dict(cache_state: dict[str, dict[str, Any]], cache_type: str) -> dict[str, Any]:
    """Return mutable dict ref for ``cache_type`` (create if missing)."""
    bucket = cache_state.get(cache_type)
    if not isinstance(bucket, dict):
        bucket = {}
        cache_state[cache_type] = bucket
    return bucket


def get_entry(cache_dict: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    entry = cache_dict.get(key)
    return entry if isinstance(entry, dict) else None


def get_value(cache_dict: dict[str, Any], key: str) -> Optional[str]:
    entry = get_entry(cache_dict, key)
    if not entry:
        return None
    value = entry.get("dst")
    return value if isinstance(value, str) else None


def add_entry(cache_dict: dict[str, Any], key: str, entry: dict[str, Any]) -> bool:
    """Upsert entry. Return True only when ``dst`` value changed."""
    before = get_value(cache_dict, key)
    after = entry.get("dst") if isinstance(entry, dict) else None
    if before == after:
        return False
    cache_dict[key] = entry
    return True


def mark_dirty(is_dirty: dict[str, bool], cache_type: str) -> None:
    is_dirty[cache_type] = True


def clear_dirty(is_dirty: dict[str, bool], cache_type: str) -> None:
    is_dirty[cache_type] = False


def get_session_entries(session_new_entries: dict[str, dict[str, Any]], cache_type: str) -> dict[str, Any]:
    return get_cache_type_dict(session_new_entries, cache_type)


def flush_session_entries(session_new_entries: dict[str, dict[str, Any]], cache_type: str) -> dict[str, Any]:
    bucket = get_session_entries(session_new_entries, cache_type)
    data = bucket.copy()
    bucket.clear()
    return data
