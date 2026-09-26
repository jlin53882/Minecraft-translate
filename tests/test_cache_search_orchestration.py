import threading
import time

from translation_tool.utils import cache_manager
from translation_tool.utils import cache_search, cache_store


def _reset_cache_state():
    state = cache_store.reset_runtime_state(cache_manager.CACHE_TYPES)
    state.translation_cache = {k: {} for k in cache_manager.CACHE_TYPES}
    state.initialized = True
    cache_manager._search_facade = None


def test_rebuild_search_index_contract_and_tmp_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager, "resolve_project_path", lambda p: tmp_path / p)
    _reset_cache_state()

    cache_manager.add_to_cache("lang", "item.minecraft.diamond", "Diamond", "鑽石")
    cache_manager.add_to_cache("md", "kubejs/docs|Hello world", "Hello world", "哈囉世界")

    cache_manager.rebuild_search_index()
    cache_manager.rebuild_search_index()

    results = cache_manager.search_cache("Diamond", cache_type="lang", use_fuzzy=False)
    assert results
    row = results[0]
    for k in ("key", "src", "dst", "mod", "path", "type"):
        assert k in row

    db_files = list(cache_manager._get_cache_root().glob("search_index.db"))
    tmp_files = list(cache_manager._get_cache_root().glob("*.tmp*"))
    assert len(db_files) == 1
    assert tmp_files == []


def test_rebuild_search_index_for_type_no_pollution(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager, "resolve_project_path", lambda p: tmp_path / p)
    _reset_cache_state()

    cache_manager.add_to_cache("lang", "item.minecraft.apple", "Apple", "蘋果")
    cache_manager.add_to_cache("patchouli", "assets/mod/book/en_us|Entry", "Entry", "條目")
    cache_manager.rebuild_search_index()

    cache_manager.add_to_cache("lang", "item.minecraft.apple", "Apple", "紅蘋果")
    cache_manager.rebuild_search_index_for_type("lang")

    lang_results = cache_manager.search_cache("紅蘋果", cache_type="lang", use_fuzzy=False)
    patchouli_results = cache_manager.search_cache("條目", cache_type="patchouli", use_fuzzy=False)

    assert lang_results and lang_results[0]["type"] == "lang"
    assert patchouli_results and patchouli_results[0]["type"] == "patchouli"


def test_rebuild_uses_build_then_swap_query_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager, "resolve_project_path", lambda p: tmp_path / p)
    _reset_cache_state()

    cache_manager.add_to_cache("lang", "item.minecraft.iron", "Iron", "鐵")
    cache_manager.rebuild_search_index()

    original = cache_search.rebuild_from_cache_dicts

    def slow_rebuild(engine, cache_types, cache_state):
        time.sleep(0.2)
        return original(engine, cache_types, cache_state)

    monkeypatch.setattr(cache_search, "rebuild_from_cache_dicts", slow_rebuild)

    exc = []

    def worker():
        try:
            cache_manager.rebuild_search_index()
        except Exception as e:  # pragma: no cover
            exc.append(e)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)

    # rebuild 期間仍應可查詢，至少不 crash
    mid_results = cache_manager.search_cache("鐵", cache_type="lang", use_fuzzy=False)
    t.join()

    assert not exc
    assert isinstance(mid_results, list)


def test_index_meta_tracks_shard_changes(tmp_path, monkeypatch):
    """啟動時只有快取分片變動才需要重建（B2）。"""
    monkeypatch.setattr(cache_manager, "_get_cache_root", lambda: tmp_path / "cache")
    _reset_cache_state()

    assert cache_manager.is_search_index_current() is False  # 尚無索引

    shard = cache_manager._get_cache_root() / "lang" / "lang_00001.json"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text('{"item.minecraft.gold": {"src": "Gold", "dst": "金"}}', encoding="utf-8")
    cache_manager.add_to_cache("lang", "item.minecraft.gold", "Gold", "金")
    cache_manager.rebuild_search_index()
    assert cache_manager.is_search_index_current() is True

    # 分片內容變動（例如翻譯後寫入新條目）→ 需要重建
    shard.write_text(
        '{"item.minecraft.gold": {"src": "Gold", "dst": "金"},'
        ' "item.minecraft.coal": {"src": "Coal", "dst": "煤炭"}}',
        encoding="utf-8",
    )
    assert cache_manager.is_search_index_current() is False

    cache_manager.rebuild_search_index()
    assert cache_manager.is_search_index_current() is True
    cache_manager.rebuild_search_index_for_type("lang")
    assert cache_manager.is_search_index_current() is False


def test_rebuild_keeps_old_index_searchable_until_swap(tmp_path, monkeypatch):
    """重建期間舊索引仍可查到資料（原本會先刪除 DB，重建中搜尋結果為空）。"""
    monkeypatch.setattr(cache_manager, "_get_cache_root", lambda: tmp_path / "cache")
    _reset_cache_state()
    cache_manager.add_to_cache("lang", "item.minecraft.iron", "Iron", "鐵")
    cache_manager.rebuild_search_index()

    started = threading.Event()
    release = threading.Event()
    original = cache_search.rebuild_from_cache_dicts

    def slow_rebuild(engine, cache_types, cache_state):
        started.set()
        release.wait(5)
        return original(engine, cache_types, cache_state)

    monkeypatch.setattr(cache_search, "rebuild_from_cache_dicts", slow_rebuild)
    t = threading.Thread(target=cache_manager.rebuild_search_index)
    t.start()
    assert started.wait(5)
    try:
        mid = cache_manager.search_cache("鐵", cache_type="lang", use_fuzzy=False)
    finally:
        release.set()
        t.join()
    assert mid and mid[0]["dst"] == "鐵"
