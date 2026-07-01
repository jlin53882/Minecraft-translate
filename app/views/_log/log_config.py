"""app/logging/log_config.py

日誌系統設定讀取與 normalize。

職責：
- 從 app config 讀取 ui_logging 區塊
- 提供 default fallback
- 驗證與正規化設定值
"""

from __future__ import annotations

from typing import Any

# 預設值（安全 default）
DEFAULT_UI_LOGGING = {
    "max_session_logs": 2000,
    "max_ui_lines": 300,
    "tail_lines": 250,
    "show_levels": ["system", "info", "warning", "error"],
    "colorize": True,
}


def load_ui_logging_config(config_getter: callable) -> dict[str, Any]:
    """
    從 config 讀取 ui_logging 設定，缺少任何欄位時以 default 填補。

    Args:
        config_getter: 呼叫端傳入的 config getter，通常是 load_config()
                     實例或其 get 方法

    Returns:
        ui_logging 設定字典，永遠包含所有鍵
    """
    # 相容兩種呼叫方式：直接傳 dict 或傳 getter function
    if callable(config_getter):
        raw: dict = config_getter().get("ui_logging", {})
    else:
        raw = config_getter.get("ui_logging", {})

    result = dict(DEFAULT_UI_LOGGING)  # copy default

    for key in ["max_session_logs", "max_ui_lines", "tail_lines"]:
        if key in raw and isinstance(raw[key], int) and raw[key] > 0:
            result[key] = raw[key]

    if "show_levels" in raw and isinstance(raw["show_levels"], list):
        result["show_levels"] = [str(level_str).lower() for level_str in raw["show_levels"] if level_str]

    if "colorize" in raw:
        result["colorize"] = bool(raw["colorize"])

    return result
