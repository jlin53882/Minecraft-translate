"""tests/test_icon_preview_navigation.py

測試 icon_preview_view 的導航相關功能（P1 review 反饋）。

覆蓋：
- _cancel_detail_search_debounce()：取消 detail 搜尋 debounce（event loop 上的 Debouncer）
- _go_back()：返回模組清單（含 P1 race condition 修復驗證）
"""

import asyncio
from unittest.mock import MagicMock, patch

from app.ui.debounce import Debouncer


class MockPage:
    """Lightweight Flet Page mock for navigation tests."""
    def __init__(self):
        self.overlay = []
        self.update_count = 0
        self.tasks = []

    def update(self):
        self.update_count += 1

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))

    def run_pending_tasks(self):
        """模擬 event loop 執行所有排程中的 task（含 debounce 延遲）。"""
        tasks, self.tasks = self.tasks, []
        for handler, args in tasks:
            asyncio.run(handler(*args))


def create_view_for_navigation():
    """建立 IconPreviewView 並設定導航相關狀態。"""
    from app.views.icon_preview_view import IconPreviewView

    with patch.object(IconPreviewView, "__init__", lambda self, page: None):
        view = IconPreviewView.__new__(IconPreviewView)
        view._page = MockPage()
        view.current_modid = None
        view.current_page = 0
        view.page_info = MagicMock()
        view._detail_search_text = ""
        view._detail_filtered_entries = None
        view.back_btn = MagicMock()
        view.save_btn = MagicMock()
        view.header = MagicMock()
        view._detail_search_debouncer = Debouncer(lambda: view._page, 0.01)
        # Mock detail search UI widgets
        view.detail_search_tf = MagicMock()
        view.detail_search_status = MagicMock()
        # Mock list_view
        view.list_view = MagicMock()
        view.list_view.controls = MagicMock()
        # Mock controls
        view.controls = []
        view.update_count = 0

        def mock_update():
            view.update_count += 1

        view.update = mock_update
    return view


# ==================================================
# P1: _cancel_detail_search_debounce
# ==================================================

class TestCancelDetailSearchDebounce:
    """_cancel_detail_search_debounce 的各種情境測試。"""

    def test_cancel_with_no_pending_call(self):
        """沒有排程中的 debounce 時，cancel 不報錯"""
        view = create_view_for_navigation()
        view._cancel_detail_search_debounce()
        assert view._page.tasks == []

    def test_cancel_prevents_pending_call(self):
        """有排程中的 debounce 時，cancel 後 callback 不會被執行"""
        view = create_view_for_navigation()
        executed = []
        view._detail_search_debouncer.call(lambda: executed.append(True))

        view._cancel_detail_search_debounce()
        view._page.run_pending_tasks()

        assert executed == []

    def test_latest_call_wins(self):
        """連續輸入只執行最後一次"""
        view = create_view_for_navigation()
        executed = []
        view._detail_search_debouncer.call(executed.append, "a")
        view._detail_search_debouncer.call(executed.append, "ab")
        view._page.run_pending_tasks()
        assert executed == ["ab"]

    def test_cancel_twice_is_safe(self):
        """連續呼叫兩次 cancel 不報錯"""
        view = create_view_for_navigation()
        view._detail_search_debouncer.call(lambda: None)
        view._cancel_detail_search_debounce()
        view._cancel_detail_search_debounce()  # 第二次應該安全
        view._page.run_pending_tasks()


# ==================================================
# P1: _go_back（含 race condition 修復驗證）
# ==================================================

class TestGoBack:
    """_go_back 的各種情境測試。"""

    def test_go_back_cancels_detail_debounce(self):
        """P1 修復驗證：_go_back 會取消 detail debounce timer"""
        view = create_view_for_navigation()

        # 模擬 detail 搜尋打字後，debounce timer 正在等待
        view.current_modid = "actuallyadditions"
        view._detail_search_text = "test"
        view._detail_filtered_entries = []
        debounce_called = []
        view._detail_search_debouncer.call(lambda: debounce_called.append(True))

        # Mock _update_detail_search_controls and _render_mod_list to avoid Flet dependency
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        view._page.run_pending_tasks()
        assert len(debounce_called) == 0, "debounce 應被取消，不應執行"

    def test_go_back_resets_state(self):
        """_go_back 正確重設所有相關狀態"""
        view = create_view_for_navigation()

        view.current_modid = "actuallyadditions"
        view.current_page = 5
        view._detail_search_text = "some search"
        view._detail_filtered_entries = ["item.test"]
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        assert view.current_modid is None
        assert view.current_page == 0
        assert view._detail_search_text == ""
        assert view._detail_filtered_entries is None
        view.page_info.value = ""  # verify MagicMock called
        view._update_detail_search_controls.assert_called_once_with(visible=False)
        view.list_view.controls.clear.assert_called_once()
        view._render_mod_list.assert_called_once()

    def test_go_back_calls_update_detail_search_controls(self):
        """_go_back 會隱藏 detail 搜尋 UI"""
        view = create_view_for_navigation()
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        view._update_detail_search_controls.assert_called_once_with(visible=False)

    def test_go_back_clears_list_view(self):
        """_go_back 會清除 list_view controls"""
        view = create_view_for_navigation()
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        view.list_view.controls.clear.assert_called_once()

    def test_go_back_renders_mod_list(self):
        """_go_back 最後會呼叫 _render_mod_list"""
        view = create_view_for_navigation()
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        view._render_mod_list.assert_called_once()


# ==================================================
# 整合：race condition 模擬測試
# ==================================================

class TestRaceCondition:
    """模擬 P1 描述的 race condition 情境。"""

    def test_rapid_back_press_does_not_overwrite_mod_list(self):
        """
        情境：用戶在 detail 搜尋打字 → 0.1 秒內按 Back

        在舊版（無 _cancel_detail_search_debounce）中，
        debounce timer 會在 150ms 後執行 _do_detail_search，
        用空的 _detail_search_text 覆蓋列表。

        新版應該在 _go_back 時立即取消 timer，防止這個行為。
        """
        view = create_view_for_navigation()

        # 設定 detail 狀態
        view.current_modid = "actuallyadditions"
        view._detail_search_text = "atomic"
        view._detail_filtered_entries = []

        # Mock _do_detail_search 來追蹤是否被呼叫
        call_log = []

        def fake_do_detail_search():
            # 模擬舊版行為：在 _detail_search_text 已重設後，
            # debounce 仍用空的 keyword 呼叫 _render_current_page
            call_log.append(("_do_detail_search", view._detail_search_text))

        # 將 _do_detail_search 替換為 spy
        original_do_detail_search = view._do_detail_search
        view._do_detail_search = fake_do_detail_search
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        # 模擬用戶打字後排程 debounce，尚未觸發前按 Back
        view._detail_search_debouncer.call(view._do_detail_search)
        view._go_back(MagicMock())

        # event loop 執行到 debounce 時應該已被取消
        view._page.run_pending_tasks()

        # 驗證：_do_detail_search 不應該被呼叫（timer 已取消）
        assert len(call_log) == 0, f"debounce 應在 _go_back 時取消，不應執行。實際呼叫了：{call_log}"
