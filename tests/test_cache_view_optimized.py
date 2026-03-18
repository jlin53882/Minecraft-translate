"""app/views/cache/cache_view_optimized.py 單元測試。

用途：驗證 CacheViewOptimized 髒標記機制正確性。
"""

from unittest.mock import MagicMock, patch
from app.views.cache.cache_view_optimized import CacheViewOptimized


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1


class TestCacheViewOptimized:
    """CacheViewOptimized 測試"""

    def test_initialization(self):
        """測試初始化"""
        page = _MockPage()

        view = CacheViewOptimized(page)

        assert view._page_ref is page
        assert view._dirty_flags == {
            "query": False,
            "shard": False,
            "overview": False,
        }
        assert view._update_timer is None

    def test_initialization_without_page(self):
        """測試不帶 page 初始化"""
        view = CacheViewOptimized()

        assert view._page_ref is None

    def test_mark_dirty_sets_flag(self):
        """測試 mark_dirty 設定 flag"""
        page = _MockPage()
        view = CacheViewOptimized(page)

        view.mark_dirty("query")

        assert view._dirty_flags["query"] is True

    def test_mark_dirty_ignores_invalid_area(self):
        """測試無效 area 不影響 dirty flags"""
        page = _MockPage()
        view = CacheViewOptimized(page)

        # 雖然無效 area 不會設定 dirty flag，但仍會排程更新（基於 page 存在）
        view.mark_dirty("invalid_area")

        # dirty flags 維持不變
        assert all(not v for v in view._dirty_flags.values())

    def test_mark_dirty_ignores_when_no_page(self):
        """測試無 page 時不處理"""
        view = CacheViewOptimized()

        view.mark_dirty("query")

        assert view._dirty_flags["query"] is False

    def test_mark_dirty_schedules_update(self):
        """測試 mark_dirty 排程更新"""
        page = _MockPage()
        view = CacheViewOptimized(page)

        view.mark_dirty("query")

        assert view._update_timer is not None

    @patch("threading.Timer")
    def test_schedule_update_creates_timer(self, mock_timer_class):
        """測試排程更新建立 timer"""
        mock_timer = MagicMock()
        mock_timer_class.return_value = mock_timer

        page = _MockPage()
        view = CacheViewOptimized(page)

        view._schedule_update()

        mock_timer_class.assert_called_once()
        mock_timer.start.assert_called_once()

    def test_schedule_update_cancels_existing_timer(self):
        """測試排程更新取消舊 timer"""
        page = _MockPage()
        view = CacheViewOptimized(page)
        old_timer = view._update_timer = MagicMock()

        view._schedule_update()

        old_timer.cancel.assert_called_once()

    def test_do_update_skips_when_no_page(self):
        """測試無 page 時跳過更新"""
        view = CacheViewOptimized()
        view._dirty_flags["query"] = True

        view._do_update()

        assert view._dirty_flags["query"] is True

    def test_do_update_skips_when_no_dirty_flags(self):
        """測試無髒標記時跳過"""
        page = _MockPage()
        view = CacheViewOptimized(page)

        view._do_update()

        assert page.updated == 0

    def test_do_update_clears_dirty_flags(self):
        """測試更新後清除髒標記"""
        page = _MockPage()
        view = CacheViewOptimized(page)
        view._dirty_flags["query"] = True
        view._render_dirty_areas = MagicMock()
        view.update = MagicMock()  # mock update

        view._do_update()

        view.update.assert_called_once()
        assert view._dirty_flags["query"] is False

    def test_do_update_renders_dirty_areas(self):
        """測試更新呼叫渲染"""
        page = _MockPage()
        view = CacheViewOptimized(page)
        view._dirty_flags["query"] = True
        view._render_dirty_areas = MagicMock()

        view._do_update()

        view._render_dirty_areas.assert_called_once()
