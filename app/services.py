"""app/services.py

此模組是 Flet UI 與核心翻譯流程之間的「服務層」：
- 封裝核心 generator（翻譯/抽取/檢查/打包）成 UI 友善的呼叫介面。
- 統一處理 TaskSession 的 log/progress/error 更新。
- 提供 log 節流（避免大量 log 造成 UI 重繪卡頓）。

維護注意：
- 這裡的函式偏長是歷史因素；本次僅補註解以降低維護風險，不改動邏輯。
- 設定檔路徑以 PROJECT_ROOT 為基準，避免 `os.getcwd()` 造成工作目錄切換時讀錯。
"""

# /minecraft_translator_flet/app/services.py
# PR13：建立 app.services_impl 骨架；本檔暫時仍是唯一實作來源。
# 後續 PR 會逐步把下方函式搬到 services_impl，再由本檔做薄 façade / re-export。
import os
import json
import traceback
import time
from collections import deque
from typing import Generator, Dict, Any
from pathlib import Path

# services.py
import logging
logger = logging.getLogger(__name__)

# PR14：logging / 節流 / handler 綁定抽離到 services_impl。
# 重要：這裡 re-export 的必須是同一個單例物件，避免外部拿到不同 instance。
from app.services_impl.logging_service import (
    LogLimiter,
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
)

# 核心演算法層的匯入
from translation_tool.core.ftb_translator import translate_directory_generator
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip
from translation_tool.core.jar_processor import extract_lang_files_generator, extract_book_files_generator
# PR15：config / rules IO 抽離到 services_impl。
from app.services_impl.config_service import (
    PROJECT_ROOT,
    CONFIG_PATH,
    REPLACE_RULES_PATH,
    _load_app_config,
    _save_app_config,
    load_config_json,
    save_config_json,
    load_replace_rules,
    save_replace_rules,
)
from translation_tool.utils.species_cache import lookup_species_name, is_potential_species_name
from translation_tool.core.lm_translator import translate_directory_generator as lm_translate_gen
from translation_tool.core.output_bundler import bundle_outputs_generator
from translation_tool.checkers.untranslated_checker import check_untranslated_generator
from translation_tool.checkers.variant_comparator import compare_variants_generator
from translation_tool.checkers.english_residue_checker import check_english_residue_generator
from translation_tool.checkers.variant_comparator_tsv import compare_variants_tsv_generator
from translation_tool.utils import cache_manager

from translation_tool.utils.log_unit import log_warning, log_error, log_debug, log_info


# PR15：config / rules IO 抽離至 app.services_impl.config_service。
# 注意：services.py 仍 re-export 同名符號，維持 views 的 import 相容。

# ------------------------------------------------------
# ---------------- 核心功能服務（含 log 限制） ----------------
# ------------------------------------------------------

def update_logger_config():
    """重新讀取 config 並套用最新的 Log 等級。

    PR14：此函式保留原本對外入口與呼叫點，但實作已抽離到
    `app.services_impl.logging_service.update_logger_config()`。

    注意：
    - 仍由 services.py 決定如何讀取 config（透過 `_load_app_config()`）。
    - handler/session 的 bind/unbind 流程不在 PR14 變更範圍內。
    """

    from app.services_impl import logging_service

    return logging_service.update_logger_config(_load_app_config, logger_name="translation_tool")



def run_lm_translation_service(
    input_dir: str,
    output_dir: str,
    session,
    dry_run: bool = False ,
    export_lang: bool = False,  # 新增參數
    write_new_cache: bool = True,  # 新增參數
):
    """執行 LM 翻譯流程（service 層包裝）。

    重點：
    - 內部呼叫 core generator，並把 generator 的 update_dict 轉成 TaskSession 更新。
    - 每次任務開始都會重讀 config 並更新 logger（讓 UI 修改 log level 後可立即生效）。

    風險/邊界：
    - 背景 thread 執行時，任何例外都必須轉成 session.add_log + session.set_error，
      避免 UI 沒訊息但任務已中止。
    - dry_run=True 時只做分析/預覽，不應送出任何實際翻譯請求。
    """
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 

    logger.debug(f"DEBUG [2. Service]: 接收到的 export_lang 為 -> {export_lang}")

    try:
        # 初始化 Session 狀態
        session.start()
        UI_LOG_HANDLER.set_session(session)
        # ⭐ Dry Run 模式提示
        if dry_run:
            session.add_log(
                "[DRY-RUN] 啟用：僅進行分析與預覽，不會送出任何 API 請求"
            )

        # ⭐ 把 dry_run 明確傳遞給 generator
        for update_dict in lm_translate_gen(
            input_dir,
            output_dir,
            dry_run=dry_run,   # ⭐ 關鍵
            export_lang=export_lang,
            write_new_cache=write_new_cache # ⭐ 傳遞下去
        ):
            # ---- log ----
            log = update_dict.get("log")
            if log:
                session.add_log(log)

            # ---- progress ----
            if "progress" in update_dict and update_dict["progress"] is not None:
                session.set_progress(update_dict["progress"])

            # ---- error ----
            if update_dict.get("error"):
                session.set_error()
                return

        # ---- 正常結束 ----
        final = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])

        if dry_run:
            session.add_log(
                "[DRY-RUN] 分析完成，未執行實際翻譯"
            )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"LM 服務失敗: {e}\n{full_traceback}")
        session.add_log(
            f"[致命錯誤] LM 翻譯服務失敗：{e}\n{full_traceback}"
        )
        session.set_error()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)



