"""app/views/cache/cache_modal_shard.py 單元測試。

用途：驗證 CacheShardModal 分片管理功能正確性。
"""

from unittest.mock import MagicMock
from app.views.cache.cache_modal_shard import CacheShardModal


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1

    def close(self, dialog):
        dialog.open = False


class TestCacheShardModal:
    """CacheShardModal 測試"""

    def test_initialization(self):
        """測試初始化"""
        page = _MockPage()

        modal = CacheShardModal(page)

        assert modal._page_ref is page
        assert modal._current_tab == "key"
        assert modal.tabs is not None
        assert modal.initial_data is None

    def test_initialization_with_initial_data(self):
        """測試帶初始資料初始化"""
        page = _MockPage()
        initial = {"key": "value"}

        modal = CacheShardModal(page, initial_data=initial)

        assert modal.initial_data is initial

    def test_initialization_sets_default_size(self):
        """測試預設尺寸"""
        page = _MockPage()

        modal = CacheShardModal(page)

        assert modal.width == 600
        assert modal.height == 500

    def test_tabs_has_three_tabs(self):
        """測試有三個標籤"""
        page = _MockPage()

        modal = CacheShardModal(page)

        assert len(modal.tabs.tabs) == 3
        assert modal.tabs.tabs[0].text == "Key 列表"
        assert modal.tabs.tabs[1].text == "SRC 編輯"
        assert modal.tabs.tabs[2].text == "DST 編輯"

    def test_on_tab_change_updates_current_tab(self):
        """測試標籤切換更新"""
        page = _MockPage()
        modal = CacheShardModal(page)
        mock_event = MagicMock()
        mock_event.control.selected_index = 1

        modal._on_tab_change(mock_event)

        assert modal._current_tab == "src"

    def test_on_tab_change_handles_all_tabs(self):
        """測試所有標籤"""
        page = _MockPage()
        modal = CacheShardModal(page)

        for index, expected in enumerate(["key", "src", "dst"]):
            mock_event = MagicMock()
            mock_event.control.selected_index = index
            modal._on_tab_change(mock_event)
            assert modal._current_tab == expected

    def test_on_confirm_returns_current_tab(self):
        """測試確認回傳當前標籤"""
        page = _MockPage()
        on_complete = MagicMock()
        modal = CacheShardModal(page, on_complete=on_complete)
        modal._current_tab = "src"

        modal._on_confirm()

        # 現在回傳包含更多欄位
        call_args = on_complete.call_args[0][0]
        assert call_args["tab"] == "src"

    def test_build_key_tab_returns_container(self):
        """測試建立 Key 標籤"""
        page = _MockPage()
        modal = CacheShardModal(page)

        result = modal._build_key_tab()

        assert result is not None
