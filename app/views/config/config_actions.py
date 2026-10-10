from __future__ import annotations

import logging
import traceback
from enum import Enum, auto

from app.services_impl.logging_service import validate_log_format
from app.services_impl.moddb_source_service import (
    normalize_priority_config,
    priority_custom_source_lines,
    priority_database_identity,
    priority_display_lines,
    priority_has_custom_sources,
)
from app.ui import kit
from app.ui.snack import show_snack
from app.views.config.settings_schema import (
    Setting,
    editable_settings,
    get_path,
    set_path,
)
from translation_tool.utils.config_manager import (
    ConfigValidationError,
    get_default,
    validate_config_values,
    validate_output_folder_names,
)
from translation_tool.utils.config_schema import LEGACY_API_KEY_PLACEHOLDERS
from translation_tool.utils.redaction import redact_text

logger = logging.getLogger(__name__)


class SaveOutcome(Enum):
    WRITE_FAILED = auto()
    SAVED_RELOAD_FAILED = auto()
    SAVED_OK = auto()


# 設定值 ↔ 設定頁控制項的轉換，全部由 settings_schema 驅動（#134）。
# 新增一般設定不需要改這個檔案；只有專用元件（API 金鑰列、模型列）在下方手寫。


def _initial_value(config: dict, setting: Setting):
    value = get_path(config, setting.path)
    return get_default(setting.path) if value is None else value


def _to_control_value(setting: Setting, value):
    kind = setting.kind
    if kind == "bool":
        return bool(value)
    if kind == "lines":
        return "\n".join(str(item) for item in (value or []))
    if kind == "choice":
        return value
    return "" if value is None else str(value)


