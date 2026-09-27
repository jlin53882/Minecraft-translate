"""PR #104 Finding 5：重建索引替換 engine 時，不可關閉正在查詢中的 engine。

原本 search_cache 先 get_engine() 取出共享 engine 就釋放鎖；rebuild 的 swap
可能在查詢執行前把該 engine close 掉 → sqlite3.ProgrammingError。
"""

import threading

from translation_tool.utils import cache_search
from translation_tool.utils.cache_search import SearchOrchestrator


def _entries(n, word):
    return {f"k{i}": {"src": f"{word} {i}", "dst": f"譯{i}"} for i in range(n)}


def test_swap_waits_for_active_query(tmp_path, monkeypatch):
    orch = SearchOrchestrator(lambda: tmp_path)
    state = {"lang": _entries(5, "Iron")}
    orch.rebuild_search_index(["lang"], state)

    old_engine = orch.get_engine()
    query_started = threading.Event()
    release_query = threading.Event()
    query_done = threading.Event()
    close_called = threading.Event()
    closed_while_query_active = []
    errors = []

    real_search = old_engine.search

    def paused_search(*args, **kwargs):
        query_started.set()
        assert release_query.wait(5)
        return real_search(*args, **kwargs)

    monkeypatch.setattr(old_engine, "search", paused_search)

    real_close = old_engine.close

    def tracking_close():
        closed_while_query_active.append(not query_done.is_set())
        close_called.set()
        real_close()

    monkeypatch.setattr(old_engine, "close", tracking_close)

    results = {}

    def query():
        try:
            results["q"] = orch.search_cache("Iron", cache_type="lang", use_fuzzy=False)
        except Exception as ex:  # noqa: BLE001 - 記錄給主執行緒斷言
            errors.append(ex)
        finally:
            query_done.set()

    rebuild_built = threading.Event()
    real_rebuild = cache_search.rebuild_from_cache_dicts

    def tracked_rebuild(engine, cache_types, cache_state):
        n = real_rebuild(engine, cache_types, cache_state)
        rebuild_built.set()
        return n

    monkeypatch.setattr(cache_search, "rebuild_from_cache_dicts", tracked_rebuild)

    def rebuild():
        try:
            orch.rebuild_search_index(["lang"], {"lang": _entries(7, "Iron")})
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)

    tq = threading.Thread(target=query)
    tq.start()
    assert query_started.wait(5)  # 查詢已取得 engine，停在真正執行 search 之前

    tr = threading.Thread(target=rebuild)
    tr.start()
    assert rebuild_built.wait(5)  # 新索引已建好，接下來就是 swap
    # 查詢仍在進行中：swap 不可關閉舊 engine（修正前這裡會立刻 close）
    assert not close_called.wait(0.5)

    release_query.set()
    tq.join(5)
    tr.join(5)

    assert errors == []
    assert len(results["q"]) == 5  # 查詢使用舊索引完成
    assert closed_while_query_active == [False]
    # swap 完成後改用新 engine / 新索引
    assert orch.get_engine() is not old_engine
    assert len(orch.search_cache("Iron", cache_type="lang", use_fuzzy=False)) == 7
    assert not list(tmp_path.glob("*.tmp*"))


def test_failed_rebuild_keeps_old_index_and_cleans_tmp(tmp_path, monkeypatch):
    """重建中途失敗：舊索引維持可用且不被關閉，暫存檔全部清除。"""
    orch = SearchOrchestrator(lambda: tmp_path)
    orch.rebuild_search_index(["lang"], {"lang": _entries(5, "Iron")})
    old_engine = orch.get_engine()

    def boom(engine, cache_types, cache_state):
        engine.index_batch(
            [{"key": "x", "src": "partial", "dst": "半", "cache_type": "lang"}]
        )
        raise RuntimeError("build failed")

    monkeypatch.setattr(cache_search, "rebuild_from_cache_dicts", boom)

    try:
        orch.rebuild_search_index(["lang"], {"lang": _entries(7, "Gold")})
    except RuntimeError as ex:
        assert "build failed" in str(ex)
    else:
        raise AssertionError("rebuild should propagate the build error")

    assert orch.get_engine() is old_engine
    assert len(orch.search_cache("Iron", cache_type="lang", limit=50)) == 5
    assert orch.search_cache("partial", cache_type="lang", limit=50) == []
    assert not list(tmp_path.glob("*.tmp*"))
