"""Extract pipeline service wrappers.

PR19：將 extract 類 service 從 app.services.py 抽離到 pipelines 子模組，
由 app.services 持續做 façade / re-export，維持 UI import 相容。

方案 2（廢除 poller）：worker 直接更新 UI，與 BundlerView 架構一致。

本模組職責：
- 統一管理 Lang/Book/Dual 三種提取模式的 Generator 呼叫
- 提供路徑準備工具（prepare_extraction_paths）以取代 UI 層硬編碼的拼接邏輯
- 透過 TaskSession 統一管理任務狀態、日誌、進度
"""

from __future__ import annotations

import logging
import os
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
    extract_dual_files_generator,
    extract_lang_files_generator,
)
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)


def _select_extraction_generator(mode: str, mods_dir: str, output_dir: str, lang_codes):
    """根據 mode 選擇對應的提取 Generator。

    Args:
        mode: 提取模式（'lang' / 'book' / 'dual'）
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        lang_codes: 指定要提取的語言代碼列表

    Returns:
        對應模式的 Generator 物件
    """
    if mode == "lang":
        return extract_lang_files_generator(mods_dir, output_dir, lang_codes=lang_codes)
    if mode == "book":
        return extract_book_files_generator(mods_dir, output_dir, lang_codes=lang_codes)
    return extract_dual_files_generator(mods_dir, output_dir, lang_codes=lang_codes)


def prepare_extraction_paths(mods_dir: str, mode: str, output_path: str = "") -> str:
    """統一處理提取任務的輸出路徑（含子資料夾名稱）。

    取代原本散落在 extractor_dialog.py 中的路徑拼接邏輯。
    UI 層不應再自行讀取 config 與拼接子資料夾。

    Args:
        mods_dir: Mod 來源資料夾路徑
        mode: 提取模式（'lang' / 'book' / 'dual'）
        output_path: 外部指定的輸出路徑（可為空）

    Returns:
        最終輸出路徑（含子資料夾名稱）
    """
    cfg = load_config()
    folder_names = cfg.get("extractor", {}).get("output_folder_names", {})
    lang_extract = folder_names.get("lang_extract", "_提取lang_輸出")
    book_extract = folder_names.get("book_extract", "_提取book_輸出")
    dual_extract = folder_names.get("dual_extract", "_提取both_輸出")

    if mode == "lang":
        output_subdir = lang_extract
    elif mode == "book":
        output_subdir = book_extract
    else:  # dual
        output_subdir = dual_extract

    # 若未指定輸出目錄，使用 mods_dir 作為基礎
    base_dir = output_path or mods_dir
    if base_dir:
        return os.path.join(base_dir, output_subdir)
    return ""


def get_output_folder_names() -> dict[str, str]:
    """取得 config 中的 output_folder_names 設定（含預設值）。

    取代 View 層內直接呼叫 load_config() + folder_names.get() 的樣板程式碼。
    讓 View 層只需呼叫此函數即可取得所有命名規則。

    Returns:
        dict 包含以下 key：
        - lang_extract / book_extract / dual_extract（提取模式的子資料夾名）
        - lang_preview / book_preview（預覽模式的子資料夾名）
    """
    cfg = load_config()
    folder_names = cfg.get("extractor", {}).get("output_folder_names", {})
    return {
        "lang_extract": folder_names.get("lang_extract", "_提取lang_輸出"),
        "book_extract": folder_names.get("book_extract", "_提取book_輸出"),
        "dual_extract": folder_names.get("dual_extract", "_提取both_輸出"),
        "lang_preview": folder_names.get("lang_preview", "_預覽lang_輸出"),
        "book_preview": folder_names.get("book_preview", "_預覽book_輸出"),
    }


def _run_extraction_with_session(
    generator,
    session: TaskSession,
    mode_label: str,
) -> None:
    """統一的 Generator 處理邏輯。

    三種提取模式（lang / book / dual）共用此流程：
    1. 透過 GLOBAL_LOG_LIMITER 過濾高頻日誌
    2. 寫入 TaskSession（log / progress / error）
    3. 完成或錯誤時呼叫 session.finish() / session.set_error()

    Args:
        generator: 對應模式的 Generator
        session: 任務 Session
        mode_label: 模式標籤，用於錯誤訊息（'Lang' / 'Book' / 'Dual'）
    """
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


def run_extraction_loop(
    generator,
    cancelled_flag=None,
    on_update=None,
):
    """從 Generator 中提取更新並調用回調函數（適用於 Dialog 等不需 TaskSession 的場景）。

    此函數供 extractor_dialog.py 等 UI 層使用，讓 Dialog 仍保有
    「直接處理 Generator yield + 立即更新 UI」的彈性，但不必重複
    Generator 過濾與 cancelled 檢查的樣板程式碼。

    Args:
        generator: 對應模式的 Generator（lang/book/dual）
        cancelled_flag: 長度為 1 的 list，用於執行緒間通訊取消（[False]）
                       None 表示不可取消
        on_update: 收到 update 時的回調函數 (update_dict) -> None

    Returns:
        統計 dict，包含 success / warnings / failures
    """
    stats = {"success": 0, "warnings": 0, "failures": 0}

    for update in generator:
        if cancelled_flag is not None and cancelled_flag[0]:
            return stats

        if "stats" in update:
            result = update["stats"]
            stats["success"] = result.get("success", 0)
            stats["warnings"] = result.get("warnings", 0)
            stats["failures"] = result.get("failures", 0)

        if update.get("error"):
            stats["failures"] += 1

        if on_update is not None:
            on_update(update)

    return stats


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
        generator = _select_extraction_generator("lang", mods_dir, output_dir, lang_codes)
        _run_extraction_with_session(generator, session, "Lang")
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
    lang_codes: list[str] | None = None,
) -> None:
    """執行書本檔擷取服務。

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
        generator = _select_extraction_generator("book", mods_dir, output_dir, lang_codes)
        _run_extraction_with_session(generator, session, "Book")
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


def run_dual_extraction_service(
    mods_dir: str,
    output_dir: str,
    session: TaskSession,
    lang_codes: list[str] | None = None,
) -> None:
    """執行 Dual（Lang + Book）雙模式提取服務。

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
        generator = _select_extraction_generator("dual", mods_dir, output_dir, lang_codes)
        _run_extraction_with_session(generator, session, "Dual")
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Dual 提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Dual 提取失敗：{e}\n{full_traceback}")
        session.set_error()
        GLOBAL_LOG_LIMITER.flush()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)