"""FTB pipeline service wrappers.

PR22：將 FTB 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging

logger = logging.getLogger(__name__)


def run_ftb_translation_service(
    directory_path: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,
    step_export: bool = True,
    step_clean: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        from translation_tool.core.ftb_translator import run_ftb_pipeline

        run_ftb_pipeline(
            directory_path,
            session=session,
            output_dir=output_dir,
            dry_run=dry_run,
            step_export=step_export,
            step_clean=step_clean,
            step_translate=step_translate,
            step_inject=step_inject,
            write_new_cache=write_new_cache,
        )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] FTB 服務失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] {e}\n{full_traceback}")
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)
