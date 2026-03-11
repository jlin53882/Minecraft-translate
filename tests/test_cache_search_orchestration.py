import threading
import time

from translation_tool.utils import cache_manager
from translation_tool.utils import cache_search


def _reset_cache_state():
    cache_manager._translation_cache = {k: {} for k in cache_manager.CACHE_TYPES}
    cache_manager._initialized = True
    cache_manager._search_orchestrator = None


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
