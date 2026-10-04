from __future__ import annotations

import logging
import traceback

from app.services_impl.logging_service import validate_log_format
from app.ui.snack import show_snack
from app.views.config.settings_schema import (
    Setting,
    editable_settings,
    get_path,
    set_path,
)
from translation_tool.utils.config_manager import get_default
from translation_tool.utils.redaction import redact_text

logger = logging.getLogger(__name__)

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
        control.value = _to_control_value(setting, _initial_value(config, setting))
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


def save_config_from_view(
    view,
    *,
    load_config_json_fn,
    save_config_json_fn,
    validate_api_keys_from_ui_fn,
    registry=None,
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
    new_config = load_config_json_fn()
    try:
        for setting in editable_settings():
            control = view.controls_map.get(setting.path)
            if control is None:
                continue
            set_path(
                new_config,
                setting.path,
                _from_control_value(setting, control.value),
            )
        api_keys = [
            key_field.value.strip()
            for key_field in view.key_fields
            if key_field.value and key_field.value.strip()
        ]
        validate_api_keys_from_ui_fn(api_keys)
        new_config["lm_translator"]["keys"] = api_keys
        models = {}
        for row in view.models_column.controls:
            cb = row._checkbox
            model_cfg = {"enabled": bool(cb.value)}
            cap_field = getattr(row, "_max_output_tokens", None)
            raw_cap = getattr(cap_field, "value", "") if cap_field is not None else ""
            if raw_cap not in (None, ""):
                model_cfg["max_output_tokens"] = int(raw_cap)
            models[cb.label] = model_cfg
        new_config["lm_translator"]["models"] = models
    except (ValueError, TypeError, RuntimeError) as err:
        # 錯誤訊息可能帶有使用者輸入，記錄前先遮蔽（#125）
        logger.error("儲存設定失敗：%s", redact_text(traceback.format_exc()))
        show_snack(view.page, f"❌ 發生錯誤：{type(err).__name__}: {err}")
        return False
    save_config_json_fn(new_config)
    view.load_config()

    if registry is not None:
        from app.view_registry import built_view

        for item in registry:
            # 只通知已建立的頁面；尚未建立的頁面建立時會讀取最新設定
            view_obj = built_view(item)
            if item["key"] != "extractor" or view_obj is None:
                continue

            content = getattr(view_obj, "content", None)
            refresh_config_defaults = getattr(content, "refresh_config_defaults", None)
            if callable(refresh_config_defaults):
                refresh_config_defaults()
            elif hasattr(content, "refresh_output_dir_helper"):
                # Backward-compatible fallback for legacy test doubles/views.
                content.refresh_output_dir_helper()

    show_snack(view.page, "✅ 設定已成功儲存！", view._success_color())
    return True
