"""Per-model ``max_output_tokens`` helper for the ``lm_translator.models`` mapping.

設定項目的型別、說明與預設值都在 ``app/views/config/settings_schema.py``（設定頁 schema）與
``DEFAULT_CONFIG``；這裡只保留引擎端讀取「每個模型的輸出上限覆寫」的函式。
"""

from __future__ import annotations


def model_output_token_cap(lm_config: dict, model_name: str) -> int | None:
    """Get a valid per-model cap, or ``None`` to fall back to the global cap."""
    models = lm_config.get("models")
    if not isinstance(models, dict):
        return None
    model = models.get(model_name)
    if not isinstance(model, dict):
        return None
    value = model.get("max_output_tokens")
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )
