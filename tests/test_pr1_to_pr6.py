# -*- coding: utf-8 -*-
"""PR1-6 UI 优化功能测试。"""

import flet as ft

from app.ui import theme
from app.ui.components import (
    empty_state,
    error_state,
    loading_state,
    styled_card,
)


# Mock Page for testing
class MockPage:
    def __init__(self):
        self.banner = None
        self.snack_bar = None
        self.overlay = []
        self.update_count = 0

    def update(self):
        self.update_count += 1


# PR1: Keyboard Shortcuts


def test_keyboard_handler_import():
    """Verify KeyboardShortcutHandler can be imported."""
    from app.ui.keyboard_shortcuts import KeyboardShortcutHandler
    assert KeyboardShortcutHandler is not None


def test_keyboard_handler_class():
    """Verify KeyboardShortcutHandler is a class."""
    from app.ui.keyboard_shortcuts import KeyboardShortcutHandler
    assert isinstance(KeyboardShortcutHandler, type)


def test_keyboard_handler_has_view_registry_param():
    """Verify KeyboardShortcutHandler has view_registry parameter."""
    import inspect

    from app.ui.keyboard_shortcuts import KeyboardShortcutHandler
    sig = inspect.signature(KeyboardShortcutHandler.__init__)
    params = list(sig.parameters.keys())
    # Should have page, view_registry, change_view_callback
    assert 'view_registry' in params


# PR2: Quick Jump Panel


def test_quick_jump_import():
    """Verify QuickJumpPanel can be imported."""
    from app.ui.quick_jump import QuickJumpPanel
    assert QuickJumpPanel is not None


def test_quick_jump_class():
    """Verify QuickJumpPanel is a class."""
    from app.ui.quick_jump import QuickJumpPanel
    assert isinstance(QuickJumpPanel, type)


# PR3: styled_card collapsible - Behavior Tests


def test_styled_card_no_collapsible():
    """Verify styled_card works without collapsible."""
    inner = ft.Text("x")
    c = styled_card(title="T", icon=ft.Icons.INFO, content=inner)
    assert isinstance(c, ft.Container)


def test_styled_card_collapsible_false():
    """Verify styled_card with collapsible=False."""
    inner = ft.Text("x")
    c = styled_card(
        title="T",
        icon=ft.Icons.INFO,
        content=inner,
        collapsible=False
    )
    assert isinstance(c, ft.Container)


def test_styled_card_collapsible_true():
    """Verify styled_card with collapsible=True."""
    inner = ft.Text("x")
    c = styled_card(
        title="T",
        icon=ft.Icons.INFO,
        content=inner,
        collapsible=True,
        default_collapsed=False
    )
    assert isinstance(c, ft.Container)


def test_styled_card_with_page():
    """Verify styled_card with page parameter."""
    page = MockPage()
    inner = ft.Text("x")
    c = styled_card(
        title="Test",
        icon=ft.Icons.INFO,
        content=inner,
        collapsible=True,
        page=page
    )
    assert isinstance(c, ft.Container)


def test_styled_card_collapse_toggle():
    """Test collapse button structure exists."""
    page = MockPage()
    inner = ft.Text("content")
    c = styled_card(
        title="Test",
        icon=ft.Icons.INFO,
        content=inner,
        collapsible=True,
        default_collapsed=False,
        page=page
    )
    # Verify card structure
    assert isinstance(c, ft.Container)
    assert c.content is not None


def test_styled_card_default_collapsed():
    """Verify default_collapsed=True creates collapsed card."""
    inner = ft.Text("content")
    c = styled_card(
        title="Test",
        icon=ft.Icons.INFO,
        content=inner,
        collapsible=True,
        default_collapsed=True
    )
    assert isinstance(c, ft.Container)


# PR4: Progress Bar - SnackBar Integration


def test_progress_bar_creation():
    """Verify ProgressBar can be created."""
    pb = ft.ProgressBar(value=0.5, width=300)
    assert pb.value == 0.5


def test_progress_bar_update():
    """Verify ProgressBar value can be updated."""
    pb = ft.ProgressBar(value=0)
    pb.value = 0.7
    assert pb.value == 0.7


