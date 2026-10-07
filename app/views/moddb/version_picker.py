"""Editable target-version dropdown shared by Mod database consumers."""

from __future__ import annotations

import logging

import flet as ft

from app.services_impl.moddb_service import open_database, version_choices
from app.ui import kit

logger = logging.getLogger(__name__)


def target_version_choices() -> list[str]:
    """Return known database/resource-pack versions without creating a database."""
    db = None
    try:
        db = open_database(create=False)
        return version_choices(db)
    except Exception:
        logger.warning("讀取 Mod 資料庫版本建議失敗", exc_info=True)
        return version_choices()
    finally:
        if db is not None:
            db.close()


def target_version_dropdown(
    *,
    value: str | None = None,
    label: str = "目標版本",
    hint: str = "選擇版本或手動輸入",
    on_change=None,
    on_focus=None,
) -> ft.Dropdown:
    """Build a searchable dropdown that also accepts arbitrary version text."""

    def on_text_change(event) -> None:
        # Editable Dropdown keeps `value` (the selected key) separate from `text`.
        # Clear a stale selected key as soon as the user types a different version.
        if event.control.value and event.control.text != event.control.value:
            event.control.value = None
        if on_change is not None:
            on_change(event)

    control = kit.dropdown(
        label=label,
        hint_text=hint,
        editable=True,
        enable_filter=True,
        options=[],
        on_select=on_change,
        on_text_change=on_text_change,
        on_focus=on_focus,
    )
    refresh_target_version_options(control)
    set_target_version(control, value)
    return control


def refresh_target_version_options(control: ft.Dropdown) -> None:
    """Refresh suggestions while retaining the current manual or selected value."""
    current = target_version_value(control)
    kit.set_dropdown_options(
        control, [(version, version) for version in target_version_choices()]
    )
    set_target_version(control, current)


def target_version_value(control: ft.Dropdown) -> str | None:
    """Read either the editable text or selected option as the target version."""
    selected = getattr(control, "value", None)
    if selected:
        return str(selected).strip() or None
    text = getattr(control, "text", None)
    return str(text or "").strip() or None


def set_target_version(control: ft.Dropdown, value: str | None) -> None:
    """Set both the visible input and selected key when it is a known option."""
    text = str(value or "").strip()
    known = {option.key for option in control.options}
    control.value = text if text in known else None
    control.text = text
