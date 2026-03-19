"""Storage normalization tests for config_service.

Verifies that when process_zh_cn_files=False, the dependent fields
skip_zh_cn_when_only_process_lang and patchouli_skip_en_us_when_zh_cn_exists
are forced to False before writing to disk.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


class TestStorageNormalization:
    """Tests for process_zh_cn_files聯動 normalization."""

    def test_zeros_dependent_fields_when_disabled(self, tmp_path: Path):
        """process_zh_cn_files=False should force other fields to False."""
        from app.services_impl import config_service

        config = {
            "process_zh_cn_files": False,
            "skip_zh_cn_when_only_process_lang": True,   # should be forced False
            "patchouli_skip_en_us_when_zh_cn_exists": True,  # should be forced False
        }
        fake_path = tmp_path / "config.json"

        # Patch CONFIG_PATH so the real file isn't touched
        with patch.object(config_service, "CONFIG_PATH", fake_path):
            config_service._save_app_config(config)

        with open(fake_path, encoding="utf-8") as f:
            saved = json.load(f)

        assert saved["skip_zh_cn_when_only_process_lang"] is False
        assert saved["patchouli_skip_en_us_when_zh_cn_exists"] is False

    def test_preserves_fields_when_enabled(self, tmp_path: Path):
        """process_zh_cn_files=True should preserve the other fields."""
        from app.services_impl import config_service

        config = {
            "process_zh_cn_files": True,
            "skip_zh_cn_when_only_process_lang": True,
            "patchouli_skip_en_us_when_zh_cn_exists": True,
        }
        fake_path = tmp_path / "config.json"

        with patch.object(config_service, "CONFIG_PATH", fake_path):
            config_service._save_app_config(config)

        with open(fake_path, encoding="utf-8") as f:
            saved = json.load(f)

        assert saved["skip_zh_cn_when_only_process_lang"] is True
        assert saved["patchouli_skip_en_us_when_zh_cn_exists"] is True

    def test_preserves_other_fields_when_disabled(self, tmp_path: Path):
        """When disabled, only the two dependent fields should be forced False."""
        from app.services_impl import config_service

        config = {
            "process_zh_cn_files": False,
            "skip_zh_cn_when_only_process_lang": True,
            "patchouli_skip_en_us_when_zh_cn_exists": True,
            "some_other_field": "keep_this_value",
        }
        fake_path = tmp_path / "config.json"

        with patch.object(config_service, "CONFIG_PATH", fake_path):
            config_service._save_app_config(config)

        with open(fake_path, encoding="utf-8") as f:
            saved = json.load(f)

        assert saved["some_other_field"] == "keep_this_value"
