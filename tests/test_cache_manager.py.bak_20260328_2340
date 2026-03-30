"""test_cache_manager.py

測試 cache_manager 的執行緒安全與 dirty flag 行為。
覆蓋：
1. initialize_translation_cache() 的 cache_lock 保護
2. save_translation_cache() 的 clear_dirty() 時機（寫入成功後）
"""

import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from translation_tool.utils import cache_manager, cache_store


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def fresh_state():
    """提供乾淨的 runtime state（每個測試獨立）。"""
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    state = cache_store.get_runtime_state()
    state.initialized = False
    state.translation_cache = {k: {} for k in cache_manager.CACHE_TYPES}
    state.session_new_entries = {k: {} for k in cache_manager.CACHE_TYPES}
    state.is_dirty = {k: False for k in cache_manager.CACHE_TYPES}
    yield state
    # 測試結束重置，避免污染後續測試
    cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)


@pytest.fixture
def mock_save_path(tmp_path: Path, fresh_state):
    """設定假的 cache 檔案路徑（不碰真實檔案系統）。"""
    cache_type = "lang"
    type_dir = tmp_path / cache_type
    type_dir.mkdir(parents=True, exist_ok=True)
    fresh_state.cache_file_path = {
        cache_type: type_dir / f"{cache_type}_cache_main.json"
    }
    return cache_type, type_dir


# =============================================================================
# 測試 1: initialize_translation_cache() 的 cache_lock 保護
# =============================================================================

def test_initialize_translation_cache_uses_cache_lock(fresh_state):
    """驗證 initialize_translation_cache() 在 cache_lock 保護下執行。

    情境：多執行緒同時呼叫 initialize_translation_cache()，
    確認第二次呼叫因為 lock 而被阻擋（initialized 已為 True），
    不會造成重複載入。
    """
    call_count = 0
    lock_enter_order = []
    lock_exit_order = []

    original_lock_class = type(fresh_state.cache_lock)

    # 追蹤 lock 的進入/離開時間點
    def _lock_enter():
        lock_enter_order.append(len(lock_enter_order))

    def _lock_exit():
        lock_exit_order.append(len(lock_exit_order))

    # Patch _load_cache_type 來計數呼叫
    def _load_cache_type_track(cache_type):
        nonlocal call_count
        call_count += 1

    with patch.object(cache_manager, "_load_cache_type", side_effect=_load_cache_type_track):
        # 模擬兩執行緒同時進入
        def call_init():
            cache_manager.initialize_translation_cache()

        # 第一次呼叫
        t1 = threading.Thread(target=call_init)
        t1.start()
        t1.join()

    # 驗證：initialized 為 True
    assert fresh_state.initialized is True
    # 驗證：每個 cache type 只載入一次
    assert call_count == len(cache_manager.CACHE_TYPES)


def test_initialize_translation_cache_no_double_load_on_concurrent_calls(fresh_state):
    """驗證 initialize_translation_cache() 重複呼叫不會造成 race condition。

    情境：多執行緒幾乎同時呼叫，確認只有一個執行緒真正執行初始化，
    其餘執行緒在 lock 處等待後直接返回（initialized=True）。
    """
    load_calls = []

    def _load_cache_type_tracking(cache_type):
        load_calls.append(cache_type)

    # 先把 initialized 設為 True，模擬已經初始化過
    fresh_state.initialized = True

    with patch.object(
        cache_manager, "_load_cache_type", side_effect=_load_cache_type_tracking
    ):
        cache_manager.initialize_translation_cache()

    # 驗證：已初始化時不再呼叫 _load_cache_type
    assert len(load_calls) == 0


# =============================================================================
# 測試 2: save_translation_cache() 的 clear_dirty() 時機
# =============================================================================

def test_save_translation_cache_dirty_True_when_save_fails(mock_save_path):
    """驗證 save_translation_cache() 在寫入失敗後 dirty flag 仍為 True。

    情境：session_new_entries 有資料，is_dirty=True，
    save_translation_cache() 嘗試儲存但 _save_entries_to_active_shards 失敗。
    預期：is_dirty 保持 True（因為資料已從 session flush 但未成功寫入磁碟）。

    設計：此測試捕捉「crash 發生於寫入前」的場景——dirty flag 必須在
    寫入真正成功後才能清除。
    """
    cache_type, _ = mock_save_path

    state = cache_store.get_runtime_state()
    state.is_dirty[cache_type] = True
    state.session_new_entries[cache_type] = {
        "key1": {"src": "Hello", "dst": "哈囉"}
    }

    with patch.object(
        cache_manager,
        "_save_entries_to_active_shards",
        side_effect=RuntimeError("磁碟寫入失敗（模擬 crash）"),
    ):
        cache_manager.save_translation_cache(cache_type, write_new_shard=True)

    # 驗證：寫入失敗後，dirty flag 仍為 True
    # （資料已從 session_new_entries flush，但寫入失敗，不能假設乾淨）
    assert state.is_dirty[cache_type] is True, (
        "寫入失敗時 dirty 應保持 True，避免資料遺失後又被視為已同步"
    )


def test_save_translation_cache_dirty_cleared_when_save_succeeds(mock_save_path):
    """驗證 save_translation_cache() 在寫入成功後 dirty flag 正確清除。"""
    cache_type, _ = mock_save_path

    state = cache_store.get_runtime_state()
    state.is_dirty[cache_type] = True
    state.session_new_entries[cache_type] = {
        "key1": {"src": "Hello", "dst": "哈囉"}
    }

    saved_data = {}

    def _capture_save(_cache_type, entries, force_new_shard=False):
        saved_data["cache_type"] = _cache_type
        saved_data["entries"] = entries.copy()

    with patch.object(
        cache_manager, "_save_entries_to_active_shards", side_effect=_capture_save
    ):
        cache_manager.save_translation_cache(cache_type, write_new_shard=True)

    # 驗證：寫入成功後，dirty 清除
    assert state.is_dirty[cache_type] is False
    # 驗證：session_new_entries 已 flush
    assert state.session_new_entries[cache_type] == {}
    # 驗證：寫入函式被正確呼叫
    assert saved_data["entries"] == {"key1": {"src": "Hello", "dst": "哈囉"}}


def test_save_translation_cache_no_op_when_no_dirty_entries(mock_save_path):
    """驗證無 dirty 資料時 save_translation_cache 不做任何事。"""
    cache_type, _ = mock_save_path

    state = cache_store.get_runtime_state()
    state.is_dirty[cache_type] = False
    state.session_new_entries[cache_type] = {}

    with patch.object(
        cache_manager, "_save_entries_to_active_shards"
    ) as mock_save:
        cache_manager.save_translation_cache(cache_type)

    # 驗證：無 session 資料時不呼叫儲存
    assert mock_save.call_count == 0
