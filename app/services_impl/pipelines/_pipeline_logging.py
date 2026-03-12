"""Shared logging bootstrap for pipeline service wrappers.

PR33: deduplicate repeated logger-config bootstrap logic.
"""

from __future__ import annotations

from app.services_impl.config_service import _load_app_config
from app.services_impl.logging_service import update_logger_config as apply_logger_config


def ensure_pipeline_logging():
    """Refresh logger config before each pipeline run (keeps old behavior)."""
    return apply_logger_config(_load_app_config, logger_name="translation_tool")
