from app.views.rules_view import RulesView


class _Loop:
    def call_soon_threadsafe(self, func, *args, **kwargs):
        func(*args, **kwargs)


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.loop = _Loop()
    def update(self):
        self.updated += 1


def test_rules_view_initial_load_populates_data(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'a', 'to': 'b'}])

    view = RulesView(_Page())

    assert len(view.all_rules_data) == 1
    assert view.total_pages >= 1


def test_rules_view_search_filters_and_moves_to_matching_page(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'aaa', 'to': 'bbb'}, {'from': 'ccc', 'to': 'ddd'}])
    view = RulesView(_Page())

    class E: pass
    e = E(); e.control = type('C', (), {'value': 'ccc'})()
    view.on_search(e)

    assert view.search_results == [1]
    assert view.current_page == 1


def test_rules_view_validate_rule_catches_duplicate(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())
    rules = [{'from': 'a', 'to': 'x'}, {'from': 'a', 'to': 'y'}]

    ok, msg = view.validate_rule('a', 'y', rules, 1)

    assert ok is False
    assert '重複' in msg


def test_rules_view_add_row_moves_to_last_page(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    view.add_row_clicked(None)

    assert len(view.all_rules_data) == 1
    assert view.current_page == 1
