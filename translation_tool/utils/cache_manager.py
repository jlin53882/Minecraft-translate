"""cache_manager.py（快取管理 façade）

PR39B：runtime state 已下沉到 `cache_store.py`，本模組僅保留正式 API 與流程協調。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from . import cache_shards, cache_store
from .cache_loader import load_cache_type
from .cache_overview import (
    build_cache_overview,
    get_active_shard_id as _get_active_shard_id_impl,
)
from .cache_search_facade import CacheSearchFacade
from .config_manager import load_config, resolve_project_path

log = logging.getLogger(__name__)

CACHE_TYPES = ["lang", "patchouli", "ftbquests", "kubejs", "md"]
ROLLING_SHARD_SIZE = 2500
ACTIVE_SHARD_FILE = ".active"
_CACHE_DIR_NAME = "快取資料夾"

_search_facade: CacheSearchFacade | None = None
_search_facade_lock = threading.Lock()

__all__ = [
    "CACHE_TYPES",
    "ROLLING_SHARD_SIZE",
    "ACTIVE_SHARD_FILE",
    "initialize_translation_cache",
    "is_cache_initialized",
    "reload_translation_cache",
    "reload_translation_cache_type",
    "save_translation_cache",
    "add_to_cache",
    "get_from_cache",
    "get_cache_entry",
    "get_cache_dict_ref",
    "get_cache_overview",
    "get_session_new_count",
    "get_active_shard_id",
    "force_rotate_shard",
    "get_search_engine",
    "rebuild_search_index",
    "rebuild_search_index_for_type",
    "search_cache",
    "find_similar_translations",
]


def _state():
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`ensure_runtime_maps`

    回傳：依函式內 return path。
    """
    return cache_store.ensure_runtime_maps(CACHE_TYPES)


def _get_cache_root() -> Path:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`resolve_project_path`

    回傳：依函式內 return path。
    """
    translation_config = load_config().get("translator", {})
    cache_dir_name = translation_config.get("cache_directory", _CACHE_DIR_NAME)
    return resolve_project_path(cache_dir_name)


def _load_cache_type(cache_type: str):
    """載入此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `load_cache_type`

    回傳：None
    """
    state = _state()
    translation_config = load_config().get("translator", {})
    load_cache_type(
        cache_type,
        translation_cache=state.translation_cache,
        cache_file_path=state.cache_file_path,
        cache_root=_get_cache_root(),
        parallel_workers=translation_config.get("parallel_execution_workers", 4),
        logger=log,
    )


def initialize_translation_cache():
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`

    回傳：None
    """
    state = _state()
    if state.initialized:
        return
    try:
        for cache_type in CACHE_TYPES:
            _load_cache_type(cache_type)
        state.initialized = True
    except Exception as e:
        log.error(f"快取系統初始化失敗: {e}", exc_info=True)


def is_cache_initialized() -> bool:
    """判斷此函式的工作（細節以程式碼為準）。

    - 主要包裝：`bool`

    回傳：依函式內 return path。
    """
    return bool(_state().initialized)


def reload_translation_cache():
    """重新載入此函式的工作（細節以程式碼為準）。

    - 主要包裝：`reset_runtime_state`, `initialize_translation_cache`

    回傳：None
    """
    state = cache_store.reset_runtime_state(CACHE_TYPES)
    with state.cache_lock:
        pass
    initialize_translation_cache()


def reload_translation_cache_type(cache_type: str):
    """重新載入此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `initialize_translation_cache`, `_load_cache_type`

    回傳：None
    """
    if cache_type not in CACHE_TYPES:
        return
    state = _state()
    initialize_translation_cache()
    with state.cache_lock:
        state.translation_cache[cache_type] = {}
        cache_store.get_session_entries(state.session_new_entries, cache_type).clear()
        cache_store.clear_dirty(state.is_dirty, cache_type)
    _load_cache_type(cache_type)


