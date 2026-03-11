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



# PR21：LM UI services 抽離至 app.services_impl.pipelines.lm_service。
# 注意：services.py 仍 re-export 同名符號，維持 lm_view.py 的 import 相容。
from app.services_impl.pipelines.lm_service import run_lm_translation_service


# PR19：extract UI services 抽離至 app.services_impl.pipelines.extract_service。
# 注意：services.py 仍 re-export 同名符號，維持 extractor_view.py 的 import 相容。
from app.services_impl.pipelines.extract_service import (
    run_lang_extraction_service,
    run_book_extraction_service,
)


# PR22：FTB UI services 抽離至 app.services_impl.pipelines.ftb_service。
# 注意：services.py 仍 re-export 同名符號，維持 translation_view.py 的 lazy import 相容。
from app.services_impl.pipelines.ftb_service import run_ftb_translation_service


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


# PR20：merge UI services 抽離至 app.services_impl.pipelines.merge_service。
# 注意：services.py 仍 re-export 同名符號，維持 merge_view.py 的 import 相容。
from app.services_impl.pipelines.merge_service import run_merge_zip_batch_service

# PR17：lookup UI services 抽離至 app.services_impl.pipelines.lookup_service。
# 注意：services.py 仍 re-export 同名符號，維持 lookup_view.py 的 import 相容。
from app.services_impl.pipelines.lookup_service import (
    run_manual_lookup_service,
    run_batch_lookup_service,
)

# PR18：bundle UI services 抽離至 app.services_impl.pipelines.bundle_service。
# 注意：services.py 仍 re-export 同名符號，維持 bundler_view.py 的 import 相容。
from app.services_impl.pipelines.bundle_service import run_bundling_service

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

