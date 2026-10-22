"""智慧校對設定：預設值、schema、範例設定檔與有效設定物件一致。"""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from translation_tool.utils import config_schema
from translation_tool.utils.config_manager import get_smart_review_settings
from translation_tool.utils.smart_review_settings import (
    DEFAULT_SMART_REVIEW_BATCH_SIZE,
    DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE,
    DEFAULT_SMART_REVIEW_STORAGE_CHUNK_SIZE,
    SMART_REVIEW_SETTING_LABELS,
    SmartReviewSettings,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "default_batch_size": DEFAULT_SMART_REVIEW_BATCH_SIZE,
    "max_batch_size": DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE,
    "storage_chunk_size": DEFAULT_SMART_REVIEW_STORAGE_CHUNK_SIZE,
}


def _schema_defaults() -> dict[str, object]:
    found = {}
    for setting in config_schema.SETTINGS:
        key = setting.path
        if key.startswith("smart_review."):
            found[key.split(".", 1)[1]] = setting.default
    return found


def test_schema_defaults_match_constants():
    assert _schema_defaults() == DEFAULTS


def test_example_config_matches_defaults():
    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert example["smart_review"] == DEFAULTS


def test_labels_cover_all_fields():
    assert set(SMART_REVIEW_SETTING_LABELS) == set(DEFAULTS)


def test_defaults_and_immutability():
    settings = SmartReviewSettings.from_config({})
    assert settings.as_dict() == DEFAULTS
    with pytest.raises(FrozenInstanceError):
        settings.max_batch_size = 1  # type: ignore[misc]
    assert dict(settings) == DEFAULTS and len(settings) == 3
    with pytest.raises(KeyError):
        settings["nope"]


def test_invalid_values_fall_back_and_default_is_capped():
    settings = SmartReviewSettings.from_config(
        {
            "smart_review": {
                "default_batch_size": 99,
                "max_batch_size": 30,
                "storage_chunk_size": True,
            }
        }
    )
    assert settings.default_batch_size == 30
    assert settings.storage_chunk_size == DEFAULT_SMART_REVIEW_STORAGE_CHUNK_SIZE


def test_get_smart_review_settings_accepts_explicit_config():
    cfg = {"smart_review": {"max_batch_size": 10, "default_batch_size": 5}}
    settings = get_smart_review_settings(cfg)
    assert isinstance(settings, SmartReviewSettings)
    assert settings["max_batch_size"] == 10 and settings.default_batch_size == 5