def _save_entries_to_active_shards(
    cache_type: str, entries: dict, force_new_shard: bool = False
):
    """保存此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `_save_entries_to_active_shards`

    回傳：依函式內 return path。
    """
    state = _state()
    type_dir = state.cache_file_path[cache_type].parent
    return cache_shards._save_entries_to_active_shards(
        type_dir=type_dir,
        cache_type=cache_type,
        entries=entries,
        rolling_shard_size=ROLLING_SHARD_SIZE,
        active_shard_file=ACTIVE_SHARD_FILE,
        force_new_shard=force_new_shard,
        logger=log,
    )


def save_translation_cache(cache_type: str, write_new_shard: bool = True):
    """保存此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`

    回傳：None
    """
    if not load_config().get("translator", {}).get("enable_cache_saving", True):
        return

    state = _state()
    with state.cache_lock:
        session_entries = cache_store.get_session_entries(
            state.session_new_entries, cache_type
        )
        if not session_entries:
            return
        data_to_save = cache_store.flush_session_entries(
            state.session_new_entries, cache_type
        )
        cache_store.clear_dirty(state.is_dirty, cache_type)

    try:
        save_path = state.cache_file_path.get(cache_type)
        if not save_path:
            return
        _save_entries_to_active_shards(
            cache_type, data_to_save, force_new_shard=write_new_shard
        )
    except Exception as e:
        log.error(f"❌ 儲存 {cache_type} 失敗: {e}", exc_info=True)


def _get_active_shard_path(cache_type: str) -> Path:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `_get_active_shard_path`

    回傳：依函式內 return path。
    """
    state = _state()
    type_dir = state.cache_file_path[cache_type].parent
    return cache_shards._get_active_shard_path(
        type_dir=type_dir,
        cache_type=cache_type,
        active_shard_file=ACTIVE_SHARD_FILE,
    )


def add_to_cache(
    cache_type: str,
    key: str,
    src: str,
    dst: str,
    *,
    mod: str | None = None,
    path: str | None = None,
):
    """加入此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`

    回傳：None
    """
    if not key or not dst:
        return

    state = _state()
    with state.cache_lock:
        cache = cache_store.get_cache_type_dict(state.translation_cache, cache_type)
        entry = {"src": src, "dst": dst}
        if mod:
            entry["mod"] = mod
        if path:
            entry["path"] = path
        changed = cache_store.add_entry(cache, key, entry)
        if changed:
            session_entries = cache_store.get_session_entries(
                state.session_new_entries, cache_type
            )
            session_entries[key] = entry
            cache_store.mark_dirty(state.is_dirty, cache_type)


def get_from_cache(cache_type: str, key: str) -> Optional[str]:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `get_value`

    回傳：依函式內 return path。
    """
    state = _state()
    if not state.initialized:
        return None
    cache = state.translation_cache.get(cache_type)
    if not isinstance(cache, dict):
        return None
    return cache_store.get_value(cache, key)


def get_cache_entry(cache_type: str, key: str) -> Optional[Dict[str, Any]]:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `get_entry`

    回傳：依函式內 return path。
    """
    state = _state()
    if not state.initialized:
        return None
    cache = state.translation_cache.get(cache_type)
    if not isinstance(cache, dict):
        return None
    return cache_store.get_entry(cache, key)


def get_cache_dict_ref(cache_type: str) -> Dict[str, Dict[str, Any]]:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`

    回傳：依函式內 return path。
    """
    state = _state()
    if not state.initialized:
        return {}
    cache = state.translation_cache.get(cache_type)
    return cache if isinstance(cache, dict) else {}


def get_session_new_count(cache_type: str) -> int:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`

    回傳：依函式內 return path。
    """
    state = _state()
    with state.cache_lock:
        return len(
            cache_store.get_session_entries(state.session_new_entries, cache_type)
        )