def _from_control_value(setting: Setting, raw):
    """控制項的值 → 要寫入 config 的值；格式錯誤丟出 ValueError（由儲存流程回報）。"""
    kind = setting.kind
    if kind == "bool":
        return bool(raw)
    if kind == "lines":
        return [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    if kind in ("int", "float"):
        convert = int if kind == "int" else float
        text = raw.strip() if isinstance(raw, str) else raw
        if text is None or text == "":
            if setting.blank == "zero":
                value = convert(0)
            elif setting.blank == "default":
                value = get_default(setting.path)
            else:
                raise ValueError(f"「{setting.label}」不可空白")
        else:
            value = convert(text)  # 字串或已是數字的值
        if setting.minimum is not None:
            value = max(convert(setting.minimum), value)
        return value
    value = "" if raw is None else raw
    if setting.validator == "log_format":
        value = validate_log_format(value)
    return value


def _apply_label_templates(view, config: dict) -> None:
    for setting in editable_settings():
        if not setting.label_template or setting.path not in view.controls_map:
            continue
        values = {name: get_path(config, path) for name, path in setting.label_refs}
        values["value"] = get_path(config, setting.path)
        view.controls_map[setting.path].label = setting.label_template.format(**values)


def _models_from_view(view) -> dict:
    """Collect the per-model settings from their editor rows."""
    models = {}
    for row in view.models_column.controls:
        checkbox = row._checkbox
        model_cfg = {"enabled": bool(checkbox.value)}
        cap_field = getattr(row, "_max_output_tokens", None)
        raw_cap = getattr(cap_field, "value", "") if cap_field is not None else ""
        if raw_cap not in (None, ""):
            model_cfg["max_output_tokens"] = int(raw_cap)
        model_name = getattr(row, "_model_name", checkbox.label)
        models[model_name] = model_cfg
    return models


def _has_enabled_model(models) -> bool:
    return isinstance(models, dict) and any(
        isinstance(model, dict) and model.get("enabled", False)
        for model in models.values()
    )


def _model_settings_signature(models) -> tuple:
    if not isinstance(models, dict):
        return ()
    return tuple(
        (
            name,
            bool(cfg.get("enabled", False)) if isinstance(cfg, dict) else False,
            cfg.get("max_output_tokens") if isinstance(cfg, dict) else None,
        )
        for name, cfg in models.items()
    )


def _validate_provider_settings(
    view, provider, lm_config, api_keys, validate_api_keys_fn, *, show_feedback
):
    if provider == "gemini":
        validate_api_keys_fn(api_keys)
    elif (
        provider == "chatgpt" and not str(lm_config.get("chatgpt_model") or "").strip()
    ):
        if show_feedback:
            show_snack(view.page, "請先登入 ChatGPT 並選擇模型；設定尚未儲存。")
        return False
    return True


def _bind_chatgpt_model_catalog(config, view, provider):
    if provider != "chatgpt":
        return
    profile_id = getattr(view, "_chatgpt_model_catalog_profile_id", None)
    if profile_id and getattr(view, "_chatgpt_model_catalog_valid", True):
        config["lm_translator"]["chatgpt_model_profile_id"] = str(profile_id)


def refresh_chatgpt_model_controls_after_reload(view):
    """Keep valid authenticated model options and refresh their panel state."""
    selected = str(view.chatgpt_model_control.value or "").strip()
    available = {
        str(option.key)
        for option in (view.chatgpt_model_control.options or [])
        if getattr(option, "key", None)
    }
    if not view._chatgpt_model_catalog_valid or selected not in available:
        view._sync_chatgpt_model_options()
    view._refresh_provider_panel_visibility()


def sync_chatgpt_model_options(view, models=None, profile_id=None) -> None:
    """Update the ChatGPT model choices without losing the configured selection."""
    current = str(view.chatgpt_model_control.value or "")
    selected = current
    if models is None:
        view._chatgpt_model_catalog_valid = False
        view._chatgpt_model_catalog_profile_id = None
        pairs = (
            [(selected, f"{selected}（尚未驗證；請更新模型清單）")]
            if selected
            else [("", "登入後更新模型清單")]
        )
    else:
        pairs = [(model.slug, model.display_name) for model in models]
        if not pairs:
            pairs = [("", "此帳號目前沒有可用模型")]
        model_ids = {slug for slug, _label in pairs}
        if selected not in model_ids or not selected:
            selected = pairs[0][0] if pairs else ""
        view._chatgpt_model_catalog_valid = bool(selected and selected in model_ids)
        view._chatgpt_model_catalog_profile_id = (
            str(profile_id)
            if view._chatgpt_model_catalog_valid and profile_id
            else None
        )
    if selected != current:
        view._store_chatgpt_model_settings(view._chatgpt_active_model)
        view.chatgpt_model_control.value = selected
    kit.set_dropdown_options(view.chatgpt_model_control, pairs)
    view.chatgpt_model_settings_panel.visible = view.controls_map[
        "lm_translator.provider"
    ].value == "chatgpt" and bool(selected)
    if selected != view._chatgpt_active_model:
        view._load_chatgpt_model_settings_for(selected)
    if not view._loading_config:
        view._refresh_dirty_state()


def invalidate_chatgpt_model_options(view, message: str) -> None:
    """Mark the saved model unverified while keeping it visible in the dropdown."""
    view._chatgpt_model_catalog_valid = False
    selected = str(view.chatgpt_model_control.value or "").strip()
    options = (
        [(selected, f"{selected}（{message or '尚未驗證'}）")]
        if selected
        else [("", message or "請重新載入目前帳號的模型")]
    )
    kit.set_dropdown_options(view.chatgpt_model_control, options)
    view.chatgpt_model_settings_panel.visible = False


def _collect_validated_config(
    view, load_config_json_fn, validate_api_keys_fn, *, show_feedback=True
):
    try:
        config = load_config_json_fn()
        for setting in editable_settings():
            control = view.controls_map.get(setting.path)
            if control is not None:
                set_path(
                    config, setting.path, _from_control_value(setting, control.value)
                )
        if _priority_source_path_change_needs_review(view, config):
            if show_feedback:
                show_snack(
                    view.page,
                    "資料庫路徑已變更，請先檢查「來源優先順序」中的自訂來源，再儲存。",
                )
            return None
        # Database-local source identities are normalized in the service layer.
        normalize_priority_config(config)
        api_keys = [
            field.value.strip()
            for field in view.key_fields
            if field.value
            and field.value.strip()
            and field.value.strip() not in LEGACY_API_KEY_PLACEHOLDERS
        ]
        lm_config = config.get("lm_translator", {})
        provider = lm_config.get("provider", "gemini")
        if not _validate_provider_settings(
            view,
            provider,
            lm_config,
            api_keys,
            validate_api_keys_fn,
            show_feedback=show_feedback,
        ):
            return None
        config["lm_translator"]["keys"] = api_keys
        models = _models_from_view(view)
        previous_models = config["lm_translator"].get("models")
        if previous_models is None and "models" not in config["lm_translator"]:
            previous_models = {
                name: {"enabled": enabled}
                for name, enabled in getattr(view, "DEFAULT_MODELS", {}).items()
            }
        previous_has_enabled = _has_enabled_model(previous_models)
        models_changed = _model_settings_signature(models) != _model_settings_signature(
            previous_models
        )
        if (
            provider == "gemini"
            and not _has_enabled_model(models)
            and (previous_has_enabled or models_changed)
        ):
            if show_feedback:
                show_snack(view.page, "至少需要保留一個啟用中的模型；設定尚未儲存。")
            return None
        config["lm_translator"]["models"] = models
        _bind_chatgpt_model_catalog(config, view, provider)
        collect_chatgpt_settings = getattr(view, "collect_chatgpt_model_settings", None)
        if callable(collect_chatgpt_settings):
            try:
                config["lm_translator"]["chatgpt_model_settings"] = (
                    collect_chatgpt_settings()
                )
            except ValueError as err:
                if show_feedback:
                    show_snack(view.page, f"❌ {err}；設定尚未儲存。")
                return None
        try:
            validate_output_folder_names(config)
        except ConfigValidationError as err:
            logger.warning("提取輸出資料夾名稱設定無效：%s", err)
            if show_feedback:
                show_snack(view.page, f"❌ {err}；設定尚未儲存。")
            return None
        validate_config_values(config)
    except Exception as err:  # noqa: BLE001 - config collection is a UI save boundary
        logger.error("儲存設定驗證失敗：%s", redact_text(traceback.format_exc()))
        if show_feedback:
            show_snack(
                view.page,
                f"❌ 設定驗證失敗（{type(err).__name__}），尚未嘗試寫入。",
            )
        return None
    return config


def _priority_source_path_change_needs_review(view, config: dict) -> bool:
    """Require explicit review before carrying custom IDs to another database."""
    baseline = getattr(view, "_priority_source_baseline", None)
    priority_control = view.controls_map.get("translation_db.priority")
    path_control = view.controls_map.get("translation_db.path")
    if baseline is None or priority_control is None or path_control is None:
        return False
    if not baseline["has_custom"]:
        return False
    current_path = priority_database_identity(
        config.get("translation_db", {}).get("path")
    )
    if current_path == baseline["path"]:
        return False
    return bool(
        priority_custom_source_lines(
            (priority_control.value or "").splitlines(), baseline["path"]
        )
    )


def _write_config_with_feedback(
    view, config, save_config_json_fn, *, show_feedback=True
) -> bool:
    try:
        write_result = save_config_json_fn(config)
    except Exception:  # noqa: BLE001 - failure may occur before or after atomic replace
        logger.error("儲存設定結果未確認：%s", redact_text(traceback.format_exc()))
        write_result = None
    if write_result is not True:
        logger.error("儲存設定未確認成功：writer 未回報 True")
        if show_feedback:
            show_snack(
                view.page,
                "❌ 無法確認設定檔是否已更新；變更仍保留在此頁，請先檢查 config.json。",
            )
        return False
    return True


def _reload_after_confirmed_write(view, *, show_feedback=True) -> bool:
    try:
        view.load_config()
    except Exception:  # noqa: BLE001 - reload is a UI boundary after confirmed persistence
        logger.error(
            "設定已寫入，但重新載入設定頁失敗：%s",
            redact_text(traceback.format_exc()),
        )
        if show_feedback:
            show_snack(
                view.page,
                "⚠️ 設定已寫入，但畫面重新載入失敗；請重新開啟設定頁確認顯示內容。",
            )
        return False
    return True


def _refresh_registered_extractor_views(registry) -> None:
    from app.view_registry import built_view

    for item in registry:
        view_obj = built_view(item)
        if item["key"] != "extractor" or view_obj is None:
            continue
        content = getattr(view_obj, "content", None)
        refresh_config_defaults = getattr(content, "refresh_config_defaults", None)
        if callable(refresh_config_defaults):
            refresh_config_defaults()
        elif hasattr(content, "refresh_output_dir_helper"):
            content.refresh_output_dir_helper()


def load_config_into_view(view, config: dict):
    """
    將 config 字典中的值填入 view 的各個 UI 控制項。

    注意：傳入的 `config` 已經是 load_config() 三層合併後的結果。
    三層 priority：config.json（用戶）> config.example.json > DEFAULT_CONFIG。
    因此這裡直接用 config.get() 取值，不需要額外的 fallback。

    對於 list 欄位（dir_names、skip_terms、translatable_keywords），
    空清單 [] 是用戶的有效設定，會直接保留，不會被 DEFAULT 值置換。
    """
    lm_cfg = config.get("lm_translator", {})

    for setting in editable_settings():
        control = view.controls_map.get(setting.path)
        if control is None:
            continue
        value = _initial_value(config, setting)
        if setting.path == "translation_db.priority":
            db_config = config.get("translation_db", {}) or {}
            raw_priority = value
            value = priority_display_lines(raw_priority, db_config.get("path"))
        control.value = _to_control_value(setting, value)
        if setting.path == "translation_db.priority":
            db_config = config.get("translation_db", {}) or {}
            view._priority_source_baseline = {
                "path": priority_database_identity(db_config.get("path")),
                "has_custom": priority_has_custom_sources(
                    raw_priority, db_config.get("path")
                ),
            }
    _apply_label_templates(view, config)

    view.models_column.controls.clear()
    models_cfg = lm_cfg.get("models")
    if "models" not in lm_cfg:
        models_cfg = {
            name: {"enabled": enabled} for name, enabled in view.DEFAULT_MODELS.items()
        }
    else:
        models_cfg = models_cfg or {}
    for name, cfg in models_cfg.items():
        cap = cfg.get("max_output_tokens")
        if cap is None:
            view.add_model_row(name)
        else:
            try:
                view.add_model_row(name, cap)
            except TypeError:
                # Characterization/test doubles from the legacy one-argument API.
                view.add_model_row(name)
        view.models_column.controls[-1]._checkbox.value = bool(
            cfg.get("enabled", False)
        )

    view.key_fields.clear()
    view.keys_column.controls.clear()
    for key in lm_cfg.get("keys", []):
        tf = view._build_key_field(value=key)
        row = view._build_key_row(tf)
        view.key_fields.append(tf)
        view.keys_column.controls.append(row)
    hydrate_chatgpt_settings = getattr(view, "_hydrate_chatgpt_model_settings", None)
    if callable(hydrate_chatgpt_settings):
        hydrate_chatgpt_settings(lm_cfg.get("chatgpt_model_settings", {}))


def load_config_transactionally(view, load_config_json_fn):
    """Hydrate the view and commit its baseline only after every UI refresh succeeds."""
    previous_saved_form_state = view._saved_form_state
    try:
        view._loading_config = True
        try:
            config = load_config_json_fn()
            result = load_config_into_view(view, config)
            for field in view.key_fields:
                view._bind_change_tracking(field)
            loaded_form_state = view._capture_form_state()
            view.db_location.refresh()
            view._check_db_path()
            view._check_priority()
        finally:
            view._loading_config = False

        # Commit only after hydration and all dependent UI refreshes have succeeded.
        view._saved_form_state = loaded_form_state
        view._reload_recovery_required = False
        view._reload_before_next_entry = False
        view._reload_exit_acknowledged = False
        view._refresh_dirty_state()
    except Exception:
        view._loading_config = False
        view._saved_form_state = previous_saved_form_state
        view._reload_recovery_required = True
        view._reload_exit_acknowledged = False
        try:
            view._refresh_dirty_state()
        except Exception:  # noqa: BLE001 - hint refresh must not mask the reload error
            logger.error(
                "設定重載失敗後無法更新恢復提示：%s",
                redact_text(traceback.format_exc()),
            )
        raise
    return result


def save_config_from_view_with_outcome(
    view,
    *,
    load_config_json_fn,
    save_config_json_fn,
    validate_api_keys_from_ui_fn,
    registry=None,
    show_feedback=True,
):
    """從 view UI 控制項收集使用者輸入並寫入 config.json。

    寫入流程：
      1. load_config_json_fn() → 取得三層合併後的設定（作為基底）
      2. 從 view 控制項讀取新值，更新到基底 dict
      3. save_config_json_fn(new_config) → 寫入 config.json（觸發 normalization）
      4. view.load_config() → 重新讀取並刷新 UI（顯示寫入後的實際值）

    注意：基底來自 load_config_json_fn()，代表：
      - 如果 config.json 存在，會讀取使用者的實際設定（含自訂值）
      - 如果 config.json 不存在，會拿到 DEFAULT_CONFIG 的值
      → 按儲存後，使用者的「預設值」就會固化進 config.json（Layer 1 覆蓋 Layer 2/3）
    """
    new_config = _collect_validated_config(
        view,
        load_config_json_fn,
        validate_api_keys_from_ui_fn,
        show_feedback=show_feedback,
    )
    if new_config is None:
        return SaveOutcome.WRITE_FAILED
    if not _write_config_with_feedback(
        view, new_config, save_config_json_fn, show_feedback=show_feedback
    ):
        return SaveOutcome.WRITE_FAILED
    if not _reload_after_confirmed_write(view, show_feedback=show_feedback):
        return SaveOutcome.SAVED_RELOAD_FAILED
    if registry is not None:
        _refresh_registered_extractor_views(registry)

    if show_feedback:
        show_snack(view.page, "✅ 設定已成功儲存！", view._success_color())
    return SaveOutcome.SAVED_OK


def save_config_from_view(
    view,
    *,
    load_config_json_fn,
    save_config_json_fn,
    validate_api_keys_from_ui_fn,
    registry=None,
):
    """Compatibility boolean API; use the outcome variant for navigation decisions."""
    return (
        save_config_from_view_with_outcome(
            view,
            load_config_json_fn=load_config_json_fn,
            save_config_json_fn=save_config_json_fn,
            validate_api_keys_from_ui_fn=validate_api_keys_from_ui_fn,
            registry=registry,
        )
        is not SaveOutcome.WRITE_FAILED
    )


def finish_unsaved_dialog(view, *, continue_navigation, before_continue=None) -> bool:
    """Resolve navigation only after pop confirms this AlertDialog was topmost."""
    if view._unsaved_dialog_resolved:
        return False
    dialog = view._unsaved_dialog
    view._unsaved_dialog_closing = True
    try:
        popped_dialog = view.page.pop_dialog()
    finally:
        view._unsaved_dialog_closing = False
    if popped_dialog is not dialog:
        logger.error(
            "關閉設定確認框時預期取得目前設定對話框，實際取得 %s",
            type(popped_dialog).__name__ if popped_dialog is not None else "None",
        )
        return False
    view._unsaved_dialog_resolved = True
    view._unsaved_dialog_open = False
    callback = view._unsaved_dialog_continue
    view._unsaved_dialog_continue = None
    if continue_navigation and callback is not None:
        if before_continue is not None:
            before_continue()
        callback()
    return True


def on_unsaved_dialog_dismiss(view) -> None:
    """Treat an external dismiss as stay; only an explicit action may navigate."""
    if view._unsaved_dialog_closing or view._unsaved_dialog_resolved:
        return
    view._unsaved_dialog_resolved = True
    view._unsaved_dialog_open = False
    view._unsaved_dialog_continue = None


def handle_unsaved_dialog_save(view) -> None:
    """Save without stacking a SnackBar above the active unsaved-changes dialog."""
    if view._unsaved_dialog_resolved:
        return
    save_succeeded = view.save_config_clicked(None, show_feedback=False)
    if view._last_save_outcome is SaveOutcome.SAVED_RELOAD_FAILED:
        view._configure_unsaved_dialog()
    elif save_succeeded:
        if finish_unsaved_dialog(view, continue_navigation=True):
            show_snack(view.page, "✅ 設定已成功儲存！", view._success_color())
    else:
        view._unsaved_dialog.content.value = (
            "設定驗證或寫入失敗；變更仍保留在此頁，請修正後重試，或留在此頁。"
        )
        view.page.update()


def handle_unsaved_dialog_retry_reload(view) -> None:
    """Keep reload recovery in the same dialog, then close it before feedback."""
    if view._unsaved_dialog_resolved:
        return
    if not view._retry_config_reload(show_feedback=False):
        view._unsaved_dialog.content.value = (
            "設定已寫入，但重新載入仍失敗；可稍後重試或留在此頁。"
        )
        view.page.update()
        return
    if finish_unsaved_dialog(view, continue_navigation=True):
        show_snack(
            view.page,
            "✅ 設定已重新載入，畫面與設定檔已同步。",
            view._success_color(),
        )
