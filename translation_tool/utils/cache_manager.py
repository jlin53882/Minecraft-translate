"""cache_manager.py（快取管理 façade）

定位：本模組提供翻譯快取的「對外 façade」，讓核心翻譯流程只需要：
- initialize / reload
- get / add
- save
- search / rebuild index

歷史背景：PR1～PR12 期間逐步把快取機制改為「依類型分資料夾 + rolling shards」，
並將部分責任抽到：
- `cache_shards.py`：分片檔的讀寫/旋轉（IO 層）
- `cache_store.py`：純 dict 操作（狀態層）
- `cache_search.py`：搜尋索引（SQLite/FTS）

PR37 目標：
- 保留 `cache_manager.py` 作為對外 façade
- 把「載入 / overview / search façade」責任下沉到 helper 模組
- 保留既有私有狀態名稱，避免打破現有 caller 與 tests
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import orjson as json

from . import cache_shards, cache_store
from .cache_loader import load_cache_type
from .cache_overview import build_cache_overview, get_active_shard_id as _get_active_shard_id_impl
from .cache_search_facade import CacheSearchFacade
from .config_manager import load_config, resolve_project_path

log = logging.getLogger(__name__)

# --- 全域變數（相容層：Phase 1 保留） ---
_cache_lock = threading.Lock()

CACHE_TYPES = ["lang", "patchouli", "ftbquests", "kubejs", "md"]

_is_dirty: dict[str, bool] = {k: False for k in CACHE_TYPES}
_session_new_entries: dict[str, dict] = {k: {} for k in CACHE_TYPES}

_CACHE_DIR_NAME = "快取資料夾"

_translation_cache: dict[str, dict] = {}
_cache_file_path: dict[str, Path] = {}
_initialized = False

ROLLING_SHARD_SIZE = 2500
ACTIVE_SHARD_FILE = ".active"

_search_facade: CacheSearchFacade | None = None
_search_facade_lock = threading.Lock()


__all__ = [
    "CACHE_TYPES",
    "ROLLING_SHARD_SIZE",
    "ACTIVE_SHARD_FILE",
    "initialize_translation_cache",
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


def _get_cache_root() -> Path:
    """取得快取根目錄。"""
    translation_config = load_config().get("translator", {})
    cache_dir_name = translation_config.get("cache_directory", _CACHE_DIR_NAME)
    return resolve_project_path(cache_dir_name)


def _load_cache_type(cache_type: str):
    translation_config = load_config().get("translator", {})
    load_cache_type(
        cache_type,
        translation_cache=_translation_cache,
        cache_file_path=_cache_file_path,
        cache_root=_get_cache_root(),
        parallel_workers=translation_config.get("parallel_execution_workers", 4),
        logger=log,
    )


def initialize_translation_cache():
    """初始化快取（只做一次）。"""
    global _initialized
    if _initialized:
        return

    try:
        for cache_type in CACHE_TYPES:
            _load_cache_type(cache_type)
        _initialized = True
    except Exception as e:
        log.error(f"快取系統初始化失敗: {e}", exc_info=True)


def reload_translation_cache():
    """強制重新讀取所有快取分片。"""
    global _translation_cache, _cache_file_path, _initialized
    with _cache_lock:
        _translation_cache = {}
        _cache_file_path = {}
        _initialized = False
        _session_new_entries.clear()
        for k in CACHE_TYPES:
            cache_store.get_session_entries(_session_new_entries, k)
            cache_store.clear_dirty(_is_dirty, k)
    initialize_translation_cache()


def reload_translation_cache_type(cache_type: str):
    """只重新載入單一 cache_type。"""
    if cache_type not in CACHE_TYPES:
        return

    initialize_translation_cache()
    with _cache_lock:
        _translation_cache[cache_type] = {}
        cache_store.get_session_entries(_session_new_entries, cache_type).clear()
        cache_store.clear_dirty(_is_dirty, cache_type)

    _load_cache_type(cache_type)


def _write_json_atomic(path: Path, data: dict):
    return cache_shards._write_json_atomic(path, data)


def _save_entries_to_active_shards(cache_type: str, entries: dict, force_new_shard: bool = False):
    type_dir = _cache_file_path[cache_type].parent
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
    if not load_config().get("translator", {}).get("enable_cache_saving", True):
        return

    with _cache_lock:
        session_entries = cache_store.get_session_entries(_session_new_entries, cache_type)
        if not session_entries:
            return

        data_to_save = cache_store.flush_session_entries(_session_new_entries, cache_type)
        cache_store.clear_dirty(_is_dirty, cache_type)

    try:
        save_path = _cache_file_path.get(cache_type)
        if not save_path:
            return

        _save_entries_to_active_shards(
            cache_type,
            data_to_save,
            force_new_shard=write_new_shard,
        )
    except Exception as e:
        log.error(f"❌ 儲存 {cache_type} 失敗: {e}", exc_info=True)


def _get_active_shard_path(cache_type: str) -> Path:
    type_dir = _cache_file_path[cache_type].parent
    return cache_shards._get_active_shard_path(
        type_dir=type_dir,
        cache_type=cache_type,
        active_shard_file=ACTIVE_SHARD_FILE,
    )


def _rotate_shard_if_needed(cache_type: str, data: dict):
    type_dir = _cache_file_path[cache_type].parent
    return cache_shards._rotate_shard_if_needed(
        type_dir=type_dir,
        cache_type=cache_type,
        data=data,
        rolling_shard_size=ROLLING_SHARD_SIZE,
        active_shard_file=ACTIVE_SHARD_FILE,
        logger=log,
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
    """寫入/更新單筆翻譯快取。"""
    if not key or not dst:
        return

    with _cache_lock:
        cache = cache_store.get_cache_type_dict(_translation_cache, cache_type)
        entry = {"src": src, "dst": dst}
        if mod:
            entry["mod"] = mod
        if path:
            entry["path"] = path

        changed = cache_store.add_entry(cache, key, entry)
        if changed:
            session_entries = cache_store.get_session_entries(_session_new_entries, cache_type)
            session_entries[key] = entry
            cache_store.mark_dirty(_is_dirty, cache_type)


def get_from_cache(cache_type: str, key: str) -> Optional[str]:
    if not _initialized:
        return None

    cache = _translation_cache.get(cache_type)
    if not isinstance(cache, dict):
        return None

    return cache_store.get_value(cache, key)


def get_cache_entry(cache_type: str, key: str) -> Optional[Dict[str, Any]]:
    if not _initialized:
        return None
    cache = _translation_cache.get(cache_type)
    if not isinstance(cache, dict):
        return None
    return cache_store.get_entry(cache, key)


def get_cache_dict_ref(cache_type: str) -> Dict[str, Dict[str, Any]]:
    """回傳底層快取 dict 的 live reference（不可改成 copy）。"""
    if not _initialized:
        return {}
    cache = _translation_cache.get(cache_type)
    return cache if isinstance(cache, dict) else {}


def get_session_new_count(cache_type: str) -> int:
    with _cache_lock:
        return len(cache_store.get_session_entries(_session_new_entries, cache_type))


def get_active_shard_id(cache_type: str) -> str:
    return _get_active_shard_id_impl(_cache_file_path, cache_type, ACTIVE_SHARD_FILE)


def get_cache_overview() -> Dict[str, Any]:
    initialize_translation_cache()
    with _cache_lock:
        return build_cache_overview(
            cache_types=CACHE_TYPES,
            translation_cache=_translation_cache,
            is_dirty=_is_dirty,
            session_new_entries=_session_new_entries,
            cache_file_path=_cache_file_path,
            rolling_shard_size=ROLLING_SHARD_SIZE,
            active_shard_file=ACTIVE_SHARD_FILE,
            get_active_shard_path=_get_active_shard_path,
            load_config=load_config,
            cache_dir_name=_CACHE_DIR_NAME,
            resolve_project_path=resolve_project_path,
        )


def force_rotate_shard(cache_type: str) -> bool:
    initialize_translation_cache()
    if cache_type not in CACHE_TYPES:
        return False
    try:
        with _cache_lock:
            type_dir = _cache_file_path[cache_type].parent
            active_file = type_dir / ACTIVE_SHARD_FILE
            if not active_file.exists():
                _ = _get_active_shard_path(cache_type)
            cur = int((active_file.read_text(encoding="utf-8") or "1").strip())
            active_file.write_text(f"{cur + 1:05d}", encoding="utf-8")
        return True
    except Exception:
        return False


def _get_search_facade() -> CacheSearchFacade:
    global _search_facade
    if _search_facade is None:
        with _search_facade_lock:
            if _search_facade is None:
                _search_facade = CacheSearchFacade(_get_cache_root, log)
    return _search_facade


def get_search_engine():
    return _get_search_facade().get_search_engine()


def rebuild_search_index():
    return _get_search_facade().rebuild_search_index(CACHE_TYPES, _translation_cache)


def rebuild_search_index_for_type(cache_type: str):
    return _get_search_facade().rebuild_search_index_for_type(cache_type, CACHE_TYPES, _translation_cache)


def search_cache(
    query: str,
    cache_type: str = None,
    limit: int = 50,
    use_fuzzy: bool = True,
) -> list:
    return _get_search_facade().search_cache(
        query=query,
        cache_type=cache_type,
        limit=limit,
        use_fuzzy=use_fuzzy,
    )


def find_similar_translations(
    text: str,
    cache_type: str = None,
    threshold: float = 0.6,
    limit: int = 20,
) -> list:
    return _get_search_facade().find_similar_translations(
        text=text,
        cache_type=cache_type,
        threshold=threshold,
        limit=limit,
    )


initialize_translation_cache()
log.info(
    "快取統計：" + ", ".join(f"{k}={len(v)}" for k, v in _translation_cache.items())
)
