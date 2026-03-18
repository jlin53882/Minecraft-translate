"""app/views/cache/cache_modal_query.py 單元測試。

用途：驗證 CacheQueryModal 搜尋功能正確性。
"""

from unittest.mock import MagicMock, patch
from app.views.cache.cache_modal_query import CacheQueryModal


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1

    def close(self, dialog):
        dialog.open = False


class TestCacheQueryModal:
    """CacheQueryModal 測試"""

    def test_initialization(self):
        """測試初始化"""
        page = _MockPage()

        modal = CacheQueryModal(page)

        assert modal._page_ref is page
        assert modal._dirty is False
        assert modal._search_timer is None
        assert modal.query_input is not None

    def test_initialization_with_initial_query(self):
        """測試帶初始搜尋字初始化"""
        page = _MockPage()

        modal = CacheQueryModal(page, initial_query="test")

        assert modal.query_input.value == "test"

    def test_on_input_sets_dirty(self):
        """測試輸入設定 dirty flag"""
        page = _MockPage()
        modal = CacheQueryModal(page)

        modal._on_input(MagicMock())

        assert modal._dirty is True

    def test_schedule_search_creates_timer(self):
        """測試 schedule 建立 timer"""
        page = _MockPage()
        modal = CacheQueryModal(page)

        modal._schedule_search()

        assert modal._search_timer is not None

    def test_schedule_search_cancels_existing_timer(self):
        """測試 schedule 取消舊 timer"""
        page = _MockPage()
        modal = CacheQueryModal(page)
        old_timer = modal._search_timer = MagicMock()

        modal._schedule_search()

        old_timer.cancel.assert_called_once()

    @patch("threading.Timer")
    def test_schedule_search_creates_new_timer(self, mock_timer_class):
        """測試 schedule 建立新 timer"""
        mock_timer = MagicMock()
        mock_timer_class.return_value = mock_timer

        page = _MockPage()
        modal = CacheQueryModal(page)

        modal._schedule_search()

        mock_timer_class.assert_called_once()
        mock_timer.start.assert_called_once()

    def test_on_confirm_returns_query(self):
        """測試確認回傳搜尋字"""
        page = _MockPage()
        on_complete = MagicMock()

        modal = CacheQueryModal(page, on_complete=on_complete)
        modal.query_input.value = "test query"

        modal._on_confirm()

        on_complete.assert_called_once_with({"query": "test query"})
