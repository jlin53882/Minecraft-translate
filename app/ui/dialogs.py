"""Owned Flet dialog presentation and targeted dismissal helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from weakref import WeakKeyDictionary

import flet as ft

RESPONSIVE_DIALOG_MARKER = "pipeline-responsive-dialog"
RESPONSIVE_FIELD_DATA_KEY = "pipeline-dialog-reserved-width"


def dialog_dimensions(page: ft.Page) -> tuple[int, int]:
    """Return a viewport-safe dialog body size with room for title and actions."""
    viewport_width = max(320, int(getattr(page, "width", 1280) or 1280))
    viewport_height = max(420, int(getattr(page, "height", 900) or 900))
    width = min(960, max(320, int(viewport_width * 0.6)), viewport_width - 32)
    height = min(680, max(200, viewport_height - 220))
    return width, height


@dataclass
class _ResponsiveDialogEntry:
    dialog: ft.DialogControl
    content: ft.Container
    original_dismiss: Callable | None
    wrapped_dismiss: Callable


@dataclass
class _ResponsivePageBinding:
    original_resize: Callable | None
    resize_handler: Callable
    entries: list[_ResponsiveDialogEntry]


_responsive_pages: WeakKeyDictionary[ft.Page, _ResponsivePageBinding] = (
    WeakKeyDictionary()
)


def _walk_controls(root):
    pending = [root]
    seen = set()
    while pending:
        control = pending.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        yield control
        for name in ("controls", "content", "actions", "title"):
            children = getattr(control, name, None)
            if isinstance(children, (list, tuple)):
                pending.extend(children)
            elif children is not None and not isinstance(children, str):
                pending.append(children)


def _resize_dialog_controls(page: ft.Page, dialog: ft.DialogControl) -> bool:
    content = getattr(dialog, "content", None)
    if not isinstance(content, ft.Container):
        return False
    width, height = dialog_dimensions(page)
    changed = content.width != width or content.height != height
    content.width = width
    content.height = height
    for control in _walk_controls(content):
        if not isinstance(control, ft.TextField):
            continue
        data = getattr(control, "data", None)
        if not isinstance(data, dict):
            continue
        reserved = data.get(RESPONSIVE_FIELD_DATA_KEY)
        if isinstance(reserved, int):
            field_width = max(216, width - reserved)
            if control.width != field_width:
                control.width = field_width
                changed = True
    return changed


def _register_responsive_dialog(page: ft.Page, dialog: ft.DialogControl) -> None:
    content = getattr(dialog, "content", None)
    if (
        not isinstance(content, ft.Container)
        or getattr(content, "data", None) != RESPONSIVE_DIALOG_MARKER
    ):
        return

    try:
        binding = _responsive_pages.get(page)
    except TypeError:
        # Lightweight test pages and older Page adapters may not support weak refs.
        return
    if binding is None:
        original_resize = getattr(page, "on_resize", None)
        entries: list[_ResponsiveDialogEntry] = []

        def on_resize(event):
            if callable(original_resize):
                original_resize(event)
            changed = False
            for entry in tuple(entries):
                if not getattr(entry.dialog, "open", False):
                    _unregister_responsive_dialog(page, entry.dialog)
                    continue
                changed = _resize_dialog_controls(page, entry.dialog) or changed
            if changed:
                page.update()

        binding = _ResponsivePageBinding(original_resize, on_resize, entries)
        _responsive_pages[page] = binding
        page.on_resize = on_resize

    if any(entry.dialog is dialog for entry in binding.entries):
        return

    original_dismiss = getattr(dialog, "on_dismiss", None)

    def on_dismiss(event):
        _unregister_responsive_dialog(page, dialog)
        if callable(original_dismiss):
            original_dismiss(event)

    binding.entries.append(
        _ResponsiveDialogEntry(dialog, content, original_dismiss, on_dismiss)
    )
    dialog.on_dismiss = on_dismiss


def _unregister_responsive_dialog(page: ft.Page, dialog: ft.DialogControl) -> None:
    try:
        binding = _responsive_pages.get(page)
    except TypeError:
        return
    if binding is None:
        return
    remaining = []
    for entry in binding.entries:
        if entry.dialog is dialog:
            if getattr(entry.dialog, "on_dismiss", None) is entry.wrapped_dismiss:
                entry.dialog.on_dismiss = entry.original_dismiss
        else:
            remaining.append(entry)
    binding.entries[:] = remaining
    if binding.entries:
        return
    if getattr(page, "on_resize", None) is binding.resize_handler:
        page.on_resize = binding.original_resize
    _responsive_pages.pop(page, None)


def present_page_dialog(page: ft.Page, dialog: ft.DialogControl) -> bool:
    """Present one native dialog, falling back to a mounted overlay on old Pages."""
    show_dialog = getattr(page, "show_dialog", None)
    if callable(show_dialog) and callable(getattr(page, "pop_dialog", None)):
        show_dialog(dialog)
        _register_responsive_dialog(page, dialog)
        return True
    page.overlay.append(dialog)
    dialog.open = True
    page.update()
    _register_responsive_dialog(page, dialog)
    return False


def close_page_dialog(page: ft.Page, dialog: ft.DialogControl) -> bool:
    """Close exactly ``dialog`` without popping an unrelated topmost modal.

    Flet's public ``pop_dialog()`` only closes whichever dialog is topmost and
    does not accept a target. For native dialogs, update the owned control's
    ``open`` state directly; Flet's public ``show_dialog()`` dismissal lifecycle
    then removes that exact stack entry after the client dismisses it. Legacy
    overlay dialogs are removed only after their closed state has been sent.
    """
    if not getattr(dialog, "open", False):
        _unregister_responsive_dialog(page, dialog)
        return False

    if dialog in getattr(page, "overlay", ()):
        dialog.open = False
        page.update()
        page.overlay.remove(dialog)
        page.update()
    else:
        dialog.open = False
        update = getattr(dialog, "update", None)
        if callable(update):
            try:
                update()
            except RuntimeError:
                page.update()
        else:
            page.update()
    _unregister_responsive_dialog(page, dialog)
    return True


def close_overlay_dialog(page: ft.Page, dialog: ft.DialogControl) -> bool:
    """Compatibility name for targeted native-or-overlay close behavior."""
    return close_page_dialog(page, dialog)


def set_dialog_feedback(page: ft.Page, control: ft.Text, message: str, color) -> None:
    """Show validation/action feedback inside its modal so it cannot hide behind it."""
    control.value = message
    control.visible = bool(message)
    control.color = color
    page.update()


def dispose_dialogs(ctx) -> None:
    """Close the dialogs owned by ``ctx`` without touching dialogs above them."""
    dialogs = tuple(getattr(ctx, "dialogs", ()))
    if not dialogs:
        return
    for dialog in reversed(dialogs):
        close_page_dialog(ctx.page, dialog)
    ctx.dialogs.clear()


def present_dialog(ctx, dialog: ft.DialogControl) -> None:
    """Present and record one dialog owned by a view/dialog context."""
    dialogs = getattr(ctx, "dialogs", None)
    if dialogs is None:
        ctx.dialogs = dialogs = []
    dialogs.append(dialog)
    ctx.uses_dialog_api = present_page_dialog(ctx.page, dialog)
