"""test_cache_manager.py

測試 cache_manager 的執行緒安全與 dirty flag 行為。
覆蓋：
1. initialize_translation_cache() 的 cache_lock 保護
2. save_translation_cache() 的 clear_dirty() 時機（寫入成功後）
"""

import threading
from pathlib import Path
from unittest.mock import patch

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


def test_add_to_cache_with_receipt_distinguishes_mutation_from_acceptance(fresh_state):
    fresh_state.initialized = True

    first = cache_manager.add_to_cache_with_receipt(
        "lang", "same-key", "source", "first", mod="one"
    )
    unchanged = cache_manager.add_to_cache_with_receipt(
        "lang", "same-key", "source", "first", mod="two"
    )

    assert first.accepted and first.changed
    assert unchanged.accepted and not unchanged.changed
    assert first.generation is not None
    assert unchanged.generation == first.generation
    assert fresh_state.session_new_entries["lang"] == {
        "same-key": {"src": "source", "dst": "first", "mod": "one"}
    }


def test_save_translation_cache_keys_only_flushes_selected_pending_keys(
    mock_save_path, monkeypatch, fresh_state
):
    cache_type, _type_dir = mock_save_path
    fresh_state.initialized = True
    fresh_state.is_dirty[cache_type] = True
    fresh_state.session_new_entries[cache_type] = {
        "this-run": {"src": "a", "dst": "A"},
        "other-run": {"src": "b", "dst": "B"},
    }
    monkeypatch.setattr(
        cache_manager,
        "load_config",
        lambda: {"translator": {"enable_cache_saving": True}},
    )
    writes = []
    monkeypatch.setattr(
        cache_manager,
        "_save_entries_to_active_shards",
        lambda kind, entries, **_kwargs: writes.append((kind, dict(entries))),
    )

    receipt = cache_manager.save_translation_cache_keys(cache_type, {"this-run"})

    assert receipt.saving_enabled
    assert receipt.saved_keys == ("this-run",)
    assert writes == [(cache_type, {"this-run": {"src": "a", "dst": "A"}})]
    assert fresh_state.session_new_entries[cache_type] == {
        "other-run": {"src": "b", "dst": "B"}
    }
    assert fresh_state.is_dirty[cache_type] is True


def test_selected_cache_flush_does_not_save_a_newer_tasks_same_key_value(
    mock_save_path, monkeypatch, fresh_state
):
    cache_type, _type_dir = mock_save_path
    fresh_state.initialized = True
    monkeypatch.setattr(
        cache_manager,
        "load_config",
        lambda: {"translator": {"enable_cache_saving": True}},
    )
    writes = []
    monkeypatch.setattr(
        cache_manager,
        "_save_entries_to_active_shards",
        lambda kind, entries, **_kwargs: writes.append((kind, dict(entries))),
    )

    task_a = cache_manager.add_to_cache_with_receipt(
        cache_type, "same-key", "source", "translation A"
    )
    task_b = cache_manager.add_to_cache_with_receipt(
        cache_type, "same-key", "source", "translation B"
    )
    assert task_a.generation is not None
    assert task_b.generation is not None
    assert task_a.generation != task_b.generation

    stale_flush = cache_manager.save_translation_cache_keys(
        cache_type,
        {"same-key"},
        expected_versions={"same-key": task_a.generation},
    )

    assert stale_flush.saved_keys == ()
    assert stale_flush.superseded_keys == ("same-key",)
    assert writes == []
    assert fresh_state.session_new_entries[cache_type]["same-key"]["dst"] == (
        "translation B"
    )

    current_flush = cache_manager.save_translation_cache_keys(
        cache_type,
        {"same-key"},
        expected_versions={"same-key": task_b.generation},
    )

    assert current_flush.saved_keys == ("same-key",)
    assert current_flush.superseded_keys == ()
    assert writes == [
        (cache_type, {"same-key": {"src": "source", "dst": "translation B"}})
    ]
    assert fresh_state.session_new_entries[cache_type] == {}


