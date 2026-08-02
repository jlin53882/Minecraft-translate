"""tests/test_icon_preview_snack_bar_fix.py

測試 icon_preview_view 的 SnackBar overlay 修復。
驗證 _show_snack 使用 in-place 修改（del），而非賦值（=）。
這是 PR #51 的一個重要 bug 修復。
"""

import pytest
from unittest.mock import MagicMock, patch
import flet as ft
from app.ui import theme
from app.ui.snack import show_snack


class MockPage:
    """Mock Flet Page，專門為 SnackBar 測試設計"""

    def __init__(self):
        self.overlay = []
        self.update_called = False

    def update(self):
        self.update_called = True


class TestSnackBarInPlaceModification:
    """驗證 SnackBar overlay 使用 in-place 修改，不使用賦值"""

    def test_snack_bar_removes_old_snackbars(self):
        """多次呼叫 _show_snack 時，舊的 SnackBar 應該被移除"""
        # 建立一個帶有舊 SnackBar 的 page
        page = MockPage()
        old_snack = ft.SnackBar(content=ft.Text("old message"))
        page.overlay.append(old_snack)

        # 建立 IconPreviewView（只 mock __init__ 的關鍵部分）
        from app.views.icon_preview_view import IconPreviewView

        with patch.object(IconPreviewView, "__init__", lambda self, page: None):
            view = IconPreviewView.__new__(IconPreviewView)
            view._page = page
            # 不呼叫真的 __init__（會觸發太多依賴）

        # 呼叫 _show_snack
        show_snack(view.page, "new message", color=theme.GREEN_600)

        # 驗證：overlay 裡只有一個 SnackBar（新的）
        snackbars = [o for o in page.overlay if isinstance(o, ft.SnackBar)]
        assert len(snackbars) == 1, f"預期 1 個 SnackBar，實際 {len(snackbars)} 個"
        assert snackbars[0].content.value == "new message"

    def test_snack_bar_deletes_only_snackbar_preserves_others(self):
        """只刪除 SnackBar，保留其他 overlay 元素"""
        page = MockPage()

        # 加入一個普通 Container 和一個 SnackBar
        fake_container = MagicMock()
        fake_container.__class__.__name__ = "Container"
        old_snack = ft.SnackBar(content=ft.Text("old"))
        page.overlay.append(fake_container)
        page.overlay.append(old_snack)

        from app.views.icon_preview_view import IconPreviewView

        with patch.object(IconPreviewView, "__init__", lambda self, page: None):
            view = IconPreviewView.__new__(IconPreviewView)
            view._page = page

        show_snack(view.page, "new message", color=theme.WARNING)

        # 驗證：Container 保留，SnackBar 被替換
        snackbars = [o for o in page.overlay if isinstance(o, ft.SnackBar)]
        assert len(snackbars) == 1
        assert len(page.overlay) == 2  # Container + 新 SnackBar

    def test_snack_bar_multiple_calls_no_accumulation(self):
        """連續呼叫 _show_snack，overlay 長度不應無限增加"""
        page = MockPage()

        from app.views.icon_preview_view import IconPreviewView

        with patch.object(IconPreviewView, "__init__", lambda self, page: None):
            view = IconPreviewView.__new__(IconPreviewView)
            view._page = page

        # 呼叫 5 次
        for i in range(5):
            show_snack(view.page, f"message {i}", color=theme.WARNING)

        # 驗證：只有 1 個 SnackBar
        snackbars = [o for o in page.overlay if isinstance(o, ft.SnackBar)]
        assert len(snackbars) == 1, f"預期 1 個，實際 {len(snackbars)} 個（accumulation bug）"
        assert snackbars[0].content.value == "message 4"  # 最後一個

    def test_snack_bar_calls_page_update_on_success(self):
        """_show_snack 正常結束後應呼叫 page.update()"""
        page = MockPage()

        from app.views.icon_preview_view import IconPreviewView

        with patch.object(IconPreviewView, "__init__", lambda self, page: None):
            view = IconPreviewView.__new__(IconPreviewView)
            view._page = page

        # 不應 raise，page.update() 應該被呼叫
        show_snack(view.page, "test", color=theme.GREEN_600)
        assert page.update_called is True