def run_lang_extraction_service(mods_dir: str, output_dir: str, session):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 
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
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 
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


# app/services.py
def run_ftb_translation_service(
    directory_path: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,   # ✅ 新增
    step_export: bool = True,
    step_clean: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,   # ✅ 新增
):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config()
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
    update_logger_config()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        # ✅ 只呼叫 pipeline 入口（import 路徑請對齊你檔案放的位置）
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
    update_logger_config()
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


def run_merge_zip_batch_service(
    zip_paths: list[str],
    output_dir: str,
    session,
    only_process_lang,
):
    """
    以 ZIP 為單位進行合併（支援 generator merge）
    - ZIP 層級 progress
    - merge_zhcn_to_zhtw_from_zip 內部 progress 疊加
    - log / error 完整轉交給 session
    """
     # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 
    UI_LOG_HANDLER.set_session(session)
    try:
        total = len(zip_paths)
        if total == 0:
            session.add_log("[系統] 未選擇任何 ZIP 檔案")
            session.finish()
            return

        for idx, zip_path in enumerate(zip_paths):
            zip_name = Path(zip_path).name
            zip_base_progress = idx / total

            session.add_log(f"[ZIP {idx + 1}/{total}] 開始處理：{zip_name}")

            try:
                # ⚠️ 關鍵：一定要 iterate generator，否則 merge 不會執行
                for update in merge_zhcn_to_zhtw_from_zip(zip_path, output_dir, only_process_lang):
                    # ---- log ----
                    if "log" in update and update["log"]:
                        session.add_log(update["log"])

                    # ---- progress（疊加 ZIP 進度）----
                    if "progress" in update and update["progress"] is not None:
                        merged_progress = zip_base_progress + (
                            update["progress"] / total
                        )
                        session.set_progress(min(merged_progress, 0.999))

                    # ---- error ----
                    if update.get("error"):
                        session.set_error()
                        return

                session.add_log(f"[ZIP {idx + 1}/{total}] 完成：{zip_name}")

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}"
                )
                session.add_log(
                    f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}"
                )
                session.set_error()
                return

            # ZIP 完成後，至少推進一次 progress
            session.set_progress((idx + 1) / total)

        session.finish()

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.add_log(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.set_error()
    
    finally:
    # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)

def run_manual_lookup_service(name: str) -> str:
    if not is_potential_species_name(name):
        return f"'{name}' 不像是一個有效的學名格式 (例如：Felis catus)。"
    result = lookup_species_name(name)
    return result if result else "在本地快取和線上查詢中均未找到結果。"

def run_batch_lookup_service(json_text: str):
    try:
        names = json.loads(json_text)
        if not isinstance(names, list):
            yield {"log": "錯誤：JSON 內容必須是一個列表 (List)。", "error": True}
            return

        results = {}
        total = len(names)

        first = GLOBAL_LOG_LIMITER.filter({"log": f"開始批次查詢 {total} 個學名...", "progress": 0})
        if first is not None:
            yield first

        for i, name in enumerate(names):
            if is_potential_species_name(name):
                result = lookup_species_name(name)
                results[name] = result if result else "未找到"
            else:
                results[name] = "格式錯誤"

            update = GLOBAL_LOG_LIMITER.filter({"log": f"({i+1}/{total}) 已查詢: {name}", "progress": (i + 1) / total})
            if update is not None:
                yield update

        final = GLOBAL_LOG_LIMITER.filter({
            "log": "--- 批次查詢完成 ---",
            "result": json.dumps(results, indent=2, ensure_ascii=False)
        })
        if final is not None:
            yield final

    except json.JSONDecodeError:
        logger.error( {"log": "輸入的不是有效的 JSON 格式。"})
        yield {"log": "輸入的不是有效的 JSON 格式。", "error": True}
    except Exception as e:
        logger.error( {"log": f"查詢時發生錯誤: {e}"})
        yield {"log": f"查詢時發生錯誤: {e}", "error": True}

def run_bundling_service(input_root_dir: str, output_zip_path: str):
    try:
        for update_dict in bundle_outputs_generator(input_root_dir, output_zip_path):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_untranslated_check_service(en_dir: str, tw_dir: str, out_dir: str):
    try:
        for update_dict in check_untranslated_generator(en_dir, tw_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 未翻譯檢查失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 未翻譯檢查失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_variant_compare_service(cn_dir: str, tw_dir: str, out_dir: str):
    try:
        for update_dict in compare_variants_generator(cn_dir, tw_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 簡繁差異比較失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 簡繁差異比較失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_english_residue_check_service(input_dir: str, out_dir: str):
    try:
        for update_dict in check_english_residue_generator(input_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 殘留英文檢查失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 殘留英文檢查失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_variant_compare_tsv_service(tsv_path: str, output_csv_path: str):
    try:
        for update_dict in compare_variants_tsv_generator(tsv_path, output_csv_path):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] TSV 簡繁差異比較失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] TSV 簡繁差異比較失敗：{e}\n{full_traceback}", "error": True, "progress": 0}


# PR16：cache UI services 抽離至 app.services_impl.cache.cache_services。
# 注意：services.py 仍 re-export 同名符號，維持 cache_view.py 的 import 相容。
from app.services_impl.cache.cache_services import (
    cache_get_overview_service,
    cache_reload_service,
    cache_reload_type_service,
    cache_save_all_service,
    cache_search_service,
    cache_get_entry_service,
    cache_update_dst_service,
    cache_rotate_service,
    cache_rebuild_index_service,
)

