from copy import deepcopy

import flet as ft
import pytest

from app.ui.snack import show_snack
from app.views.config_view import ConfigView
from tests.conftest import mock_page


class LifoDialogPage:
    """Page double that models Flet's active DialogControl stack."""

    def __init__(self):
        self._page = mock_page()
        self.overlay = self._page.overlay
        self.dialog_stack = []
        self.popped_dialogs = []

    def __getattr__(self, name):
        return getattr(self._page, name)

    def show_dialog(self, dialog):
        self.dialog_stack.append(dialog)
        self.overlay.append(dialog)
        dialog.open = True

    def pop_dialog(self):
        if not self.dialog_stack:
            return None
        dialog = self.dialog_stack.pop()
        dialog.open = False
        self.popped_dialogs.append(dialog)
        if dialog in self.overlay:
            self.overlay.remove(dialog)
        on_dismiss = getattr(dialog, "on_dismiss", None)
        if callable(on_dismiss):
            on_dismiss(None)
        return dialog


def _config_for_dialog_save():
    return {
        "logging": {},
        "translator": {},
        "ftb_translator": {},
        "species_cache": {},
        "lm_translator": {"models": {"test-model": {"enabled": True}}},
        "output_bundler": {},
        "lang_merger": {},
        "extractor": {"output_folder_names": {}},
    }


def test_config_save_closes_its_dialog_before_showing_success_snackbar(monkeypatch):
    config = _config_for_dialog_save()
    monkeypatch.setattr(
        "app.views.config_view.load_config_json", lambda: deepcopy(config)
    )
    monkeypatch.setattr("app.views.config_view.save_config_json", lambda _cfg: True)
    monkeypatch.setattr(
        "app.views.config_view.validate_api_keys_from_ui", lambda _keys: None
    )
    monkeypatch.setattr(
        "app.views.config.config_actions.validate_config_values", lambda _cfg: None
    )

    page = LifoDialogPage()
    lower_dialog = ft.AlertDialog(title=ft.Text("Existing dialog"))
    page.show_dialog(lower_dialog)
    lower_snack = show_snack(page, "Existing notification")
    view = ConfigView(page)
    field = view.controls_map["lm_translator.temperature"]
    field.value = "0.9"
    view._on_form_changed()
    view._on_nav_click("prompts")
    config_dialog = page.dialog_stack[-1]

    view._unsaved_dialog.actions[2].on_click(None)

    assert page.popped_dialogs == [config_dialog]
    assert config_dialog.open is False
    assert lower_dialog in page.dialog_stack
    assert lower_snack in page.dialog_stack
    assert view._selected_nav == "prompts"
    assert isinstance(page.dialog_stack[-1], ft.SnackBar)
    assert page.dialog_stack[-1].content.value == "✅ 設定已成功儲存！"
    view._unsaved_dialog.actions[2].on_click(None)
    assert page.popped_dialogs == [config_dialog]


