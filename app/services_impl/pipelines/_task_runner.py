"""Shared task runner for pipeline service wrappers."""

from __future__ import annotations

import logging
import traceback
from typing import Callable, Iterable, Any

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging

logger = logging.getLogger(__name__)


def run_callable_task(*, session, task_name: str, func: Callable[..., Any], kwargs: dict, add_session_log_on_error: bool = False, ui_log_handler=UI_LOG_HANDLER):
    """Run a callable pipeline task with shared lifecycle handling."""
    ensure_pipeline_logging()
    try:
        session.start()
        ui_log_handler.set_session(session)
        result = func(**kwargs)
        session.finish()
        return result
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error("[%s] %s\n%s", task_name, e, full_traceback)
        if add_session_log_on_error:
            session.add_log(f"[{task_name}] {e}\n{full_traceback}")
        session.set_error()
        return None
    finally:
        ui_log_handler.set_session(None)

