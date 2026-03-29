"""tests/test_logging_config.py

PR1：Logging Core Foundation — log_config 單元測試。
"""

from __future__ import annotations

import pytest
from app.logging.log_config import load_ui_logging_config, DEFAULT_UI_LOGGING


class TestLoadUiLoggingConfig:
    """驗證 load_ui_logging_config 的 fallback 與正規化行為。"""

    def test_empty_config_returns_defaults(self):
        """傳入空 config 時應回傳完整 defaults。"""
        cfg = {}
        result = load_ui_logging_config(cfg.get, "ui_logging") if False else None
        # 兩種呼叫方式
        def empty_getter():
            return {}

        result = load_ui_logging_config(empty_getter)
        for key, val in DEFAULT_UI_LOGGING.items():
            assert result[key] == val

    def test_partial_config_merges_defaults(self):
        """只提供部分鍵時，未提供的鍵使用 default。"""
        def partial_getter():
            return {"ui_logging": {"max_ui_lines": 500}}

        result = load_ui_logging_config(partial_getter)
        assert result["max_ui_lines"] == 500
        assert result["tail_lines"] == DEFAULT_UI_LOGGING["tail_lines"]

    def test_invalid_value_falls_back(self):
        """無效值（負數/非int）時 fallback。"""
        def bad_getter():
            return {"ui_logging": {"max_session_logs": -1, "tail_lines": "abc"}}

        result = load_ui_logging_config(bad_getter)
        assert result["max_session_logs"] == DEFAULT_UI_LOGGING["max_session_logs"]
        assert result["tail_lines"] == DEFAULT_UI_LOGGING["tail_lines"]

    def test_show_levels_lowercase_normalized(self):
        """show_levels 傳入大寫時正規化為小寫。"""
        def upper_getter():
            return {"ui_logging": {"show_levels": ["INFO", "ERROR", "Warning"]}}

        result = load_ui_logging_config(upper_getter)
        assert result["show_levels"] == ["info", "error", "warning"]