@pytest.mark.parametrize("failure", ["validation", "write"])
@pytest.mark.parametrize("resolution", ["stay", "discard"])
def test_config_save_failure_stays_inside_its_dialog_stack(
    monkeypatch, failure, resolution
):
    config = _config_for_dialog_save()
    monkeypatch.setattr(
        "app.views.config_view.load_config_json", lambda: deepcopy(config)
    )
    monkeypatch.setattr(
        "app.views.config_view.validate_api_keys_from_ui",
        (lambda _keys: (_ for _ in ()).throw(ValueError("invalid")))
        if failure == "validation"
        else lambda _keys: None,
    )
    monkeypatch.setattr(
        "app.views.config_view.save_config_json",
        lambda _cfg: failure != "write",
    )
    monkeypatch.setattr(
        "app.views.config.config_actions.validate_config_values", lambda _cfg: None
    )

    page = LifoDialogPage()
    lower_dialog = ft.AlertDialog(title=ft.Text("Other dialog"))
    page.show_dialog(lower_dialog)
    lower_snack = show_snack(page, "Unrelated notification")
    view = ConfigView(page)
    view.controls_map["lm_translator.temperature"].value = "0.9"
    view._on_form_changed()
    navigations = []
    view.confirm_unsaved_changes(lambda: navigations.append("continued"))
    config_dialog = view._unsaved_dialog
    stack_before_action = list(page.dialog_stack)

    config_dialog.actions[2].on_click(None)

    assert page.dialog_stack == stack_before_action
    assert page.dialog_stack[-1] is config_dialog
    assert config_dialog.open is True
    assert view.has_unsaved_changes is True
    assert navigations == []
    assert not any(
        isinstance(dialog, ft.SnackBar) and dialog not in (lower_snack,)
        for dialog in page.dialog_stack
    )
    assert (
        "失敗" in config_dialog.content.value or "儲存" in config_dialog.content.value
    )

    action = 0 if resolution == "stay" else 1
    config_dialog.actions[action].on_click(None)

    assert page.popped_dialogs == [config_dialog]
    assert lower_dialog in page.dialog_stack
    assert lower_snack in page.dialog_stack
    assert navigations == ([] if resolution == "stay" else ["continued"])


def test_unsaved_save_reload_recovery_failure_then_retry_keeps_dialog_topmost(
    monkeypatch,
):
    config = _config_for_dialog_save()
    load_calls = 0

    def load_config():
        nonlocal load_calls
        load_calls += 1
        # Initial load and save collection succeed; the first post-write reload
        # and first recovery retry fail, then the next retry succeeds.
        if load_calls in (3, 4):
            raise OSError("temporary reload failure")
        return deepcopy(config)

    monkeypatch.setattr("app.views.config_view.load_config_json", load_config)
    monkeypatch.setattr("app.views.config_view.save_config_json", lambda _cfg: True)
    monkeypatch.setattr(
        "app.views.config_view.validate_api_keys_from_ui", lambda _keys: None
    )
    monkeypatch.setattr(
        "app.views.config.config_actions.validate_config_values", lambda _cfg: None
    )

    page = LifoDialogPage()
    lower_dialog = ft.AlertDialog(title=ft.Text("Other dialog"))
    page.show_dialog(lower_dialog)
    lower_snack = show_snack(page, "Still open below")
    view = ConfigView(page)
    view.controls_map["lm_translator.temperature"].value = "0.9"
    view._on_form_changed()
    view._on_nav_click("prompts")
    config_dialog = view._unsaved_dialog

    config_dialog.actions[2].on_click(None)
    assert view._reload_recovery_required is True
    assert config_dialog.open is True
    assert view._selected_nav == "general"
    assert page.dialog_stack[-1] is config_dialog

    config_dialog.actions[1].on_click(None)
    assert config_dialog.open is True
    assert page.dialog_stack[-1] is config_dialog
    assert page.popped_dialogs == []
    assert view._selected_nav == "general"

    config_dialog.actions[1].on_click(None)
    assert page.popped_dialogs == [config_dialog]
    assert lower_dialog in page.dialog_stack
    assert lower_snack in page.dialog_stack
    assert view._selected_nav == "prompts"
    assert isinstance(page.dialog_stack[-1], ft.SnackBar)
    assert (
        page.dialog_stack[-1].content.value == "✅ 設定已重新載入，畫面與設定檔已同步。"
    )


def test_external_unsaved_dialog_dismiss_cannot_run_navigation_callback(monkeypatch):
    monkeypatch.setattr(
        "app.views.config_view.load_config_json", lambda: _config_for_dialog_save()
    )
    page = LifoDialogPage()
    view = ConfigView(page)
    view.controls_map["lm_translator.temperature"].value = "0.9"
    view._on_form_changed()
    navigations = []
    view.confirm_unsaved_changes(lambda: navigations.append("continued"))
    dialog = view._unsaved_dialog

    assert page.pop_dialog() is dialog
    dialog.actions[2].on_click(None)

    assert view._unsaved_dialog_resolved is True
    assert view._unsaved_dialog_open is False
    assert navigations == []
