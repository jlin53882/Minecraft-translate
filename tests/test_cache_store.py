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
