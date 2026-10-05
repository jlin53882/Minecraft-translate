"""PR1-6 UI 优化功能测试。"""

import flet as ft


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
    assert "view_registry" in params


# PR2: Quick Jump Panel（已由 app.shell.palette 的 CommandPalette 取代）


def test_quick_jump_import():
    """Verify CommandPalette can be imported."""
    from app.shell.palette import CommandPalette

    assert CommandPalette is not None


def test_quick_jump_class():
    """Verify CommandPalette is a class."""
    from app.shell.palette import CommandPalette

    assert isinstance(CommandPalette, type)


# PR3: styled_card collapsible - Behavior Tests


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
        content=ft.Row(
            [
                ft.Text("Loading..."),
                pb,
            ],
            spacing=10,
        ),
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
        content=ft.Row(
            [
                ft.Text("Loading..."),
                pb,
            ]
        ),
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


# Theme tests
