"""Shared logging bootstrap for pipeline service wrappers.

PR33: deduplicate repeated logger-config bootstrap logic.
"""

from __future__ import annotations

from app.services_impl.config_service import _load_app_config
from app.services_impl.logging_service import update_logger_config as apply_logger_config

def ensure_pipeline_logging():
    """在每次流水線執行前刷新日誌配置，確保 translation_tool 的日誌行為符合預期。"""
    return apply_logger_config(_load_app_config, logger_name="translation_tool")