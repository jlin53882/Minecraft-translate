# CacheQueryView 單元測試

import pytest
from unittest.mock import MagicMock
import flet as ft
from app.views.cache.cache_query_view import CacheQueryView


class TestCacheQueryView:
    """CacheQueryView 測試"""

    def test_init_creates_widgets(self):
        """測試初始化時創建必要的 widgets"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.update = MagicMock()

        view = CacheQueryView(page, cache_view)

        assert view.tf_query_input is not None
        assert view.dd_query_mode is not None
        assert view.dd_query_type is not None
        assert view.btn_query_search is not None
        assert view.btn_query_clear is not None

    def test_set_type_options(self):
        """測試設定分類選項"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.update = MagicMock()

        view = CacheQueryView(page, cache_view)

        options = [
            ft.dropdown.Option("lang", "Lang"),
            ft.dropdown.Option("mods", "Mods"),
        ]
        view.set_type_options(options)

        assert len(view.dd_query_type.options) == 2
        assert view.dd_query_type.value == "ALL"

    def test_on_mode_change_shows_hint(self):
        """測試模式變更顯示提示"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.update = MagicMock()

        view = CacheQueryView(page, cache_view)
        view._on_mode_change()

        assert view.query_change_hint.visible is True
        cache_view.update.assert_called()

    def test_on_type_change_shows_hint(self):
        """測試分類變更顯示提示"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.update = MagicMock()

        view = CacheQueryView(page, cache_view)
        view._on_type_change()

        assert view.query_change_hint.visible is True
        cache_view.update.assert_called()

    def test_on_search_triggers_callback(self):
        """測試搜尋觸發回調"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.ui_busy = False
        cache_view.update = MagicMock()
        cache_view._notify = MagicMock()
        cache_view._on_query_view_search = MagicMock()

        view = CacheQueryView(page, cache_view)
        view.tf_query_input.value = "test query"
        view.dd_query_mode.value = "KEY"
        view.dd_query_type.value = "ALL"

        view._on_search()

        cache_view._on_query_view_search.assert_called_once_with(
            query="test query",
            mode="KEY",
            dtype="ALL",
        )

    def test_on_search_empty_query_shows_warning(self):
        """測試空查詢時顯示警告"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.ui_busy = False
        cache_view._notify = MagicMock()

        view = CacheQueryView(page, cache_view)
        view.tf_query_input.value = ""

        view._on_search()

        cache_view._notify.assert_called_with("請輸入查詢內容", "warn")

    def test_on_search_busy_state_ignores(self):
        """測試忙碌狀態時忽略搜尋"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.ui_busy = True
        cache_view._notify = MagicMock()

        view = CacheQueryView(page, cache_view)
        view.tf_query_input.value = "test"

        view._on_search()

        cache_view._notify.assert_called_with("目前忙碌中，暫停搜尋", "warn")

    def test_on_clear_resets_ui(self):
        """測試清除重置 UI"""
        page = MagicMock()
        cache_view = MagicMock()
        cache_view.update = MagicMock()
        cache_view._on_query_view_clear = MagicMock()

        view = CacheQueryView(page, cache_view)
        view.tf_query_input.value = "test query"
        view.query_change_hint.visible = True

        view._on_clear()

        assert view.tf_query_input.value == ""
        assert view.query_change_hint.visible is False
        assert len(view.query_result_list.controls) == 0
        cache_view._on_query_view_clear.assert_called_once()
