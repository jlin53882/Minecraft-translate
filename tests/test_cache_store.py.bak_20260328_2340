from pathlib import Path

from translation_tool.utils import cache_manager, cache_store


def test_cache_store_entry_and_value_crud():
    state = {"lang": {}}
    cache_dict = cache_store.get_cache_type_dict(state, "lang")

    changed = cache_store.add_entry(cache_dict, "k1", {"src": "s1", "dst": "d1"})
    assert changed is True
    assert cache_store.get_entry(cache_dict, "k1") == {"src": "s1", "dst": "d1"}
    assert cache_store.get_value(cache_dict, "k1") == "d1"

    changed_again = cache_store.add_entry(cache_dict, "k1", {"src": "s1-new", "dst": "d1"})
    assert changed_again is False
    # contract: dst 相同時不覆寫舊 entry
    assert cache_store.get_entry(cache_dict, "k1") == {"src": "s1", "dst": "d1"}


def test_cache_store_dirty_and_session_helpers():
    is_dirty = {"lang": False}
    session_new_entries = {"lang": {"k0": {"dst": "v0"}}}

    cache_store.mark_dirty(is_dirty, "lang")
    assert is_dirty["lang"] is True

    flushed = cache_store.flush_session_entries(session_new_entries, "lang")
    assert flushed == {"k0": {"dst": "v0"}}
    assert session_new_entries["lang"] == {}

    cache_store.clear_dirty(is_dirty, "lang")
    assert is_dirty["lang"] is False


def test_manager_add_save_reload_smoke(monkeypatch, tmp_path: Path):
    cache_type = "lang"
    key = "hello"

    state = cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    state.initialized = True
    state.translation_cache = {k: {} for k in cache_manager.CACHE_TYPES}
    state.session_new_entries = {k: {} for k in cache_manager.CACHE_TYPES}
    state.is_dirty = {k: False for k in cache_manager.CACHE_TYPES}

    type_dir = tmp_path / cache_type
    type_dir.mkdir(parents=True, exist_ok=True)
    state.cache_file_path = {cache_type: type_dir / f"{cache_type}_cache_main.json"}

    monkeypatch.setattr(
        cache_manager,
        "load_config",
        lambda: {"translator": {"enable_cache_saving": True}},
    )

    saved = {}

    def _fake_save_entries(_cache_type: str, entries: dict, force_new_shard: bool = False):
        saved["cache_type"] = _cache_type
        saved["entries"] = entries.copy()
        saved["force_new_shard"] = force_new_shard

    monkeypatch.setattr(cache_manager, "_save_entries_to_active_shards", _fake_save_entries)

    cache_manager.add_to_cache(cache_type, key, "Hello", "哈囉")
    assert cache_manager.get_from_cache(cache_type, key) == "哈囉"
    assert cache_manager.get_session_new_count(cache_type) == 1

    cache_manager.save_translation_cache(cache_type, write_new_shard=True)
    assert saved["cache_type"] == cache_type
    assert saved["entries"] == {key: {"src": "Hello", "dst": "哈囉"}}
    assert saved["force_new_shard"] is True
    assert cache_manager.get_session_new_count(cache_type) == 0
    assert state.is_dirty[cache_type] is False

    def _fake_load_cache_type(_cache_type: str):
        state.translation_cache[_cache_type] = {key: {"src": "Hello", "dst": "哈囉"}}

    monkeypatch.setattr(cache_manager, "_load_cache_type", _fake_load_cache_type)
    cache_manager.reload_translation_cache_type(cache_type)

    assert cache_manager.get_cache_entry(cache_type, key) == {"src": "Hello", "dst": "哈囉"}


# =============================================================================
# 測試 3: add_entry() 執行緒安全保護
# =============================================================================

import threading


def test_add_entry_thread_safety_different_keys():
    """驗證 add_entry() 多執行緒同時寫入不同 key 時不造成資料遺失。

    情境：兩個執行緒同時對同一個 cache_dict 寫入不同的 key，
    不使用 cache_manager.add_to_cache() 的 lock（直接呼叫 add_entry）。
    預期：所有 key 都正確寫入，無競爭條件導致 entry 遺失。

    此測試驗證 add_entry() 在多執行序並發呼叫時，
    對不同 key 的寫入能正確完成而不互相干擾。
    """
    # 使用獨立的 cache_dict，不走 manager 的 lock
    cache_dict = {}

    results = []

    def writer(thread_id, keys):
        for k in keys:
            entry = {"src": f"src_{thread_id}_{k}", "dst": f"dst_{thread_id}_{k}"}
            changed = cache_store.add_entry(cache_dict, k, entry)
            results.append((thread_id, k, changed))

    # 建立兩組不同的 key，避免 key collision 測試混淆
    keys_a = [f"key_a_{i}" for i in range(50)]
    keys_b = [f"key_b_{i}" for i in range(50)]

    t1 = threading.Thread(target=writer, args=(1, keys_a))
    t2 = threading.Thread(target=writer, args=(2, keys_b))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # 驗證：所有 key 都成功寫入（無遺漏）
    assert len(cache_dict) == 100, (
        f"預期 100 個 entry，實際只有 {len(cache_dict)} 個。"
        "多執行序寫入不同 key 不應造成資料遺失。"
    )

    # 驗證：每個 key 都被寫入一次（changed=True）
    changed_count = sum(1 for _, _, changed in results if changed is True)
    assert changed_count == 100, (
        f"預期 100 次 changed=True，實際有 {changed_count} 次。"
    )


def test_add_entry_thread_safety_same_key_race():
    """驗證 add_entry() 多執行序同時寫入相同 key 的競爭行為。

    情境：兩個執行緒同時對同一個 key 寫入不同的值，
    模擬真實並發情境下的資料競爭。

    預期：最終 cache_dict[key] 為其中一個執行緒的寫入結果
    （add_entry 本身不做 internal lock，所以結果是 non-deterministic）。

    此測試記錄竞争結果，用於確認 add_entry() 在並發下
    不會發生 dict 結構損壞或 exception。
    """
    cache_dict = {}
    exceptions = []

    def writer(thread_id, value):
        try:
            entry = {"src": f"src_{thread_id}", "dst": f"dst_{thread_id}_{value}"}
            cache_store.add_entry(cache_dict, "shared_key", entry)
        except Exception as e:
            exceptions.append((thread_id, e))

    t1 = threading.Thread(target=writer, args=(1, "value_a"))
    t2 = threading.Thread(target=writer, args=(2, "value_b"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # 驗證：無 exception（add_entry 不應拋出例外）
    assert len(exceptions) == 0, f"add_entry 在並發下不應拋出例外: {exceptions}"

    # 驗證：cache_dict["shared_key"] 是其中一個執行緒的寫入結果
    # （dst 值是 "dst_1_value_a" 或 "dst_2_value_b"）
    final_dst = cache_dict.get("shared_key", {}).get("dst", "")
    assert final_dst in (
        "dst_1_value_a",
        "dst_2_value_b",
    ), f"最終值應為其中一個執行緒的寫入，實際: {final_dst}"

    # 驗證：entry 結構完整
    assert isinstance(cache_dict["shared_key"], dict)
    assert "src" in cache_dict["shared_key"]
    assert "dst" in cache_dict["shared_key"]

