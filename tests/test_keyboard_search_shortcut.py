"""C16：Ctrl+F 聚焦目前頁面的搜尋框（原本 search_field 從未被設定，快捷鍵無作用）。"""

from types import SimpleNamespace

from app.ui.keyboard_shortcuts import KeyboardShortcutHandler


class _Page:
    def __init__(self):
        self.tasks = []

    def run_task(self, handler, *args):
        self.tasks.append(handler)


def _event(key):
    return SimpleNamespace(key=key, ctrl=True, meta=False, shift=False, alt=False)


def test_ctrl_f_focuses_current_view_search_field():
    page = _Page()
    handler = KeyboardShortcutHandler(page, [], lambda idx: None)
    search_box = SimpleNamespace(visible=True, focus=lambda: None)
    rules_view = SimpleNamespace(search_box=search_box)
    wrapped = SimpleNamespace(content=rules_view)  # wrap_view 外框
    handler.set_current_view_getter(lambda: wrapped)

    handler.handle_keyboard(_event("F"))
    assert page.tasks == [search_box.focus]


def test_ctrl_f_prefers_visible_field_and_ignores_pages_without_search():
    page = _Page()
    handler = KeyboardShortcutHandler(page, [], lambda idx: None)
    hidden = SimpleNamespace(visible=False, focus=lambda: None)
    shown = SimpleNamespace(visible=True, focus=lambda: None)
    view = SimpleNamespace(detail_search_tf=hidden, mod_search_tf=shown)
    handler.set_current_view_getter(lambda: SimpleNamespace(content=view))
    handler.handle_keyboard(_event("f"))
    assert page.tasks == [shown.focus]

    handler.set_current_view_getter(lambda: SimpleNamespace(content=SimpleNamespace()))
    handler.handle_keyboard(_event("f"))
    assert len(page.tasks) == 1
