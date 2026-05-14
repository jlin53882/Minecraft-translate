import flet as ft
from app.views.rules_view import RulesView


class _Loop:
    def call_soon_threadsafe(self, func, *args, **kwargs):
        func(*args, **kwargs)


class _Page:
    def __init__(self):
        self.overlay = []
        self.updated = 0
        self.loop = _Loop()
        self._tasks = []

    def update(self):
        self.updated += 1

    def run_task(self, coro, *args):
        self._tasks.append((coro, args))

    def _run_all_tasks(self):
        for coro, args in self._tasks:
            result = coro(*args)
            if result is not None:
                try:
                    result.send(None)
                except StopIteration:
                    pass


def test_rules_view_initial_load_populates_data(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'a', 'to': 'b'}])

    view = RulesView(_Page())

    assert len(view.all_rules_data) == 1
    assert view.total_pages >= 1


def test_rules_view_search_filters_and_moves_to_matching_page(monkeypatch):
    
    # Mock threading.Thread and Timer for tests
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.threading.Timer', lambda delay, target: type('Tm', (), {'start': lambda self: target(), 'cancel': lambda self: None})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'aaa', 'to': 'bbb'}, {'from': 'ccc', 'to': 'ddd'}])
    view = RulesView(_Page())

    class E: pass
    e = E(); e.control = type('C', (), {'value': 'ccc'})()
    view.on_search(e)

    # 搜尋結果應該是 rule 物件列表（不是 index 列表）
    assert view.search_results is not None
    assert len(view.search_results) == 1
    assert view.search_results[0].get('from') == 'ccc'
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


def test_rules_view_all_controls_exist(monkeypatch):
    """測試 RulesView 所有 UI 控件存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    assert isinstance(view.loading_indicator, ft.ProgressRing)
    assert view.loading_indicator.visible is False

    assert isinstance(view.page_info, ft.Text)
    assert '頁面' in view.page_info.value

    assert isinstance(view.total_count_text, ft.Text)
    assert '共' in view.total_count_text.value

    assert isinstance(view.prev_button, ft.IconButton)
    assert view.prev_button.icon == ft.Icons.ARROW_BACK
    assert view.prev_button.disabled is True

    assert isinstance(view.next_button, ft.IconButton)
    assert view.next_button.icon == ft.Icons.ARROW_FORWARD
    assert view.next_button.disabled is True

    assert isinstance(view.page_jump_field, ft.TextField)
    assert view.page_jump_field.width == 70
    assert view.page_jump_field.text_align == ft.TextAlign.CENTER

    assert isinstance(view.search_box, ft.TextField)
    assert view.search_box.label == '搜尋規則 (由/至)'

    assert isinstance(view.sort_box, ft.Dropdown)
    assert view.sort_box.label == '排序方式'

    assert isinstance(view.rules_table, ft.DataTable)


def test_rules_view_on_page_jump_submit_invalid_page(monkeypatch):
    """測試頁碼跳轉無效時的處理"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'a', 'to': 'b'}] * 30)
    page = _Page()
    view = RulesView(page)

    class E:
        pass

    e = E()
    e.control = type('C', (), {'value': '9999'})()

    view.on_page_jump_submit(e)

    assert page.overlay


def test_rules_view_prev_button_disabled_when_on_first_page(monkeypatch):
    """測試第一頁時 prev_button 應該 disabled"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'a', 'to': 'b'}] * 30)
    view = RulesView(_Page())

    assert view.prev_button.disabled is True


def test_rules_view_search_box_on_change_exists(monkeypatch):
    """測試 search_box 的 on_change 回調"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    assert view.search_box.on_change is not None
    assert view.sort_box.on_change is not None


def test_rules_view_show_snack_bar_adds_to_overlay(monkeypatch):
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    page = _Page()
    view = RulesView(page)

    view._show_snack_bar('Test error', '#FF0000')

    assert len(page.overlay) >= 1
    assert any('Test error' in str(o.content.value) if hasattr(o, 'content') else False for o in page.overlay)


def test_rules_view_validate_rule_accepts_valid_rule(monkeypatch):
    """測試 validate_rule 接受有效規則"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())
    rules = [{'from': 'a', 'to': 'x'}]

    ok, msg = view.validate_rule('b', 'y', rules, 1)

    assert ok is True
    assert msg == ''


def test_rules_view_rule_matches(monkeypatch):
    """測試 _rule_matches 正確匹配規則"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    rule = {'from': 'hello', 'to': '你好'}

    assert view._rule_matches(rule, 'hello', False) is True
    assert view._rule_matches(rule, 'world', False) is False


def test_rules_view_prev_page_button_exists(monkeypatch):
    """測試 prev_button 存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    assert view.prev_button is not None
    assert view.next_button is not None


def test_rules_view_initial_page_state(monkeypatch):
    """測試初始頁面狀態"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    assert view.current_page == 1
    assert view.total_pages >= 1


def test_rules_view_search_box_exists(monkeypatch):
    """測試 search_box 存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(_Page())

    assert view.search_box is not None

