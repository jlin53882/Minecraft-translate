"""KubeJS pipeline service wrappers.

PR23：將 KubeJS 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.config_service import _load_app_config
from app.services_impl.logging_service import UI_LOG_HANDLER, update_logger_config as apply_logger_config

logger = logging.getLogger(__name__)


def _update_logger_config():
    return apply_logger_config(_load_app_config, logger_name="translation_tool")


def run_kubejs_tooltip_service(
    input_dir: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
):
    _update_logger_config()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        from translation_tool.core.kubejs_translator import run_kubejs_pipeline

        run_kubejs_pipeline(
            input_dir=input_dir,
            output_dir=output_dir,
            session=session,
            dry_run=dry_run,
            step_extract=step_extract,
            step_translate=step_translate,
            step_inject=step_inject,
            write_new_cache=write_new_cache,
        )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] KubeJS 服務失敗：{e}\n{full_traceback}")
        # ✅ UI_LOG_HANDLER 已接好：logger.error 會出現在 UI，所以不用 session.add_log
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)
