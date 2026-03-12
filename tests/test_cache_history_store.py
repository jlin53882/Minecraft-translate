from app.views.cache_manager import cache_history_store as hs


def test_history_append_and_load_recent(tmp_path):
    cache_root = str(tmp_path / 'cache_root')
    hs.history_append_event(cache_root, 'lang', {'key': 'k1', 'old_dst': 'a', 'new_dst': 'b', 'ts': '2026-03-12T23:00:00+08:00'})
    rows = hs.history_load_recent(cache_root, 'lang', 'k1', limit=5)

    assert len(rows) == 1
    assert rows[0]['new_dst'] == 'b'


def test_history_dirs_returns_none_when_root_missing():
    assert hs.history_dirs('', 'lang') == (None, None, None)
