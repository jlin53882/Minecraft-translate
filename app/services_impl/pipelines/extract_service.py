"""Extract pipeline service wrappers.

PR19：將 extract 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.logging_service import (
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
)
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging
from translation_tool.core.jar_processor import (
    extract_book_files_generator,
    extract_lang_files_generator,
)

logger = logging.getLogger(__name__)

def run_lang_extraction_service(mods_dir: str, output_dir: str, session):
    """執行語言檔擷取服務。"""
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        for update in extract_lang_files_generator(mods_dir, output_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update)
            if filtered is None:
                continue

            if "log" in filtered:
                session.add_log(filtered["log"])

            if "progress" in filtered:
                session.set_progress(filtered["progress"])

            if filtered.get("error"):
                session.set_error()
                return
        # for loop 結束後
        final = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])
        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Lang 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Lang 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)

def run_book_extraction_service(mods_dir: str, output_dir: str, session):
    """執行書本檔擷取服務。"""
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        for update in extract_book_files_generator(mods_dir, output_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update)
            if filtered is None:
                continue

            if "log" in filtered:
                session.add_log(filtered["log"])

            if "progress" in filtered:
                session.set_progress(filtered["progress"])

            if filtered.get("error"):
                session.set_error()
                return

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()

    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)
