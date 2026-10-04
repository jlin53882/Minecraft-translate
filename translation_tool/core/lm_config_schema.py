"""Minimal, backward-compatible schema for the ``lm_translator`` section."""

from __future__ import annotations

from typing import Final

LM_TRANSLATOR_SCHEMA: Final[dict[str, dict[str, object]]] = {
    # 預設值是範本佔位字串，不是空清單；與 DEFAULT_CONFIG 一致（有測試保護）。
    "keys": {
        "type": "list[str]",
        "default": ["YOUR_GEMINI_API_KEY_1", "YOUR_GEMINI_API_KEY_2"],
    },
    "models": {
        "type": "mapping[str, ModelConfig]",
        "default": {"gemini-2.5-flash": {"enabled": True}},
    },
    "max_output_tokens": {"type": "int", "default": 32768, "minimum": 0},
    "temperature": {"type": "number", "default": 0.3},
    "key_failure_cooldown_sec": {
        "type": "number",
        "default": 3600,
        "minimum": 0,
    },
}

MODEL_SCHEMA: Final[dict[str, dict[str, object]]] = {
    "enabled": {"type": "bool", "default": False},
    "max_output_tokens": {
        "type": "int|null",
        "default": None,
        "minimum": 0,
        "description": "per-model override; null uses the global value; 0 omits the field",
    },
}


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
