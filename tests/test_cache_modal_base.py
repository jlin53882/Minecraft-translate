"""app/views/cache/cache_modal_base.py 單元測試。

用途：驗證 CacheModalBase 基底類的功能正確性。
"""

from app.views.cache.cache_modal_base import CacheModalBase


class _MockPage:
    def __init__(self):
        self.overlay = []
        self.updated = 0

    def update(self):
        self.updated += 1

    def close(self, dialog):
        dialog.open = False


class _MockDialog:
    def __init__(self):
        self.open = False


class TestCacheModalBase:
    """CacheModalBase 測試"""

    def test_initialization(self):
        """測試初始化"""
        page = _MockPage()

        modal = CacheModalBase(page)

        assert modal._page_ref is page
        assert modal.on_complete is None
        assert modal.on_error is None
        assert modal._is_open is False

    def test_initialization_with_callbacks(self):
        """測試帶 callback 初始化"""
        page = _MockPage()

        def on_complete(data):
            pass

        def on_error(e):
            pass

        modal = CacheModalBase(page, on_complete=on_complete, on_error=on_error)

        assert modal.on_complete is on_complete
        assert modal.on_error is on_error

    def test_open_creates_dialog(self):
        """測試 open 建立 dialog"""
        page = _MockPage()
        modal = CacheModalBase(page)

        modal.open()

        assert modal._is_open is True
        assert modal._dialog is not None
        assert len(page.overlay) == 1
        assert modal._dialog.open is True
        assert page.updated == 1

    def test_open_idempotent(self):
        """測試重複 open 不會重複建立"""
        page = _MockPage()
        modal = CacheModalBase(page)

        modal.open()
        modal.open()

        assert len(page.overlay) == 1

    def test_close_removes_dialog(self):
        """測試 close 移除 dialog"""
        page = _MockPage()
        modal = CacheModalBase(page)

        modal.open()
        modal.close()

        assert modal._is_open is False
        assert modal._dialog is None
        # dialog.open 應設為 False（由 mock close 處理）
        assert modal._dialog is None

    def test_close_idempotent(self):
        """測試重複 close 不會出錯"""
        page = _MockPage()
        modal = CacheModalBase(page)

        modal.close()
        assert modal._is_open is False

    def test_do_complete_calls_callback(self):
        """測試 _do_complete 呼叫 callback"""
        page = _MockPage()
        result = {}

        def on_complete(data):
            result.update(data)

        modal = CacheModalBase(page, on_complete=on_complete)
        modal._do_complete({"key": "value"})

        assert result == {"key": "value"}
        assert modal._is_open is False

    def test_do_error_calls_callback(self):
        """測試 _do_error 呼叫 callback"""
        page = _MockPage()
        error_result = {}

        def on_error(e):
            error_result.update({"error": e})

        modal = CacheModalBase(page, on_error=on_error)
        modal._do_error("test error")

        assert error_result == {"error": "test error"}
        assert modal._is_open is False
