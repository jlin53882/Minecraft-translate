"""app/services.py

PR29 後此模組僅保留 QC/checkers 暫緩線所需的 façade。

背景：
- PR28a/PR28b 已將非 QC caller 全部搬到 `app.services_impl.*`。
- 因此本檔不再保留 pipeline / cache / config 的 re-export。
- QC/checkers 線仍暫緩，先維持 `qc_view.py -> app.services` 的相容入口。
"""

import logging
import traceback

from app.services_impl.logging_service import GLOBAL_LOG_LIMITER
from translation_tool.checkers.english_residue_checker import (
    check_english_residue_generator,
)
from translation_tool.checkers.untranslated_checker import check_untranslated_generator
from translation_tool.checkers.variant_comparator import compare_variants_generator
from translation_tool.checkers.variant_comparator_tsv import (
    compare_variants_tsv_generator,
)

logger = logging.getLogger(__name__)


__all__ = [
    "run_untranslated_check_service",
    "run_variant_compare_service",
    "run_english_residue_check_service",
    "run_variant_compare_tsv_service",
]


def run_untranslated_check_service(en_dir: str, tw_dir: str, out_dir: str):
    """執行此 generator 並逐步回報進度（yield update dict）。

    - 主要包裝：`check_untranslated_generator`
    """
    try:
        for update_dict in check_untranslated_generator(en_dir, tw_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 未翻譯檢查失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] 未翻譯檢查失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }


def run_variant_compare_service(cn_dir: str, tw_dir: str, out_dir: str):
    """執行此 generator 並逐步回報進度（yield update dict）。

    - 主要包裝：`compare_variants_generator`
    """
    try:
        for update_dict in compare_variants_generator(cn_dir, tw_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 簡繁差異比較失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] 簡繁差異比較失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }


def run_english_residue_check_service(input_dir: str, out_dir: str):
    """執行此 generator 並逐步回報進度（yield update dict）。

    - 主要包裝：`check_english_residue_generator`
    """
    try:
        for update_dict in check_english_residue_generator(input_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 殘留英文檢查失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] 殘留英文檢查失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }


def run_variant_compare_tsv_service(tsv_path: str, output_csv_path: str):
    """執行此 generator 並逐步回報進度（yield update dict）。

    - 主要包裝：`compare_variants_tsv_generator`
    """
    try:
        for update_dict in compare_variants_tsv_generator(tsv_path, output_csv_path):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] TSV 簡繁差異比較失敗：{e}\n{full_traceback}")
        yield {
            "log": f"[致命錯誤] TSV 簡繁差異比較失敗：{e}\n{full_traceback}",
            "error": True,
            "progress": 0,
        }