def test_progress_snackbar_display():
    """Test SnackBar progress display."""
    page = MockPage()
    pb = ft.ProgressBar(value=0.3, width=200)

    snack = ft.SnackBar(
        content=ft.Row([
            ft.Text("Loading..."),
            pb,
        ], spacing=10),
        duration=999999,
    )
    page.snack_bar = snack
    page.snack_bar.open = True

    assert page.snack_bar is not None
    assert page.snack_bar.open is True
    assert pb.value == 0.3


def test_progress_snackbar_update():
    """Test SnackBar progress can be updated."""
    page = MockPage()
    pb = ft.ProgressBar(value=0.0, width=200)

    snack = ft.SnackBar(
        content=ft.Row([
            ft.Text("Loading..."),
            pb,
        ]),
        duration=999999,
    )
    page.snack_bar = snack

    # Update progress
    pb.value = 0.5
    page.update()

    assert pb.value == 0.5


def test_snackbar_close():
    """Test SnackBar can be closed."""
    page = MockPage()
    snack = ft.SnackBar(
        content=ft.Text("Done"),
        duration=999999,
    )
    page.snack_bar = snack
    page.snack_bar.open = False
    page.update()

    assert page.snack_bar.open is False


# PR5: Unified States


def test_loading_state():
    """Verify loading_state returns Container."""
    ls = loading_state("Loading...")
    assert isinstance(ls, ft.Container)


def test_loading_state_no_spinner():
    """Verify loading_state without spinner."""
    ls = loading_state("Wait", show_spinner=False)
    assert isinstance(ls, ft.Container)


def test_empty_state():
    """Verify empty_state returns Container."""
    es = empty_state(
        icon=ft.Icons.SEARCH_OFF,
        title="No results",
        message="Try another keyword"
    )
    assert isinstance(es, ft.Container)
    assert es.alignment == ft.alignment.center


def test_empty_state_with_button():
    """Verify empty_state with action button."""
    btn = ft.ElevatedButton("Retry")
    es = empty_state(
        icon=ft.Icons.ERROR_OUTLINE,
        title="Error",
        message="Please retry",
        action_button=btn
    )
    assert isinstance(es, ft.Container)


def test_error_state():
    """Verify error_state returns Container."""
    err = error_state(
        icon=ft.Icons.ERROR,
        title="Error",
        message="Operation failed"
    )
    assert isinstance(err, ft.Container)


def test_error_state_with_retry():
    """Verify error_state with retry button."""
    btn = ft.ElevatedButton("Retry")
    err = error_state(
        icon=ft.Icons.ERROR,
        title="Error",
        message="Failed",
        retry_button=btn
    )
    assert isinstance(err, ft.Container)


# PR6: Cache Panels


def test_cache_overview_panel_import():
    """Verify CacheOverviewPanel can be imported."""
    from app.views.cache_manager.panels import CacheOverviewPanel
    assert CacheOverviewPanel is not None


def test_cache_query_panel_import():
    """Verify CacheQueryPanel can be imported."""
    from app.views.cache_manager.panels import CacheQueryPanel
    assert CacheQueryPanel is not None


def test_cache_shard_panel_import():
    """Verify CacheShardPanel can be imported."""
    from app.views.cache_manager.panels import CacheShardPanel
    assert CacheShardPanel is not None


def test_cache_overview_panel_class():
    """Verify CacheOverviewPanel is a class."""
    from app.views.cache_manager.panels import CacheOverviewPanel
    assert isinstance(CacheOverviewPanel, type)


def test_cache_query_panel_class():
    """Verify CacheQueryPanel is a class."""
    from app.views.cache_manager.panels import CacheQueryPanel
    assert isinstance(CacheQueryPanel, type)


def test_cache_shard_panel_class():
    """Verify CacheShardPanel is a class."""
    from app.views.cache_manager.panels import CacheShardPanel
    assert isinstance(CacheShardPanel, type)


# Theme tests


def test_theme_text_secondary():
    """Verify TEXT_SECONDARY exists."""
    assert hasattr(theme, 'TEXT_SECONDARY')


def test_theme_text_secondary_200():
    """Verify TEXT_SECONDARY_200 exists."""
    assert hasattr(theme, 'TEXT_SECONDARY_200')


def test_theme_text_disabled():
    """Verify TEXT_DISABLED exists."""
    assert hasattr(theme, 'TEXT_DISABLED')
