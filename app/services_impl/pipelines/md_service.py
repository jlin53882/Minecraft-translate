"""Markdown pipeline service wrappers.

PR24：將 Markdown 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging

logger = logging.getLogger(__name__)


def run_md_translation_service(
    input_dir: str,
    session,
    output_dir: str | None = None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
    lang_mode: str = "non_cjk_only",
):
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        from translation_tool.core.md_translation_assembly import run_md_pipeline

        run_md_pipeline(
            input_dir=input_dir,
            session=session,
            output_dir=output_dir,
            dry_run=dry_run,
            step_extract=step_extract,
            step_translate=step_translate,
            step_inject=step_inject,
            write_new_cache=write_new_cache,
            lang_mode=lang_mode,
        )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[非預期錯誤] MD 流程失敗：{e}\n{full_traceback}")
        session.add_log(f"[非預期錯誤] {e}\n{full_traceback}")
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)
