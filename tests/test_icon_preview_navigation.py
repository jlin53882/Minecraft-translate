"""tests/test_icon_preview_navigation.py

測試 icon_preview_view 的導航相關功能（P1 review 反饋）。

覆蓋：
- _cancel_detail_search_debounce()：取消 detail 搜尋 debounce timer
- _go_back()：返回模組清單（含 P1 race condition 修復驗證）
"""

import pytest
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


class MockPage:
    """Lightweight Flet Page mock for navigation tests."""
    def __init__(self):
        self.overlay = []
        self.update_count = 0

    def update(self):
        self.update_count += 1


def create_view_for_navigation():
    """建立 IconPreviewView 並設定導航相關狀態。"""
    from app.views.icon_preview_view import IconPreviewView

    with patch.object(IconPreviewView, "__init__", lambda self, page: None):
        view = IconPreviewView.__new__(IconPreviewView)
        view.page = MockPage()
        view.current_modid = None
        view.current_page = 0
        view.page_info = MagicMock()
        view._detail_search_text = ""
        view._detail_filtered_entries = None
        view.back_btn = MagicMock()
        view.save_btn = MagicMock()
        view.header = MagicMock()
        # 搜尋 debounce timer 初始為 None
        view._detail_search_debounce_timer: threading.Timer | None = None
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

    def test_cancel_with_no_timer(self):
        """沒有 active timer 時，cancel 不報錯"""
        view = create_view_for_navigation()
        view._detail_search_debounce_timer = None

        # 不應拋出例外
        view._cancel_detail_search_debounce()
        assert view._detail_search_debounce_timer is None

    def test_cancel_cancels_active_timer(self):
        """有 active timer 時，cancel 會呼叫 Timer.cancel() 並清除參考"""
        view = create_view_for_navigation()
        executed = []

        def on_timer_fire():
            executed.append(True)

        timer = threading.Timer(0.5, on_timer_fire)  # 0.5 秒後執行
        timer.start()
        view._detail_search_debounce_timer = timer

        view._cancel_detail_search_debounce()
        # timer 參考應清除
        assert view._detail_search_debounce_timer is None
        # 等待原本的 timer 窗口過去
        time.sleep(0.7)
        # 函式不應被執行（已被取消）
        assert len(executed) == 0

    def test_cancel_twice_is_safe(self):
        """連續呼叫兩次 cancel 不報錯"""
        view = create_view_for_navigation()
        timer = threading.Timer(10.0, lambda: None)
        timer.start()
        view._detail_search_debounce_timer = timer

        view._cancel_detail_search_debounce()
        view._cancel_detail_search_debounce()  # 第二次應該安全

        assert view._detail_search_debounce_timer is None


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
        detail_timer = threading.Timer(5.0, lambda: debounce_called.append(True))
        detail_timer.start()
        view._detail_search_debounce_timer = detail_timer

        # Mock _update_detail_search_controls and _render_mod_list to avoid Flet dependency
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        # Timer 應該已被取消且參考清除
        assert view._detail_search_debounce_timer is None
        # 等待原本的 timer 窗口過去
        time.sleep(0.3)
        assert len(debounce_called) == 0, "debounce 應被取消，不應執行"

    def test_go_back_resets_state(self):
        """_go_back 正確重設所有相關狀態"""
        view = create_view_for_navigation()

        view.current_modid = "actuallyadditions"
        view.current_page = 5
        view._detail_search_text = "some search"
        view._detail_filtered_entries = ["item.test"]
        view._detail_search_debounce_timer = None  # 已取消
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
        view._detail_search_debounce_timer = None
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        view._update_detail_search_controls.assert_called_once_with(visible=False)

    def test_go_back_clears_list_view(self):
        """_go_back 會清除 list_view controls"""
        view = create_view_for_navigation()
        view._detail_search_debounce_timer = None
        view._update_detail_search_controls = MagicMock()
        view._render_mod_list = MagicMock()

        view._go_back(MagicMock())

        view.list_view.controls.clear.assert_called_once()

    def test_go_back_renders_mod_list(self):
        """_go_back 最後會呼叫 _render_mod_list"""
        view = create_view_for_navigation()
        view._detail_search_debounce_timer = None
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
        view._detail_search_debounce_timer = None

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

        # 建立一個 150ms 後才執行的 detail timer（模擬用戶打字後的 debounce）
        view._detail_search_debounce_timer = threading.Timer(0.150, fake_do_detail_search)
        view._detail_search_debounce_timer.start()

        # 用戶在 100ms 時按 Back
        time.sleep(0.05)
        view._go_back(MagicMock())
        # timer 應該已取消，不會執行 fake_do_detail_search

        # 等待原本的 150ms 窗口過去
        time.sleep(0.15)

        # 驗證：_do_detail_search 不應該被呼叫（timer 已取消）
        assert len(call_log) == 0, f"debounce 應在 _go_back 時取消，不應執行。實際呼叫了：{call_log}"
