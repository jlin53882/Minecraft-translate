"""Provider and per-model runtime overrides for the LM translator configuration.

設定項目的型別、說明與預設值都在 ``app/views/config/settings_schema.py``（設定頁 schema）與
``DEFAULT_CONFIG``；這裡只保留引擎端讀取「每個模型的輸出上限覆寫」的函式。
"""

from __future__ import annotations

CHATGPT_REASONING_EFFORTS = frozenset({"low", "medium", "high"})


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


def _chatgpt_model_settings(lm_config: dict, model_name: str) -> dict:
    all_settings = lm_config.get("chatgpt_model_settings")
    if not isinstance(all_settings, dict):
        return {}
    settings = all_settings.get(model_name)
    return settings if isinstance(settings, dict) else {}


def chatgpt_model_input_token_budget(lm_config: dict, model_name: str) -> int | None:
    """Get a positive per-model ChatGPT input budget, or ``None`` for global fallback."""
    value = _chatgpt_model_settings(lm_config, model_name).get("max_input_token_budget")
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else None
    )


def chatgpt_model_reasoning_effort(lm_config: dict, model_name: str) -> str | None:
    """Return a supported configured effort; ``None`` keeps the model default."""
    value = _chatgpt_model_settings(lm_config, model_name).get("reasoning_effort")
    return (
        value if isinstance(value, str) and value in CHATGPT_REASONING_EFFORTS else None
    )
