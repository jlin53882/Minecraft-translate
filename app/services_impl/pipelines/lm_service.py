"""LM pipeline service wrappers.

PR21：將 LM 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。
"""

from __future__ import annotations

import logging
import traceback
from functools import partial

from app.services_impl.logging_service import (
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
)
from app.services_impl.pipelines._pipeline_logging import (
    ensure_pipeline_logging,
    mirror_session_log,
)
from translation_tool.core.lm_translator import (
    translate_directory_generator as lm_translate_gen,
)
from translation_tool.translation_db import DbSettings
from translation_tool.utils.cancellation import cancel_scope

logger = logging.getLogger(__name__)


def _session_cancel_requested(session) -> bool:
    return bool(getattr(session, "cancel_requested", False))


def run_lm_translation_service(
    input_dir: str,
    output_dir: str,
    session,
    dry_run: bool = False,
    export_lang: bool = False,
    write_new_cache: bool = True,
    *,
    manage_session: bool = True,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
    translation_db_settings_snapshot: DbSettings | None = None,
):
    """執行 LM 翻譯流程（service 層包裝）。

    ``manage_session=False`` 時由呼叫端擁有 ``TaskSession`` 的 ``start()``／``finish()``
    （一鍵流程的步驟 3 會依序翻譯多個來源，共用同一個 session）。

    Admitted UI operations pass ``translation_db_settings_snapshot``; ``None`` remains
    supported for legacy callers that resolve the current global settings.
    """
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    ensure_pipeline_logging()

    logger.debug(f"DEBUG [2. Service]: 接收到的 export_lang 為 -> {export_lang}")

    try:
        # 初始化 Session 狀態
        if manage_session:
            session.start()
        UI_LOG_HANDLER.set_session(session)
        # ⭐ Dry Run 模式提示
        if dry_run:
            mirror_session_log(
                session,
                logger,
                "[DRY-RUN] 啟用：僅進行分析與預覽，不會送出任何 API 請求",
            )

        # ⭐ 把 dry_run 明確傳遞給 generator
        gen = lm_translate_gen(
            input_dir,
            output_dir,
            dry_run=dry_run,
            export_lang=export_lang,
            write_new_cache=write_new_cache,
            should_cancel=partial(_session_cancel_requested, session),
            use_translation_db=use_translation_db,
            translation_db_version=translation_db_version,
            translation_db_settings_snapshot=translation_db_settings_snapshot,
        )
        # cancel_scope：generator 在此執行緒迭代，等待 API 限流時也能被取消打斷
        with cancel_scope(partial(_session_cancel_requested, session)):
            for update_dict in gen:
                filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
                if filtered is None:
                    continue

                if filtered.get("log"):
                    session.add_log(filtered["log"])

                if "progress" in filtered and filtered["progress"] is not None:
                    session.set_progress(filtered["progress"])

                if filtered.get("error"):
                    session.set_error()
                    return

        final = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])

        if dry_run:
            mirror_session_log(session, logger, "[DRY-RUN] 分析完成，未執行實際翻譯")

    except Exception as e:  # noqa: BLE001
        full_traceback = traceback.format_exc()
        mirror_session_log(
            session,
            logger,
            f"[致命錯誤] LM 翻譯服務失敗：{e}\n{full_traceback}",
            level="error",
        )
        session.set_error()
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)
        # 任何結束路徑（成功／service 回報錯誤／例外）都只送一次 terminal finish：
        # 失敗時順序是 set_error() → finish()（已標記錯誤的維持 ERROR）
        if manage_session:
            session.finish()
