from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable

import orjson as json


def get_active_shard_id(cache_file_path: dict[str, Path], cache_type: str, active_shard_file: str) -> str:
    try:
        type_dir = cache_file_path.get(cache_type, Path(".")).parent
        active_file = type_dir / active_shard_file
        if active_file.exists():
            return active_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def build_cache_overview(
    *,
    cache_types: list[str],
    translation_cache: dict[str, dict[str, Any]],
    is_dirty: dict[str, bool],
    session_new_entries: dict[str, dict[str, Any]],
    cache_file_path: dict[str, Path],
    rolling_shard_size: int,
    active_shard_file: str,
    get_active_shard_path: Callable[[str], Path],
    load_config: Callable[[], dict],
    cache_dir_name: str,
    resolve_project_path: Callable[[str], Path],
) -> dict[str, Any]:
    out_types: dict[str, Any] = {}
    total_entries = 0
    dirty_type_count = 0

    for cache_type in cache_types:
        entries = len(translation_cache.get(cache_type, {}))
        dirty = bool(is_dirty.get(cache_type, False))
        session_new = len(session_new_entries.get(cache_type, {}))
        active_id = get_active_shard_id(cache_file_path, cache_type, active_shard_file)

        active_entries = 0
        try:
            active_path = get_active_shard_path(cache_type)
            if active_path.exists():
                active_data = json.loads(active_path.read_bytes())
                if isinstance(active_data, dict):
                    active_entries = len(active_data)
        except Exception:
            active_entries = 0

        if dirty:
            dirty_type_count += 1
        total_entries += entries

        out_types[cache_type] = {
            "entries_count": entries,
            "session_new_count": session_new,
            "is_dirty": dirty,
            "active_shard_id": active_id,
            "active_shard_entries": active_entries,
            "shard_capacity": rolling_shard_size,
        }

    try:
        translation_config = load_config().get("translator", {})
        cache_root = str(resolve_project_path(translation_config.get("cache_directory", cache_dir_name)).resolve())
    except Exception:
        cache_root = ""

    return {
        "cache_root": cache_root,
        "total_entries": total_entries,
        "dirty_type_count": dirty_type_count,
        "types": out_types,
        "last_reload_at": datetime.datetime.now().strftime("%H:%M:%S"),
        "last_save_at": None,
    }
