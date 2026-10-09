"""Tests for per-model LM configuration overrides."""

import pytest

from translation_tool.core.lm_config_schema import (
    chatgpt_model_input_token_budget,
    chatgpt_model_reasoning_effort,
)


def test_chatgpt_model_overrides_are_scoped_to_the_selected_model():
    config = {
        "chatgpt_model_settings": {
            "gpt-5-codex": {
                "max_input_token_budget": 12000,
                "reasoning_effort": "high",
            },
            "gpt-5-mini": {"reasoning_effort": "low"},
        }
    }

    assert chatgpt_model_input_token_budget(config, "gpt-5-codex") == 12000
    assert chatgpt_model_reasoning_effort(config, "gpt-5-codex") == "high"
    assert chatgpt_model_input_token_budget(config, "gpt-5-mini") is None
    assert chatgpt_model_reasoning_effort(config, "gpt-5-mini") == "low"
    assert chatgpt_model_reasoning_effort(config, "unknown-model") is None


@pytest.mark.parametrize("value", [True, False, 0, -1, "12000", None])
def test_invalid_chatgpt_input_budgets_use_global_fallback(value):
    config = {
        "chatgpt_model_settings": {"gpt-model": {"max_input_token_budget": value}}
    }

    assert chatgpt_model_input_token_budget(config, "gpt-model") is None


@pytest.mark.parametrize("value", ["model_default", "maximum", "", None, 1])
def test_unsupported_chatgpt_reasoning_efforts_use_model_default(value):
    config = {"chatgpt_model_settings": {"gpt-model": {"reasoning_effort": value}}}

    assert chatgpt_model_reasoning_effort(config, "gpt-model") is None


@pytest.mark.parametrize(
    "settings",
    [None, [], {"gpt-model": None}, {"gpt-model": "invalid"}],
)
def test_malformed_chatgpt_model_settings_are_ignored(settings):
    config = {"chatgpt_model_settings": settings}

    assert chatgpt_model_input_token_budget(config, "gpt-model") is None
    assert chatgpt_model_reasoning_effort(config, "gpt-model") is None
