"""cache_manager.py（快取管理 façade）

PR39B：runtime state 已下沉到 `cache_store.py`，本模組僅保留正式 API 與流程協調。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cache_shards, cache_store
from .cache_loader import load_cache_type
from .cache_overview import (
    build_cache_overview,
)
from .cache_overview import (
    get_active_shard_id as _get_active_shard_id_impl,
)
from .cache_search_facade import CacheSearchFacade
from .config_manager import load_config, resolve_project_path

log = logging.getLogger(__name__)

CACHE_TYPES = ["lang", "patchouli", "ftbquests", "kubejs", "md"]
ROLLING_SHARD_SIZE = 2500
ACTIVE_SHARD_FILE = ".active"
# 初始化失敗後的重試冷卻秒數；避免持續故障時每次讀寫都重跑完整載入並洗版 log。
_INIT_RETRY_COOLDOWN_SECONDS = 30.0
_monotonic = time.monotonic
_CACHE_DIR_NAME = "快取資料"

_search_facade: CacheSearchFacade | None = None
_search_facade_lock = threading.Lock()

__all__ = [
    "ACTIVE_SHARD_FILE",
    "CACHE_TYPES",
    "ROLLING_SHARD_SIZE",
    "CacheAddReceipt",
    "CacheSaveReceipt",
    "add_to_cache",
    "add_to_cache_batch",
    "add_to_cache_with_receipt",
    "find_similar_translations",
    "force_rotate_shard",
    "get_active_shard_id",
    "get_cache_dict_ref",
    "get_cache_entry",
    "get_cache_overview",
    "get_from_cache",
    "get_search_engine",
    "get_session_new_count",
    "initialize_translation_cache",
    "is_cache_initialized",
    "rebuild_search_index",
    "rebuild_search_index_for_type",
    "reload_translation_cache",
    "reload_translation_cache_type",
    "save_translation_cache",
    "save_translation_cache_keys",
    "search_cache",
]


@dataclass(frozen=True)
class CacheAddReceipt:
    """Whether an add was accepted and whether it changed the cached dst."""

    accepted: bool
    changed: bool


@dataclass(frozen=True)
class CacheSaveReceipt:
    """Receipt for the selected pending keys written by one cache flush."""

    saving_enabled: bool
    saved_keys: tuple[str, ...] | None


def _state():
    """取得或建立快取執行期狀態實例"""
    return cache_store.ensure_runtime_maps(CACHE_TYPES)


def _initialized_state():
    """惰性初始化快取並回傳執行期狀態。

    模組 import 必須保持唯讀，避免 pytest collection 或單純匯入時建立
    使用者資料夾；需要磁碟快取的正式 API 在第一次呼叫時才初始化。
    """
    initialize_translation_cache()
    return _state()


def _write_rejected(state, operation: str) -> bool:
    """初始化失敗時拒絕寫入，避免接受無 durable save path 的記憶體資料。

    必須在持有 `state.cache_lock` 時呼叫。每個失敗視窗只記一次 error，
    避免大量寫入洗版。
    """
    if state.initialized:
        return False
    if not state.write_reject_logged:
        state.write_reject_logged = True
        log.error(f"快取尚未成功初始化，拒絕 {operation}（寫入不會被持久化）")
    return True


def _clear_partial_state(state) -> None:
    """清掉半套載入的 runtime cache，避免 read API 暴露部分資料。"""
    state.translation_cache = {}
    state.cache_file_path = {}
    state.active_cache_root = None
    state.initialized = False


def _get_cache_root() -> Path:
    """從設定取得快取根目錄路徑"""
    state = _state()
    if state.active_cache_root is not None:
        return state.active_cache_root
    return _configured_cache_root()


def _configured_cache_root() -> Path:
    """讀取目前持久化設定的快取根目錄（尚未啟用前使用）。"""
    translation_config = load_config().get("translator", {})
    cache_dir_name = translation_config.get("cache_directory", _CACHE_DIR_NAME)
    return resolve_project_path(cache_dir_name)


def _load_cache_type(cache_type: str):
    """載入指定類型的快取。"""
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
    """初始化翻譯快取系統。"""
    state = _state()
    with state.cache_lock:
        if state.initialized:
            return
        failed_at = state.init_failed_at
        if (
            failed_at is not None
            and _monotonic() - failed_at < _INIT_RETRY_COOLDOWN_SECONDS
        ):
            return
        try:
            state.active_cache_root = _configured_cache_root()
            for cache_type in CACHE_TYPES:
                _load_cache_type(cache_type)
            state.initialized = True
            state.init_failed_at = None
            state.write_reject_logged = False
        except Exception as e:
            # 部分 cache type 可能已載入；失敗時不可讓 read API 看見半套狀態。
            _clear_partial_state(state)
            state.init_failed_at = _monotonic()
            state.write_reject_logged = False
            log.error(f"快取系統初始化失敗: {e!r}", exc_info=True)  # noqa: G201


def is_cache_initialized() -> bool:
    """檢查快取是否已初始化。"""
    return bool(_state().initialized)


def reload_translation_cache():
    """重新載入翻譯快取。"""
    state = cache_store.get_runtime_state()
    with state.cache_lock:
        configured_root = _configured_cache_root()
        cache_store.reset_runtime_state(CACHE_TYPES)
        # re-fetch state after reset (reset_runtime_state modifies the global)
        state = cache_store.get_runtime_state()
        state.active_cache_root = configured_root
        # 重新載入所有快取型別
        translation_config = load_config().get("translator", {})
        try:
            for cache_type in CACHE_TYPES:
                load_cache_type(
                    cache_type,
                    translation_cache=state.translation_cache,
                    cache_file_path=state.cache_file_path,
                    cache_root=_get_cache_root(),
                    parallel_workers=translation_config.get(
                        "parallel_execution_workers", 4
                    ),
                    logger=log,
                )
        except Exception:
            # 與 initialize_translation_cache 一致：失敗時不留半套 state，再把例外交給呼叫端。
            _clear_partial_state(state)
            raise
        state.initialized = True


def reload_translation_cache_type(cache_type: str):
    """重新載入指定類型的翻譯快取。"""
    if cache_type not in CACHE_TYPES:
        return
    state = _state()
    # 明確的重載請求：略過初始化失敗冷卻，直接重試。
    state.init_failed_at = None
    initialize_translation_cache()
    with state.cache_lock:
        if not state.initialized:
            log.error(f"快取尚未成功初始化，無法重新載入 {cache_type}")
            return
        state.translation_cache[cache_type] = {}
        cache_store.get_session_entries(state.session_new_entries, cache_type).clear()
        cache_store.clear_dirty(state.is_dirty, cache_type)
    _load_cache_type(cache_type)


def _save_entries_to_active_shards(
    cache_type: str, entries: dict, force_new_shard: bool = False
):
    """儲存項目到作用中分片，容量滿時自動輪轉"""
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
    """儲存翻譯快取。"""
    if not load_config().get("translator", {}).get("enable_cache_saving", True):
        return True

    state = _state()
    with state.cache_lock:
        session_entries = cache_store.get_session_entries(
            state.session_new_entries, cache_type
        )
        if not session_entries:
            return True
        data_to_save = cache_store.flush_session_entries(
            state.session_new_entries, cache_type
        )
    try:
        save_path = state.cache_file_path.get(cache_type)
        if not save_path:
            # 缺少 save path 代表快取未成功初始化；不可清掉尚未 durable 的 pending。
            with state.cache_lock:
                cache_store.restore_session_entries_if_absent(
                    state.session_new_entries, state.is_dirty, cache_type, data_to_save
                )
            log.error(
                f"❌ 儲存 {cache_type} 失敗：快取尚未成功初始化，保留 pending 項目"
            )
            return False
        _save_entries_to_active_shards(
            cache_type, data_to_save, force_new_shard=write_new_shard
        )
        with state.cache_lock:
            # save 期間若又有新 pending，仍保持 dirty
            if not cache_store.get_session_entries(
                state.session_new_entries, cache_type
            ):
                cache_store.clear_dirty(state.is_dirty, cache_type)
        return True
    except Exception as e:
        # 落盤失敗（底層已 rollback）：尚未 durable 的批次放回 pending，下次 save 可重試。
        # 只補回不存在的 key，save 期間寫入的較新值不會被舊批次覆蓋。
        with state.cache_lock:
            cache_store.restore_session_entries_if_absent(
                state.session_new_entries, state.is_dirty, cache_type, data_to_save
            )
        log.error(f"❌ 儲存 {cache_type} 失敗: {e!r}", exc_info=True)  # noqa: G201
        return False


def save_translation_cache_keys(
    cache_type: str, keys: set[str] | frozenset[str], write_new_shard: bool = True
) -> CacheSaveReceipt:
    """Persist only the requested pending keys and return an exact flush receipt.

    Other pending keys for the same profile belong to other runs and stay queued.
    A disabled cache writer is reported separately from a successful empty flush.
    """
    if not load_config().get("translator", {}).get("enable_cache_saving", True):
        return CacheSaveReceipt(saving_enabled=False, saved_keys=())

    state = _state()
    with state.cache_lock:
        pending = cache_store.get_session_entries(state.session_new_entries, cache_type)
        data_to_save = {key: pending[key] for key in keys if key in pending}
        for key in data_to_save:
            del pending[key]
    if not data_to_save:
        return CacheSaveReceipt(saving_enabled=True, saved_keys=())

    try:
        save_path = state.cache_file_path.get(cache_type)
        if not save_path:
            with state.cache_lock:
                cache_store.restore_session_entries_if_absent(
                    state.session_new_entries, state.is_dirty, cache_type, data_to_save
                )
            return CacheSaveReceipt(saving_enabled=True, saved_keys=None)
        _save_entries_to_active_shards(
            cache_type, data_to_save, force_new_shard=write_new_shard
        )
        with state.cache_lock:
            if not cache_store.get_session_entries(
                state.session_new_entries, cache_type
            ):
                cache_store.clear_dirty(state.is_dirty, cache_type)
        return CacheSaveReceipt(
            saving_enabled=True, saved_keys=tuple(data_to_save.keys())
        )
    except Exception:
        with state.cache_lock:
            cache_store.restore_session_entries_if_absent(
                state.session_new_entries, state.is_dirty, cache_type, data_to_save
            )
        log.exception("儲存 %s 指定快取鍵失敗", cache_type)
        return CacheSaveReceipt(saving_enabled=True, saved_keys=None)


def _get_active_shard_path(cache_type: str) -> Path:
    """取得目前作用中的分片檔案路徑"""
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
) -> bool:
    """新增翻譯到快取。

    Returns:
        True 代表條目已在快取中（新增或內容未變）；False 代表未寫入
        （key/dst 為空，或快取初始化失敗而拒絕寫入）。
    """
    return add_to_cache_with_receipt(
        cache_type, key, src, dst, mod=mod, path=path
    ).accepted


def add_to_cache_with_receipt(
    cache_type: str,
    key: str,
    src: str,
    dst: str,
    *,
    mod: str | None = None,
    path: str | None = None,
) -> CacheAddReceipt:
    """Add a translation and distinguish acceptance from a dst mutation."""
    if not key or not dst:
        return CacheAddReceipt(accepted=False, changed=False)

    state = _initialized_state()
    with state.cache_lock:
        if _write_rejected(state, "add_to_cache_with_receipt"):
            return CacheAddReceipt(accepted=False, changed=False)
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
        return CacheAddReceipt(accepted=True, changed=changed)


def add_to_cache_batch(
    cache_type: str,
    entries: list[tuple[str, str, str]],
    *,
    mods: list[str | None] | None = None,
    paths: list[str | None] | None = None,
) -> bool:
    """批次新增翻譯到快取（單次鎖獲取，減少鎖競爭）。

    Args:
        cache_type: 快取類型 (lang, patchouli, ftbquests, kubejs, md)
        entries: List of (key, src, dst) tuples
        mods: Optional list of mod names (same length as entries)
        paths: Optional list of paths (same length as entries)

    Returns:
        False 代表整批被拒絕（快取初始化失敗）；否則 True（空 entries 視為 True）。
    """
    if not entries:
        return True

    state = _initialized_state()
    with state.cache_lock:
        if _write_rejected(state, "add_to_cache_batch"):
            return False
        cache = cache_store.get_cache_type_dict(state.translation_cache, cache_type)
        session_entries = cache_store.get_session_entries(
            state.session_new_entries, cache_type
        )
        dirty = False

        for i, (key, src, dst) in enumerate(entries):
            if not key or not dst:
                continue
            entry = {"src": src, "dst": dst}
            if mods and i < len(mods) and mods[i]:
                entry["mod"] = mods[i]
            if paths and i < len(paths) and paths[i]:
                entry["path"] = paths[i]
            changed = cache_store.add_entry(cache, key, entry)
            if changed:
                session_entries[key] = entry
                dirty = True

        if dirty:
            cache_store.mark_dirty(state.is_dirty, cache_type)
        return True


def get_from_cache(cache_type: str, key: str) -> str | None:
    """從快取取得指定 key 的翻譯文字 (dst)。"""
    state = _initialized_state()
    cache = state.translation_cache.get(cache_type)
    if not isinstance(cache, dict):
        return None
    return cache_store.get_value(cache, key)


def get_cache_entry(cache_type: str, key: str) -> dict[str, Any] | None:
    """取得指定 cache_type 的單筆完整快取條目。"""
    state = _initialized_state()
    cache = state.translation_cache.get(cache_type)
    if not isinstance(cache, dict):
        return None
    return cache_store.get_entry(cache, key)


def get_cache_dict_ref(cache_type: str) -> dict[str, dict[str, Any]]:
    """取得指定類型的快取字典參照。"""
    state = _initialized_state()
    cache = state.translation_cache.get(cache_type)
    return cache if isinstance(cache, dict) else {}


def get_session_new_count(cache_type: str) -> int:
    """取得本次 session 新增的項目數"""
    state = _state()
    with state.cache_lock:
        return len(
            cache_store.get_session_entries(state.session_new_entries, cache_type)
        )


def get_active_shard_id(cache_type: str) -> str:
    """取得指定快取類型的目前作用中分片 ID"""
    state = _initialized_state()
    return _get_active_shard_id_impl(
        state.cache_file_path, cache_type, ACTIVE_SHARD_FILE
    )


def get_cache_overview() -> dict[str, Any]:
    """取得所有快取類型的概覽（包含項目數與狀態）"""
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
    """強制輪轉至下一個分片。"""
    initialize_translation_cache()
    state = _state()
    if cache_type not in CACHE_TYPES:
        return False
    try:
        with state.cache_lock:
            type_dir = state.cache_file_path[cache_type].parent
            # 與寫入交易共用跨程序 `.active.lock`
            cache_shards.force_rotate_active_shard(
                type_dir=type_dir,
                cache_type=cache_type,
                active_shard_file=ACTIVE_SHARD_FILE,
                logger=log,
            )
        return True
    except Exception:
        log.warning("強制輪替作用中分片失敗：%s", cache_type, exc_info=True)
        return False


def _get_search_facade() -> CacheSearchFacade:
    """取得或建立快取搜尋外觀（惰性初始化）"""
    global _search_facade
    if _search_facade is None:
        with _search_facade_lock:
            if _search_facade is None:
                _search_facade = CacheSearchFacade(lambda: _get_cache_root(), log)
    return _search_facade


def get_search_engine():
    """取得快取查詢用的搜尋引擎實例"""
    return _get_search_facade().get_search_engine()


def is_search_index_current() -> bool:
    """搜尋索引是否與磁碟上的快取分片一致（不需載入快取內容）。"""
    return _get_search_facade().is_search_index_current(CACHE_TYPES)


def rebuild_search_index() -> bool:
    """重建所有快取類型的搜尋索引。

    Returns:
        False 代表快取初始化失敗而拒絕重建（不可用空 cache 覆蓋既有索引）；
        True 代表已交給搜尋外觀重建。
    """
    state = _initialized_state()
    if not state.initialized:
        log.error("快取尚未成功初始化，拒絕重建搜尋索引")
        return False
    result = _get_search_facade().rebuild_search_index(
        CACHE_TYPES, state.translation_cache
    )
    # 舊版測試替身／外部 facade 可能沒有回傳值；只有明確 False 才算失敗。
    return result is not False


def rebuild_search_index_for_type(cache_type: str) -> bool:
    """重建指定快取類型的搜尋索引。

    Returns:
        False 代表快取初始化失敗而拒絕重建；True 代表已交給搜尋外觀重建。
    """
    state = _initialized_state()
    if not state.initialized:
        log.error(f"快取尚未成功初始化，拒絕重建 {cache_type} 搜尋索引")
        return False
    result = _get_search_facade().rebuild_search_index_for_type(
        cache_type, CACHE_TYPES, state.translation_cache
    )
    return result is not False


def search_cache(
    query: str, cache_type: str | None = None, limit: int = 50, use_fuzzy: bool = True
) -> list:
    """搜尋快取中符合查詢字的翻譯"""
    return _get_search_facade().search_cache(
        query=query, cache_type=cache_type, limit=limit, use_fuzzy=use_fuzzy
    )


def find_similar_translations(
    text: str, cache_type: str | None = None, threshold: float = 0.6, limit: int = 20
) -> list:
    """使用模糊比對找出相似的翻譯"""
    return _get_search_facade().find_similar_translations(
        text=text, cache_type=cache_type, threshold=threshold, limit=limit
    )
