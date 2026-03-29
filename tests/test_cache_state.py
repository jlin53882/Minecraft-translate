from app.views.cache_manager.cache_state import CacheHistoryState, CacheQueryState, CacheShardState


def test_cache_query_state_defaults():
    s = CacheQueryState()
    assert s.query_results == []
    assert s.query_page == 1
    assert s.query_page_size == 50


def test_cache_shard_state_defaults():
    s = CacheShardState()
    assert s.selected_type == ''
    assert s.page == 1
    assert s.src_mode == 'preview'


def test_cache_history_state_defaults():
    s = CacheHistoryState()
    assert s.history_window_source is None
    assert s.query_records == []
    assert s.shard_records == []
