"""Responsive dialog sizing follows page resize and releases its handler."""

from types import SimpleNamespace

import flet as ft

from app.ui.dialogs import close_page_dialog, dialog_dimensions, present_page_dialog
from app.views.pipeline.pipeline_forms import dialog_content, dialog_text_field


class _ResizablePage:
    def __init__(self):
        self.width = 1360
        self.height = 900
        self.overlay = []
        self.updates = 0
        self.resize_events = []
        self.on_resize = self.resize_events.append
        self.shown = []

    def update(self):
        self.updates += 1

    def show_dialog(self, dialog):
        dialog.open = True
        self.shown.append(dialog)

    def pop_dialog(self):  # pragma: no cover - closing must target the owned dialog
        raise AssertionError("dialog resize close must not pop an unrelated dialog")


def _responsive_dialog(page):
    field = dialog_text_field(page, label="測試路徑")
    dialog = ft.AlertDialog(
        content=dialog_content(page, ft.Column([field])), modal=True
    )
    return dialog, field


def test_open_dialog_resizes_body_and_responsive_fields_and_restores_handler():
    page = _ResizablePage()
    original_resize = page.on_resize
    dialog, field = _responsive_dialog(page)
    initial_size = dialog_dimensions(page)
    initial_field_width = field.width

    present_page_dialog(page, dialog)

    page.width = 390
    page.height = 844
    event = SimpleNamespace(width=390, height=844)
    page.on_resize(event)

    assert page.resize_events == [event]
    assert (dialog.content.width, dialog.content.height) == dialog_dimensions(page)
    assert (dialog.content.width, dialog.content.height) != initial_size
    assert field.width == max(216, dialog.content.width - 340)
    assert field.width != initial_field_width
    assert page.updates == 1

    assert close_page_dialog(page, dialog)
    assert page.on_resize is original_resize


def test_closing_one_of_multiple_responsive_dialogs_keeps_other_resize_binding():
    page = _ResizablePage()
    original_resize = page.on_resize
    first, _first_field = _responsive_dialog(page)
    second, second_field = _responsive_dialog(page)
    present_page_dialog(page, first)
    present_page_dialog(page, second)

    close_page_dialog(page, first)
    assert page.on_resize is not original_resize

    page.width = 768
    page.height = 700
    page.on_resize(SimpleNamespace(width=768, height=700))
    assert (second.content.width, second.content.height) == dialog_dimensions(page)
    assert second_field.width == max(216, second.content.width - 340)

    close_page_dialog(page, second)
    assert page.on_resize is original_resize


def test_external_dialog_dismiss_releases_responsive_resize_binding():
    page = _ResizablePage()
    original_resize = page.on_resize
    dialog, _field = _responsive_dialog(page)
    dismiss_events = []
    dialog.on_dismiss = dismiss_events.append
    present_page_dialog(page, dialog)

    dialog.open = False
    dialog.on_dismiss("dismissed")

    assert dismiss_events == ["dismissed"]
    assert page.on_resize is original_resize
