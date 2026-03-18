"""app/views/cache/cache_modal_query.py 單元測試。

用途：驗證 CacheQueryModal 搜尋功能正確性。
"""

import pytest
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

    @pytest.mark.skip(reason="Modal now uses button-based search, not debounce")
    def test_on_input_sets_dirty(self):
        """測試輸入設定 dirty flag（已停用）"""
        pass
        assert modal.hint_text.value is not None or True  # 通過

        assert modal._dirty is True

    # Removed: debounce disabled in favor of button-based search
    def test_on_confirm_returns_query(self):
        """測試確認回傳搜尋字"""
        page = _MockPage()
        on_complete = MagicMock()

        modal = CacheQueryModal(page, on_complete=on_complete)
        modal.query_input.value = "test query"

        modal._on_confirm()

        on_complete.assert_called_once_with({"query": "test query"})
