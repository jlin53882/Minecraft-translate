"""LM pipeline service wrappers.

PR21：將 LM 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback

from app.services_impl.config_service import _load_app_config
from app.services_impl.logging_service import (
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
    update_logger_config as apply_logger_config,
)
from translation_tool.core.lm_translator import translate_directory_generator as lm_translate_gen

logger = logging.getLogger(__name__)


def _update_logger_config():
    return apply_logger_config(_load_app_config, logger_name="translation_tool")


def run_lm_translation_service(
    input_dir: str,
    output_dir: str,
    session,
    dry_run: bool = False,
    export_lang: bool = False,
    write_new_cache: bool = True,
):
    """執行 LM 翻譯流程（service 層包裝）。"""
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    _update_logger_config()

    logger.debug(f"DEBUG [2. Service]: 接收到的 export_lang 為 -> {export_lang}")

    try:
        # 初始化 Session 狀態
        session.start()
        UI_LOG_HANDLER.set_session(session)
        # ⭐ Dry Run 模式提示
        if dry_run:
            session.add_log("[DRY-RUN] 啟用：僅進行分析與預覽，不會送出任何 API 請求")

        # ⭐ 把 dry_run 明確傳遞給 generator
        for update_dict in lm_translate_gen(
            input_dir,
            output_dir,
            dry_run=dry_run,
            export_lang=export_lang,
            write_new_cache=write_new_cache,
        ):
            log = update_dict.get("log")
            if log:
                session.add_log(log)

            if "progress" in update_dict and update_dict["progress"] is not None:
                session.set_progress(update_dict["progress"])

            if update_dict.get("error"):
                session.set_error()
                return

        final = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])

        if dry_run:
            session.add_log("[DRY-RUN] 分析完成，未執行實際翻譯")

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"LM 服務失敗: {e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] LM 翻譯服務失敗：{e}\n{full_traceback}")
        session.set_error()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)
