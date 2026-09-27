from app.view_registry import DEFAULT_WINDOW_SIZE, VIEW_WINDOW_SIZES, get_window_size


def test_get_window_size_returns_specific_size_for_known_view():
    assert get_window_size('cache') == VIEW_WINDOW_SIZES['cache']


def test_get_window_size_returns_default_for_unknown_view():
    assert get_window_size('unknown') == DEFAULT_WINDOW_SIZE


def test_views_are_built_lazily(monkeypatch):
    """B15：啟動時不建立全部頁面，第一次取用才建立，之後重用同一個實例。"""
    from app import view_registry as vr

    built = []

    def fake_import(key, page, file_picker):
        built.append(key)
        return object()

    monkeypatch.setattr(vr, "_lazy_import_view", fake_import)
    monkeypatch.setattr(vr, "wrap_view", lambda v: v)
    registry = vr.build_view_registry(page=None, file_picker=None)

    assert built == []
    assert registry[0]["key"] == "config"
    assert vr.built_view(registry[2]) is None

    hooked = []
    registry[11].on_build(hooked.append)
    first = registry[11]["view"]
    assert built == ["pipeline"]
    assert hooked == [first]
    assert registry[11]["view"] is first
    assert vr.built_view(registry[11]) is first
    assert built == ["pipeline"]
