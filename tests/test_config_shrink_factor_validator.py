"""設定頁儲存時擋下非法的縮批比例（必須 0 < factor < 1 且有限）。"""

from __future__ import annotations

import pytest

from app.views.config import config_actions
from app.views.config.settings_schema import SETTINGS_BY_PATH, VALIDATOR_NAMES

PATH = "lm_translator.batch_shrink_factor"


def test_validator_is_registered_on_the_setting():
    assert "batch_shrink_factor" in VALIDATOR_NAMES
    assert SETTINGS_BY_PATH[PATH].validator == "batch_shrink_factor"


@pytest.mark.parametrize("raw", ["0.5", " 0.75 ", 0.1, "0.999"])
def test_valid_values_are_accepted(raw):
    value = config_actions._from_control_value(SETTINGS_BY_PATH[PATH], raw)
    assert 0 < value < 1


@pytest.mark.parametrize("raw", ["0", "1", "1.5", "-0.2", "nan", "inf", "abc", "1e9"])
def test_invalid_values_are_rejected_instead_of_widening_batches(raw):
    with pytest.raises(ValueError):
        config_actions._from_control_value(SETTINGS_BY_PATH[PATH], raw)


def test_blank_uses_schema_default_which_is_valid():
    value = config_actions._from_control_value(SETTINGS_BY_PATH[PATH], "")
    assert 0 < value < 1