def get_active_shard_id(cache_type: str) -> str:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `_get_active_shard_id_impl`

    回傳：依函式內 return path。
    """
    state = _state()
    return _get_active_shard_id_impl(
        state.cache_file_path, cache_type, ACTIVE_SHARD_FILE
    )


def get_cache_overview() -> Dict[str, Any]:
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`initialize_translation_cache`, `_state`

    回傳：依函式內 return path。
    """
    initialize_translation_cache()
    state = _state()
    with state.cache_lock:
        return build_cache_overview(
            cache_types=CACHE_TYPES,
            translation_cache=state.translation_cache,
            is_dirty=state.is_dirty,
            session_new_entries=state.session_new_entries,
            cache_file_path=state.cache_file_path,
            rolling_shard_size=ROLLING_SHARD_SIZE,
            active_shard_file=ACTIVE_SHARD_FILE,
            get_active_shard_path=_get_active_shard_path,
            load_config=load_config,
            cache_dir_name=_CACHE_DIR_NAME,
            resolve_project_path=resolve_project_path,
        )


def force_rotate_shard(cache_type: str) -> bool:
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`initialize_translation_cache`, `_state`

    回傳：依函式內 return path。
    """
    initialize_translation_cache()
    state = _state()
    if cache_type not in CACHE_TYPES:
        return False
    try:
        with state.cache_lock:
            type_dir = state.cache_file_path[cache_type].parent
            active_file = type_dir / ACTIVE_SHARD_FILE
            if not active_file.exists():
                _ = _get_active_shard_path(cache_type)
            cur = int((active_file.read_text(encoding="utf-8") or "1").strip())
            active_file.write_text(f"{cur + 1:05d}", encoding="utf-8")
        return True
    except Exception:
        return False


def _get_search_facade() -> CacheSearchFacade:
    """取得此函式的工作（細節以程式碼為準）。

    回傳：依函式內 return path。
    """
    global _search_facade
    if _search_facade is None:
        with _search_facade_lock:
            if _search_facade is None:
                _search_facade = CacheSearchFacade(_get_cache_root, log)
    return _search_facade


def get_search_engine():
    """取得此函式的工作（細節以程式碼為準）。

    - 主要包裝：`get_search_engine`

    回傳：依函式內 return path。
    """
    return _get_search_facade().get_search_engine()


def rebuild_search_index():
    """重建此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `rebuild_search_index`

    回傳：依函式內 return path。
    """
    state = _state()
    return _get_search_facade().rebuild_search_index(
        CACHE_TYPES, state.translation_cache
    )


def rebuild_search_index_for_type(cache_type: str):
    """重建此函式的工作（細節以程式碼為準）。

    - 主要包裝：`_state`, `rebuild_search_index_for_type`

    回傳：依函式內 return path。
    """
    state = _state()
    return _get_search_facade().rebuild_search_index_for_type(
        cache_type, CACHE_TYPES, state.translation_cache
    )


def search_cache(
    query: str, cache_type: str = None, limit: int = 50, use_fuzzy: bool = True
) -> list:
    """處理此函式的工作（細節以程式碼為準）。

    - 主要包裝：`search_cache`

    回傳：依函式內 return path。
    """
    return _get_search_facade().search_cache(
        query=query, cache_type=cache_type, limit=limit, use_fuzzy=use_fuzzy
    )


def find_similar_translations(
    text: str, cache_type: str = None, threshold: float = 0.6, limit: int = 20
) -> list:
    """找出此函式的工作（細節以程式碼為準）。

    - 主要包裝：`find_similar_translations`

    回傳：依函式內 return path。
    """
    return _get_search_facade().find_similar_translations(
        text=text, cache_type=cache_type, threshold=threshold, limit=limit
    )


initialize_translation_cache()
_state_obj = _state()
log.info(
    "快取統計："
    + ", ".join(f"{k}={len(v)}" for k, v in _state_obj.translation_cache.items())
)
