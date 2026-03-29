from app.view_registry import DEFAULT_WINDOW_SIZE, VIEW_WINDOW_SIZES, get_window_size


def test_get_window_size_returns_specific_size_for_known_view():
    assert get_window_size('cache') == VIEW_WINDOW_SIZES['cache']


def test_get_window_size_returns_default_for_unknown_view():
    assert get_window_size('unknown') == DEFAULT_WINDOW_SIZE
