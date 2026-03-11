"""app/services.py

此模組現在的主要角色是 façade / export surface：
- 對外維持 `app.services` 這個穩定入口，避免 UI caller 每拆一顆 service 就跟著改 import。
- 少數尚未拆除或暫緩處理的 legacy wrappers，暫時仍留在這裡。

維護注意：
- 新的 pipeline 實作優先放 `app/services_impl/pipelines/`。
- `app/services.py` 只保留穩定出口與少量過渡層邏輯，避免再次膨脹回雜物間。
- QC / checkers 線目前方向未定，先維持現狀，不在這顆 cleanup 內處理。
"""

# /minecraft_translator_flet/app/services.py
# PR13：建立 app.services_impl 骨架；後續逐步由本檔做薄 façade / re-export。
import logging
import traceback

logger = logging.getLogger(__name__)

# PR14：logging / 節流 / handler 綁定抽離到 services_impl。
# 目前本檔仍需保留 GLOBAL_LOG_LIMITER 與 update_logger_config() 這個對外入口。
from app.services_impl.logging_service import GLOBAL_LOG_LIMITER

# PR15：config / rules IO 抽離到 services_impl。
# 注意：services.py 仍 re-export 同名符號，維持 views 的 import 相容。
from app.services_impl.config_service import (
    _load_app_config,
    load_config_json,
    save_config_json,
    load_replace_rules,
    save_replace_rules,
)
from translation_tool.checkers.untranslated_checker import check_untranslated_generator
from translation_tool.checkers.variant_comparator import compare_variants_generator
from translation_tool.checkers.english_residue_checker import check_english_residue_generator
from translation_tool.checkers.variant_comparator_tsv import compare_variants_tsv_generator


# ------------------------------------------------------
# façade helper
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



# ------------------------------------------------------
# pipeline façade exports
# ------------------------------------------------------

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

# PR23：KubeJS UI services 抽離至 app.services_impl.pipelines.kubejs_service。
# 注意：services.py 仍 re-export 同名符號，維持 translation_view.py 的 lazy import 相容。
from app.services_impl.pipelines.kubejs_service import run_kubejs_tooltip_service

# PR24：MD UI services 抽離至 app.services_impl.pipelines.md_service。
# 注意：services.py 仍 re-export 同名符號，維持 translation_view.py 的 lazy import 相容。
from app.services_impl.pipelines.md_service import run_md_translation_service


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


# ------------------------------------------------------
# legacy QC / checkers（暫緩線：先不拆、不刪）
# ------------------------------------------------------

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


# ------------------------------------------------------
# cache façade exports
# ------------------------------------------------------

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

