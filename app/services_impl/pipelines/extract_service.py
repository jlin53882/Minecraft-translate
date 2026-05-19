"""Extract pipeline service wrappers.

PR19：將 extract 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。

方案 2（廢除 poller）：worker 直接更新 UI，與 BundlerView 架構一致。
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from app.services_impl.logging_service import (
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
)
from app.services_impl.pipelines._pipeline_logging import ensure_pipeline_logging
from app.logging.task_session import TaskSession
from translation_tool.core.jar_processor import (
    extract_book_files_generator,
    extract_lang_files_generator,
)

logger = logging.getLogger(__name__)


def _run_extraction(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
    mode: str,
    lang_codes: list[str] | None = None,
) -> None:
    """Worker 主體：直接更新 UI，不依賴 poller。"""
    generator = extract_lang_files_generator(mods_dir, output_dir, lang_codes=lang_codes) if mode == 'lang' else extract_book_files_generator(mods_dir, output_dir)

    for update in generator:
        filtered: dict[str, Any] | None = GLOBAL_LOG_LIMITER.filter(update)
        if filtered is None:
            continue

        if "log" in filtered:
            session.add_log(filtered["log"])

        if "progress" in filtered:
            session.set_progress(filtered["progress"])

        if filtered.get("error"):
            session.set_error()
            return

    final: dict[str, Any] | None = GLOBAL_LOG_LIMITER.flush()
    if final and "log" in final:
        session.add_log(final["log"])
    session.finish()


def run_lang_extraction_service(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
    lang_codes: list[str] | None = None,
) -> None:
    """執行語言檔擷取服務。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        session: 任務 Session
        lang_codes: 指定要提取的語言代碼列表，若為 None 則從 config 讀取
    """
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        for update in extract_lang_files_generator(mods_dir, output_dir, lang_codes=lang_codes):
            filtered: dict[str, Any] | None = GLOBAL_LOG_LIMITER.filter(update)
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
        final: dict[str, Any] | None = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])
        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Lang 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Lang 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


def run_book_extraction_service(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
) -> None:
    """執行書本檔擷取服務。"""
    ensure_pipeline_logging()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        for update in extract_book_files_generator(mods_dir, output_dir):
            filtered: dict[str, Any] | None = GLOBAL_LOG_LIMITER.filter(update)
            if filtered is None:
                continue

            if "log" in filtered:
                session.add_log(filtered["log"])

            if "progress" in filtered:
                session.set_progress(filtered["progress"])

            if filtered.get("error"):
                session.set_error()
                return

        final: dict[str, Any] | None = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])
        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()
        GLOBAL_LOG_LIMITER.flush()

    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)