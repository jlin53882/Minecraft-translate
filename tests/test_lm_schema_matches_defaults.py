"""#134：schema 與 DEFAULT_CONFIG 預設值必須一致，避免兩處各自維護而漂移。"""

from __future__ import annotations

from translation_tool.core.lm_config_schema import LM_TRANSLATOR_SCHEMA, MODEL_SCHEMA
from translation_tool.utils.config_manager import DEFAULT_CONFIG


def test_lm_translator_schema_defaults_match_default_config():
    lm_defaults = DEFAULT_CONFIG["lm_translator"]
    for key, spec in LM_TRANSLATOR_SCHEMA.items():
        assert key in lm_defaults, f"schema 欄位 {key} 不在 DEFAULT_CONFIG"
        assert lm_defaults[key] == spec["default"], (
            f"{key}: schema 預設 {spec['default']!r} != DEFAULT_CONFIG {lm_defaults[key]!r}"
        )


def test_model_schema_fields_are_optional_and_default_safe():
    assert MODEL_SCHEMA["max_output_tokens"]["default"] is None
    assert MODEL_SCHEMA["enabled"]["default"] is False
