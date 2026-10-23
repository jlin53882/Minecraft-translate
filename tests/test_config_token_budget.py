"""lm_translator token 預算設定：預設值與驗證（issue #108）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from translation_tool.core.lm_batch_budget import BudgetConfig, TokenEstimatorConfig
from translation_tool.utils import config_manager as cm
from translation_tool.utils.config_manager import (
    DEFAULT_CONFIG,
    ConfigValidationError,
    _validate_lm_translator_config,
)

KEYS = [
    "token_budget_enabled",
    "max_output_token_budget",
    "max_input_token_budget",
    "max_output_tokens",
    "output_token_factor",
    "budget_min_scale",
    "budget_recover_after",
    "budget_recover_factor",
]


def test_defaults_exist_and_match_budget_config_defaults():
    lm = DEFAULT_CONFIG["lm_translator"]
    d = BudgetConfig()

    assert all(k in lm for k in KEYS)
    assert lm["token_budget_enabled"] is d.enabled
    assert lm["max_output_token_budget"] == d.max_output_token_budget == 65536
    assert lm["max_input_token_budget"] == d.max_input_token_budget == 256000
    assert lm["max_output_tokens"] == d.max_output_tokens == 65536
    assert lm["output_token_factor"] == d.output_token_factor
    assert lm["budget_min_scale"] == d.min_scale
    assert lm["budget_recover_after"] == d.recover_after
    assert lm["budget_recover_factor"] == d.recover_factor


def test_default_config_passes_validation():
    _validate_lm_translator_config(dict(DEFAULT_CONFIG["lm_translator"]))


def test_config_example_documents_the_new_keys():
    example = json.loads(
        (Path(__file__).resolve().parents[1] / "config.example.json").read_text(
            encoding="utf-8"
        )
    )["lm_translator"]

    for key in KEYS:
        assert key in example, f"config.example.json 缺少 {key}"
    estimator = example["token_estimator"]
    assert estimator == {
        "algorithm": "tiktoken_calibrated",
        "tiktoken_encoding": "o200k_base",
        "tiktoken_prompt_scale": 1.38027,
        "tiktoken_payload_scale": 0.92983,
        "tiktoken_request_bias": -451.55,
        "tiktoken_item_overhead_tokens": 0.39633,
        "prompt_ascii_tokens_per_char": 0.1,
        "prompt_cjk_tokens_per_char": 0.85,
        "prompt_other_tokens_per_char": 1.5,
        "item_ascii_tokens_per_char": 0.22,
        "item_cjk_tokens_per_char": 0.62,
        "item_other_tokens_per_char": 1.5,
        "input_item_overhead_tokens": 38,
        "output_item_overhead_tokens": 12,
    }
    assert BudgetConfig.from_config(example).token_estimator == TokenEstimatorConfig(
        algorithm="tiktoken_calibrated",
        tiktoken_encoding="o200k_base",
        tiktoken_prompt_scale=1.38027,
        tiktoken_payload_scale=0.92983,
        tiktoken_request_bias=-451.55,
        tiktoken_item_overhead_tokens=0.39633,
        prompt_ascii_tokens_per_char=0.1,
        prompt_cjk_tokens_per_char=0.85,
        prompt_other_tokens_per_char=1.5,
        item_ascii_tokens_per_char=0.22,
        item_cjk_tokens_per_char=0.62,
        item_other_tokens_per_char=1.5,
        input_item_overhead_tokens=38,
        output_item_overhead_tokens=12,
    )
    _validate_lm_translator_config(example)


def test_valid_custom_values_are_accepted():
    _validate_lm_translator_config(
        {
            "token_budget_enabled": False,
            "max_output_token_budget": 10000,
            "max_input_token_budget": 20000,
            "max_output_tokens": 0,  # 0 = 不送 maxOutputTokens
            "output_token_factor": 2,
            "budget_min_scale": 1,
            "budget_recover_after": 1,
            "budget_recover_factor": 2,
        }
    )


def test_token_estimator_values_are_validated_and_fallback_is_safe():
    _validate_lm_translator_config(
        {
            "token_estimator": {
                "prompt_ascii_tokens_per_char": 0.1,
                "input_item_overhead_tokens": 38,
            }
        }
    )

    with pytest.raises(ConfigValidationError, match="token_estimator"):
        _validate_lm_translator_config(
            {"token_estimator": {"item_ascii_tokens_per_char": -1}}
        )
    with pytest.raises(ConfigValidationError, match="token_estimator"):
        _validate_lm_translator_config(
            {"token_estimator": {"item_ascii_tokens_per_char": "0.22"}}
        )
    with pytest.raises(ConfigValidationError, match="algorithm"):
        _validate_lm_translator_config(
            {"token_estimator": {"algorithm": "unknown"}}
        )

    estimator = TokenEstimatorConfig.from_config(
        {
            "token_estimator": {
                "item_ascii_tokens_per_char": "bad",
                "item_cjk_tokens_per_char": float("inf"),
                "input_item_overhead_tokens": -1,
            }
        }
    )
    assert estimator.item_ascii_tokens_per_char == TokenEstimatorConfig().item_ascii_tokens_per_char
    assert estimator.item_cjk_tokens_per_char == TokenEstimatorConfig().item_cjk_tokens_per_char
    assert estimator.input_item_overhead_tokens == TokenEstimatorConfig().input_item_overhead_tokens


@pytest.mark.parametrize("value", [1, 8, 15])
def test_chatgpt_responses_rejects_output_caps_below_api_minimum(value):
    with pytest.raises(ConfigValidationError, match="max_output_tokens"):
        _validate_lm_translator_config(
            {"provider": "chatgpt", "max_output_tokens": value}
        )


@pytest.mark.parametrize("value", [1, 8, 15])
def test_chatgpt_model_overrides_reject_output_caps_below_api_minimum(value):
    with pytest.raises(ConfigValidationError, match="max_output_tokens"):
        _validate_lm_translator_config(
            {
                "provider": "chatgpt",
                "models": {"gpt-5-codex": {"max_output_tokens": value}},
            }
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("token_budget_enabled", "yes"),
        ("token_budget_enabled", 1),
        ("max_output_token_budget", 0),
        ("max_output_token_budget", -5),
        ("max_output_token_budget", "24000"),
        ("max_output_token_budget", True),
        ("max_output_token_budget", 1.5),
        ("max_input_token_budget", 0),
        ("max_output_tokens", -1),
        ("max_output_tokens", "big"),
        ("output_token_factor", 0.1),
        ("output_token_factor", 7),
        ("output_token_factor", "1.5"),
        ("budget_min_scale", 0),
        ("budget_min_scale", 1.5),
        ("budget_recover_after", 0),
        ("budget_recover_after", 2.5),
        ("budget_recover_factor", 1),
        ("budget_recover_factor", 5),
    ],
)
def test_invalid_values_are_rejected_with_the_key_name(key, value):
    with pytest.raises(ConfigValidationError, match=key):
        _validate_lm_translator_config({key: value})


def test_budget_larger_than_api_cap_only_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _validate_lm_translator_config(
            {"max_output_token_budget": 40000, "max_output_tokens": 32768}
        )

    assert any("max_output_token_budget" in r.getMessage() for r in caplog.records)


def test_user_config_without_the_keys_gets_defaults_after_merge(tmp_path):
    user = {"lm_translator": {"temperature": 0.5}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(user), encoding="utf-8")

    cm.clear_config_cache()
    cfg = cm.load_config(path)

    for key in KEYS:
        assert key in cfg["lm_translator"]
    assert cfg["lm_translator"]["temperature"] == 0.5
    cm.clear_config_cache()


def test_token_budget_settings_live_on_their_own_settings_page():
    from app.views.config.settings_schema import NAV_PAGES, resolved_layout
    from translation_tool.utils.config_schema import C_TOKEN_BUDGET, SETTINGS_BY_PATH

    moved = (
        "max_output_token_budget",
        "max_input_token_budget",
        "token_budget_enabled",
        "output_token_factor",
        "budget_min_scale",
        "budget_recover_after",
        "budget_recover_factor",
    )
    assert "token_budget" in [page["id"] for page in NAV_PAGES]
    for key in moved:
        setting = SETTINGS_BY_PATH[f"lm_translator.{key}"]
        assert (setting.page, setting.card) == ("token_budget", C_TOKEN_BUDGET)

    layout = resolved_layout()
    assert [card.title for card in layout["token_budget"]] == [C_TOKEN_BUDGET]
    batch_paths = {
        path
        for card in layout["batch_limits"]
        for block in card.blocks
        for path in getattr(block, "paths", ())
    }
    assert not any(path.rsplit(".", 1)[1] in moved for path in batch_paths)