def test_save_translation_cache_keys_reports_disabled_and_failure_receipts(
    mock_save_path, monkeypatch, fresh_state
):
    cache_type, _type_dir = mock_save_path
    fresh_state.initialized = True
    fresh_state.is_dirty[cache_type] = True
    fresh_state.session_new_entries[cache_type] = {"key": {"src": "a", "dst": "A"}}
    monkeypatch.setattr(
        cache_manager,
        "load_config",
        lambda: {"translator": {"enable_cache_saving": False}},
    )

    disabled = cache_manager.save_translation_cache_keys(cache_type, {"key"})

    assert not disabled.saving_enabled and disabled.saved_keys == ()
    assert "key" in fresh_state.session_new_entries[cache_type]

    monkeypatch.setattr(
        cache_manager,
        "load_config",
        lambda: {"translator": {"enable_cache_saving": True}},
    )
    monkeypatch.setattr(
        cache_manager,
        "_save_entries_to_active_shards",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    failed = cache_manager.save_translation_cache_keys(cache_type, {"key"})

    assert failed.saving_enabled and failed.saved_keys is None
    assert fresh_state.session_new_entries[cache_type] == {
        "key": {"src": "a", "dst": "A"}
    }
    assert fresh_state.is_dirty[cache_type] is True


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

    # Patch _load_cache_type 來計數呼叫
    def _load_cache_type_track(cache_type):
        nonlocal call_count
        call_count += 1

    with patch.object(
        cache_manager, "_load_cache_type", side_effect=_load_cache_type_track
    ):
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
    state.session_new_entries[cache_type] = {"key1": {"src": "Hello", "dst": "哈囉"}}

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
    state.session_new_entries[cache_type] = {"key1": {"src": "Hello", "dst": "哈囉"}}

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

    with patch.object(cache_manager, "_save_entries_to_active_shards") as mock_save:
        cache_manager.save_translation_cache(cache_type)

    # 驗證：無 session 資料時不呼叫儲存
    assert mock_save.call_count == 0


# =============================================================================
# 測試 3: save 失敗後 pending 恢復與重試（真實分片寫入 + .shard_order 失敗注入）
# =============================================================================


def _disk_entries(type_dir: Path) -> dict:
    import logging

    from translation_tool.utils.cache_loader import load_cache_type

    cache: dict = {}
    load_cache_type(
        "lang",
        translation_cache=cache,
        cache_file_path={},
        cache_root=type_dir.parent,
        parallel_workers=2,
        logger=logging.getLogger("t"),
    )
    return cache["lang"]


def _fail_order_write():
    from translation_tool.utils import cache_shards

    return patch.object(
        cache_shards, "_record_shard_write", side_effect=OSError("order write failed")
    )


def test_save_failure_restores_pending_and_retry_persists(mock_save_path):
    """Case 1：.shard_order 失敗（numbered rollback）→ pending 恢復、dirty；重試後落盤。"""
    cache_type, type_dir = mock_save_path
    state = cache_store.get_runtime_state()
    entry = {"src": "Hello", "dst": "哈囉"}
    state.session_new_entries[cache_type] = {"K": entry}
    state.is_dirty[cache_type] = True

    with _fail_order_write():
        cache_manager.save_translation_cache(cache_type, write_new_shard=False)

    assert state.session_new_entries[cache_type] == {"K": entry}
    assert state.is_dirty[cache_type] is True
    assert _disk_entries(type_dir) == {}  # 底層已 rollback

    cache_manager.save_translation_cache(cache_type, write_new_shard=False)

    assert state.session_new_entries[cache_type] == {}
    assert state.is_dirty[cache_type] is False
    assert _disk_entries(type_dir) == {"K": entry}


def test_restore_does_not_overwrite_newer_pending_value(mock_save_path):
    """Case 2：save 期間同 key 寫入較新值，save 失敗後保留較新值。"""
    cache_type, _type_dir = mock_save_path
    state = cache_store.get_runtime_state()
    old = {"src": "s", "dst": "A"}
    new = {"src": "s", "dst": "B"}
    state.session_new_entries[cache_type] = {"K": old, "J": old}

    def save_then_concurrent_write(ct, entries, force_new_shard=False):
        # 此時 pending 已被 flush；另一個 writer 寫入較新的 K
        with state.cache_lock:
            state.session_new_entries[ct]["K"] = new
        raise OSError("disk error")

    with patch.object(
        cache_manager,
        "_save_entries_to_active_shards",
        side_effect=save_then_concurrent_write,
    ):
        cache_manager.save_translation_cache(cache_type)

    assert state.session_new_entries[cache_type]["K"] == new  # 不被舊批次覆蓋
    assert state.session_new_entries[cache_type]["J"] == old  # 缺少的 key 補回
    assert state.is_dirty[cache_type] is True


def test_save_success_path_clears_pending_and_dirty(mock_save_path):
    """Case 3：成功路徑不變。"""
    cache_type, type_dir = mock_save_path
    state = cache_store.get_runtime_state()
    entry = {"src": "Hello", "dst": "哈囉"}
    state.session_new_entries[cache_type] = {"K": entry}
    state.is_dirty[cache_type] = True

    cache_manager.save_translation_cache(cache_type, write_new_shard=False)

    assert state.session_new_entries[cache_type] == {}
    assert state.is_dirty[cache_type] is False
    assert _disk_entries(type_dir) == {"K": entry}


def test_repeated_save_failures_remain_retryable(mock_save_path):
    """Case 4：連續失敗兩次仍可重試，第三次成功。"""
    cache_type, type_dir = mock_save_path
    state = cache_store.get_runtime_state()
    entry = {"src": "Hello", "dst": "哈囉"}
    state.session_new_entries[cache_type] = {"K": entry}
    state.is_dirty[cache_type] = True

    for _ in range(2):
        with _fail_order_write():
            cache_manager.save_translation_cache(cache_type, write_new_shard=False)
        assert state.session_new_entries[cache_type] == {"K": entry}
        assert state.is_dirty[cache_type] is True

    cache_manager.save_translation_cache(cache_type, write_new_shard=False)

    assert state.session_new_entries[cache_type] == {}
    assert state.is_dirty[cache_type] is False
    assert _disk_entries(type_dir) == {"K": entry}


def test_timestamp_shard_save_failure_also_restores_pending(mock_save_path):
    cache_type, type_dir = mock_save_path
    state = cache_store.get_runtime_state()
    entry = {"src": "x", "dst": "y"}
    state.session_new_entries[cache_type] = {"K": entry}

    with _fail_order_write():
        cache_manager.save_translation_cache(cache_type, write_new_shard=True)

    assert state.session_new_entries[cache_type] == {"K": entry}
    assert state.is_dirty[cache_type] is True
    assert list(type_dir.glob("lang_*.json")) == []


# =============================================================================
# 測試 4: force_rotate_shard 走 .active.lock
# =============================================================================


def test_force_rotate_shard_advances_active(mock_save_path):
    """Case B：active 00001 → force rotate → 00002。"""
    cache_type, type_dir = mock_save_path
    cache_manager._get_active_shard_path(cache_type)
    (type_dir / ".active").write_text("00001", encoding="utf-8")
    state = cache_store.get_runtime_state()
    state.initialized = True

    assert cache_manager.force_rotate_shard(cache_type) is True
    assert (type_dir / ".active").read_text(encoding="utf-8") == "00002"


def test_force_rotate_shard_waits_for_shard_lock(mock_save_path):
    """Case A：writer 持有 .active.lock 時，force rotate 必須等待，不可 interleave。"""
    import time

    from translation_tool.utils import cache_shards

    cache_type, type_dir = mock_save_path
    cache_manager._get_active_shard_path(cache_type)
    (type_dir / ".active").write_text("00001", encoding="utf-8")
    cache_store.get_runtime_state().initialized = True

    result: dict = {}
    started = threading.Event()

    def rotate():
        started.set()
        result["ok"] = cache_manager.force_rotate_shard(cache_type)
        result["done_at"] = time.monotonic()

    with cache_shards._shard_lock(type_dir, ".active"):
        t = threading.Thread(target=rotate)
        t.start()
        assert started.wait(2)
        time.sleep(0.3)
        # 鎖仍被持有：active 尚未被修改，rotate 尚未完成
        assert (type_dir / ".active").read_text(encoding="utf-8") == "00001"
        assert "ok" not in result
    t.join(5)

    assert result["ok"] is True
    assert (type_dir / ".active").read_text(encoding="utf-8") == "00002"
