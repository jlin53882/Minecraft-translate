"""Editable target-version dropdown shared by Mod database consumers."""

from __future__ import annotations

import logging

import flet as ft

from app.services_impl.moddb_service import open_database, version_choices
from app.ui import kit

logger = logging.getLogger(__name__)
MERGE_INHERIT_VERSION = "__inherit_global__"


def target_version_choices() -> list[str]:
    """Return known database/resource-pack versions without creating a database."""
    db = None
    try:
        db = open_database(create=False)
        return version_choices(db)
    except Exception:
        logger.warning("讀取 Mod 資料庫版本建議失敗", exc_info=True)
        try:
            return version_choices()
        except Exception:
            logger.warning("讀取資源包版本建議失敗", exc_info=True)
            return []
    finally:
        if db is not None:
            db.close()


def merge_target_version_choices() -> list[str]:
    """Return only versions present in the existing database; never create/fallback."""
    db = None
    try:
        db = open_database(create=False)
        return list(db.versions()) if db is not None else []
    except Exception:
        logger.warning("讀取語系合併資料庫版本失敗", exc_info=True)
        return []
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.warning("關閉語系合併版本查詢資料庫失敗", exc_info=True)


def merge_target_version_dropdown(
    *,
    global_version: str = "",
    value: str | None = None,
    on_select=None,
    on_focus=None,
) -> ft.Dropdown:
    """Build the non-editable, database-only version selector for Merge DB."""
    control = kit.dropdown(
        label="目標版本",
        hint_text="選擇沿用全域設定或指定資料庫版本",
        editable=False,
        enable_filter=True,
        options=[],
        on_select=on_select,
        on_focus=on_focus,
    )
    refresh_merge_target_version_options(control, global_version, value)
    return control


def refresh_merge_target_version_options(
    control: ft.Dropdown,
    global_version: str,
    value: str | None = None,
    choices: list[str] | None = None,
) -> None:
    """Refresh Merge DB options without ever adding a stale/manual value."""
    versions = merge_target_version_choices() if choices is None else list(choices)
    global_version = str(global_version or "").strip()
    inherited_label = f"沿用全域設定（目前：{global_version or '未指定'}）"
    kit.set_dropdown_options(
        control,
        [(MERGE_INHERIT_VERSION, inherited_label)]
        + [(version, version) for version in versions],
    )
    selected = value if value in versions else MERGE_INHERIT_VERSION
    control.value = selected
    control.text = selected


def target_version_dropdown(
    *,
    value: str | None = None,
    label: str = "目標版本",
    hint: str = "選擇版本或手動輸入",
    on_change=None,
    on_focus=None,
) -> ft.Dropdown:
    """Build a searchable dropdown that also accepts arbitrary version text."""
    manual_option: str | None = None

    def on_text_change(event) -> None:
        # Flet clears editable text in the browser when a selected key is set to
        # None during the same text event. Keep the typed value as a valid option
        # while replacing the previous ad-hoc option, so it survives blur/refocus.
        nonlocal manual_option
        text = event.control.text or ""
        if text != event.control.value:
            options = [
                (option.key, option.text)
                for option in event.control.options
                if option.key != manual_option
            ]
            known = {key for key, _label in options}
            if text and text not in known:
                options.append((text, text))
                manual_option = text
            else:
                manual_option = None
            kit.set_dropdown_options(event.control, options)
            event.control.value = text or None
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
    choices = target_version_choices()
    if current and current not in choices:
        choices.append(current)
    kit.set_dropdown_options(control, [(version, version) for version in choices])
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
