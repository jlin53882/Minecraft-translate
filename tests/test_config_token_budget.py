"""lm_translator token 預算設定：預設值與驗證（issue #108）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from translation_tool.core.lm_batch_budget import BudgetConfig
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
    assert lm["max_output_token_budget"] == d.max_output_token_budget == 24000
    assert lm["max_input_token_budget"] == d.max_input_token_budget == 60000
    assert lm["max_output_tokens"] == d.max_output_tokens == 32768
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
