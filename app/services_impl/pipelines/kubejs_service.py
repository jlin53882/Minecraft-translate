"""KubeJS pipeline service wrappers.

PR23：將 KubeJS 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.logging_service import UI_LOG_HANDLER
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging

logger = logging.getLogger(__name__)


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
    """run_kubejs_tooltip_service 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    ensure_pipeline_logging()
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
