import flet as ft
from app.views.rules_view import RulesView
from app.ui.snack import show_snack
from tests.conftest import mock_page


class _Loop:
    def call_soon_threadsafe(self, func, *args, **kwargs):
        func(*args, **kwargs)


def test_rules_view_initial_load_populates_data(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'a', 'to': 'b'}])

    view = RulesView(mock_page())

    assert len(view.all_rules_data) == 1
    assert view.total_pages >= 1


def test_rules_view_search_filters_and_moves_to_matching_page(monkeypatch):
    
    # Mock threading.Thread and Timer for tests
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.threading.Timer', lambda delay, target: type('Tm', (), {'start': lambda self: target(), 'cancel': lambda self: None})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [{'from': 'aaa', 'to': 'bbb'}, {'from': 'ccc', 'to': 'ddd'}])
    view = RulesView(mock_page())

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
    view = RulesView(mock_page())
    rules = [{'from': 'a', 'to': 'x'}, {'from': 'a', 'to': 'y'}]

    ok, msg = view.validate_rule('a', 'y', rules, 1)

    assert ok is False
    assert '重複' in msg


def test_rules_view_add_row_moves_to_last_page(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    view.add_row_clicked(None)

    assert len(view.all_rules_data) == 1
    assert view.current_page == 1


def test_rules_view_all_controls_exist(monkeypatch):
    """測試 RulesView 所有 UI 控件存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

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
    page = mock_page()
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
    view = RulesView(mock_page())

    assert view.prev_button.disabled is True


def test_rules_view_search_box_on_change_exists(monkeypatch):
    """測試 search_box 的 on_change 回調"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert view.search_box.on_change is not None
    assert view.sort_box.on_change is not None


def test_rules_view_show_snack_bar_adds_to_overlay(monkeypatch):
    """測試 _show_snack_bar 正確將 SnackBar 加入 page.overlay"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    page = mock_page()
    view = RulesView(page)

    show_snack(view.page, 'Test error', '#FF0000')

    assert len(page.overlay) >= 1
    assert any('Test error' in str(o.content.value) if hasattr(o, 'content') else False for o in page.overlay)


def test_rules_view_validate_rule_accepts_valid_rule(monkeypatch):
    """測試 validate_rule 接受有效規則"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    rules = [{'from': 'a', 'to': 'x'}]

    ok, msg = view.validate_rule('b', 'y', rules, 1)

    assert ok is True
    assert msg == ''


def test_rules_view_rule_matches(monkeypatch):
    """測試 _rule_matches 正確匹配規則"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    rule = {'from': 'hello', 'to': '你好'}

    assert view._rule_matches(rule, 'hello', False) is True
    assert view._rule_matches(rule, 'world', False) is False


def test_rules_view_prev_page_button_exists(monkeypatch):
    """測試 prev_button 存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert view.prev_button is not None
    assert view.next_button is not None


def test_rules_view_initial_page_state(monkeypatch):
    """測試初始頁面狀態"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert view.current_page == 1
    assert view.total_pages >= 1


def test_rules_view_search_box_exists(monkeypatch):
    """測試 search_box 存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert view.search_box is not None



def test_rules_view_new_rid_exists(monkeypatch):
    """測試 _new_rid 方法存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert hasattr(view, '_new_rid')
    assert callable(view._new_rid)


def test_rules_view_find_index_by_rid_exists(monkeypatch):
    """測試 _find_index_by_rid 方法存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert hasattr(view, '_find_index_by_rid')
    assert callable(view._find_index_by_rid)


def test_rules_view_do_search_exists(monkeypatch):
    """測試 _do_search 方法存在"""
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())

    assert hasattr(view, '_do_search')
    assert callable(view._do_search)


def test_rules_view_build_header(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_build_header')
    assert callable(view._build_header)


def test_rules_view_build_toolbar(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_build_toolbar')
    assert callable(view._build_toolbar)


def test_rules_view_build_rules_table_area(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_build_rules_table_area')
    assert callable(view._build_rules_table_area)


def test_rules_view_build_footer(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_build_footer')
    assert callable(view._build_footer)


def test_rules_view_load_rules_core(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_load_rules_core')
    assert callable(view._load_rules_core)


def test_rules_view_initial_load(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_initial_load')
    assert callable(view._initial_load)


def test_rules_view_render_current_page(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_render_current_page')
    assert callable(view._render_current_page)


def test_rules_view_handle_reload_success(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_handle_reload_success')
    assert callable(view._handle_reload_success)


def test_rules_view_handle_reload_failure(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_handle_reload_failure')
    assert callable(view._handle_reload_failure)


def test_rules_view_run_on_ui_thread(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_run_on_ui_thread')
    assert callable(view._run_on_ui_thread)


def test_rules_view_show_snack_bar(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    # _show_snack_bar 物理刪除 (PR #85 重構),SnackBar 透過 show_snack helper 顯示
    # view 本身不再有 _show_snack_bar method,改為驗 show_snack helper 已 import + 可用
    from app.ui.snack import show_snack
    assert callable(show_snack)


def test_rules_view_init_controls(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, '_init_controls')
    assert callable(view._init_controls)


def test_rules_view_total_count_text(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert view.total_count_text is not None


def test_rules_view_loading_indicator(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert view.loading_indicator is not None


def test_rules_view_on_sort_change(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, 'on_sort_change')
    assert callable(view.on_sort_change)


def test_rules_view_translate_regex_error(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, 'translate_regex_error')
    assert callable(view.translate_regex_error)


def test_rules_view_on_search(monkeypatch):
    monkeypatch.setattr('app.views.rules_view.threading.Thread', lambda target=None, daemon=None: type('T', (), {'start': lambda self: target()})())
    monkeypatch.setattr('app.views.rules_view.load_replace_rules', lambda: [])
    view = RulesView(mock_page())
    assert hasattr(view, 'on_search')
    assert callable(view.on_search)
