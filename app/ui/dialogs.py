"""Owned Flet dialog presentation and targeted dismissal helpers."""

from __future__ import annotations

import flet as ft


def present_page_dialog(page: ft.Page, dialog: ft.DialogControl) -> bool:
    """Present one native dialog, falling back to a mounted overlay on old Pages."""
    show_dialog = getattr(page, "show_dialog", None)
    if callable(show_dialog) and callable(getattr(page, "pop_dialog", None)):
        show_dialog(dialog)
        return True
    page.overlay.append(dialog)
    dialog.open = True
    page.update()
    return False


def close_page_dialog(page: ft.Page, dialog: ft.DialogControl) -> bool:
    """Close exactly ``dialog`` without popping an unrelated topmost modal.

    Flet's public ``pop_dialog()`` only closes whichever dialog is topmost. A
    targeted ``open=False`` update lets Flet's ``show_dialog()`` dismissal hook
    retire this specific stack entry; legacy overlay dialogs are removed only
    after their closed state has been sent to the client.
    """
    if not getattr(dialog, "open", False):
        return False

    if dialog in getattr(page, "overlay", ()):
        dialog.open = False
        page.update()
        page.overlay.remove(dialog)
        page.update()
    else:
        # Flet's public pop_dialog() closes only the top item. Inspect the
        # framework-owned stack first so an unrelated modal is never popped.
        native_stack = getattr(page, "_dialogs", None)
        controls = getattr(native_stack, "controls", None)
        if controls is not None and dialog in controls:
            top_open = next((item for item in reversed(controls) if item.open), None)
            if top_open is dialog:
                popped = page.pop_dialog()
                if popped is dialog:
                    return True
            # A child/foreign modal can be above this control. Close only the
            # owned target and leave it mounted through its dismiss animation;
            # Flet's wrapped on_dismiss callback then removes this exact entry.
            dialog.open = False
            dialog.update()
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
