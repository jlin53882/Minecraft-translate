"""translation_tool/core/jar_processor.py 模組。

用途：作為 jar discovery / extract / preview 的相容入口。
維護注意：主要實作已拆到 jar_processor_discovery / extract / preview 子模組。
"""

import re
from pathlib import Path
from typing import Dict, Any, Generator

from translation_tool.core.jar_processor_discovery import find_jar_files
from translation_tool.utils.config_manager import load_config
from translation_tool.core.jar_processor_extract import (
    extract_from_jar_impl,
    run_extraction_process_impl,
)
from translation_tool.core.jar_processor_preview import (
    ExtractionSummary,
    generate_preview_report,
    preview_extraction_generator_impl,
)

BOOK_PATH_REGEX_DUAL_STRUCTURE = re.compile(
    r"(assets|data)/([^/]+)/"
    r"(patchouli_books|book|manual|guidebook)/"
    r"(?:([^/]+)/)?"
    r"(?:"
    r"(_?(?:en_us|zh_tw|zh_cn))(/.*)?"
    r"|"
    r"book\.json"
    r")$",
    re.IGNORECASE,
)

def get_lang_codes(*, skip_zh_cn: bool = False) -> list[str]:
    """從 config 取得 jar_extractor.lang_codes，預設 ["en_us", "zh_tw", "zh_cn"]。
    
    Args:
        skip_zh_cn: 是否跳過 zh_cn（從 extractor.skip_zh_cn_extract 讀取）。
    
    回傳值保證為非空 list。
    """
    cfg = load_config()
    codes = cfg.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_tw", "zh_cn"])
    if skip_zh_cn and "zh_cn" in codes:
        codes = [c for c in codes if c != "zh_cn"]
    if not isinstance(codes, list) or not codes:
        codes = ["en_us", "zh_tw", "zh_cn"]
    return codes

def build_lang_file_regex(*, skip_zh_cn: bool = False) -> re.Pattern:
    """根據 config 中的 lang_codes 動態建 lang file regex。
    
    確保與 preview 使用的 regex 行為一致。
    """
    codes = get_lang_codes(skip_zh_cn=skip_zh_cn)
    codes_str = "|".join(map(re.escape, codes))
    return re.compile(rf"(?:assets/([^/]+)/)?lang/({codes_str})\.(json|lang)$", re.IGNORECASE)

def _extract_from_jar(
    jar_path: str,
    output_root: str,
    target_regex: re.Pattern,
    all_scan_results: dict[Path, dict[str, str | None]] | None = None,
) -> Dict[str, Any]:
    """從 JAR 檔案提取檔案。

    Args:
        jar_path: JAR 檔案路徑
        output_root: 輸出根目錄
        target_regex: 目標檔案正規表達式
        all_scan_results: 預先掃描的 JAR 結果（由 caller 傳入）
    """
    return extract_from_jar_impl(jar_path, output_root, target_regex, scan_results=all_scan_results)

def _run_extraction_process(
    mods_dir: str, output_dir: str, target_regex: re.Pattern, process_name: str
) -> Generator[Dict[str, Any], None, None]:
    """執行提取流程的 generator。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        target_regex: 目標檔案正規表達式
        process_name: 處理名稱（如 "Lang"、"Patchouli Book"）

    Yields:
        進度字典
    """
    yield from run_extraction_process_impl(
        mods_dir,
        output_dir,
        target_regex,
        process_name,
        find_jar_files_fn=find_jar_files,
        extract_from_jar_fn=_extract_from_jar,
    )

def extract_lang_files_generator(mods_dir: str, output_dir: str, *, skip_zh_cn: bool = False) -> Generator[Dict[str, Any], None, None]:
    """從 mods 目錄提取語言檔。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        skip_zh_cn: 是否跳過 zh_cn 抽取

    Yields:
        進度字典
    """
    lang_file_regex = build_lang_file_regex(skip_zh_cn=skip_zh_cn)
    yield from _run_extraction_process(
        mods_dir=mods_dir,
        output_dir=output_dir,
        target_regex=lang_file_regex,
        process_name="Lang",
    )

def extract_book_files_generator(mods_dir: str, output_dir: str) -> Generator[Dict[str, Any], None, None]:
    """從 mods 目錄提取 Patchouli 書本檔。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑

    Yields:
        進度字典
    """
    yield from _run_extraction_process(
        mods_dir,
        output_dir,
        BOOK_PATH_REGEX_DUAL_STRUCTURE,
        "Patchouli Book",
    )

def extract_dual_files_generator(mods_dir: str, output_dir: str, *, skip_zh_cn: bool = False) -> Generator[Dict[str, Any], None, None]:
    """從 mods 目錄依序提取語言檔與書本檔（dual 模式）。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        skip_zh_cn: 是否跳過 zh_cn 抽取

    Yields:
        進度字典
    """
    lang_file_regex = build_lang_file_regex(skip_zh_cn=skip_zh_cn)
    lang_error = None
    lang_stats = None
    try:
        for update in _run_extraction_process(
            mods_dir=mods_dir,
            output_dir=output_dir,
            target_regex=lang_file_regex,
            process_name="Lang",
        ):
            if "stats" in update:
                lang_stats = update["stats"]
                yield {**update, "phase": "lang"}
            else:
                yield update
    except Exception as e:
        lang_error = str(e)
    if lang_stats:
        yield {"phase": "lang", "stats": lang_stats}
    yield {"phase": "book", "log": "[系統] Lang 提取完成，開始提取 Book..."}
    book_error = None
    try:
        for update in _run_extraction_process(
            mods_dir,
            output_dir,
            BOOK_PATH_REGEX_DUAL_STRUCTURE,
            "Patchouli Book",
        ):
            if "stats" in update:
                book_stats = update["stats"]
                if lang_stats:
                    combined = {
                        "success": lang_stats["success"] + book_stats["success"],
                        "failures": lang_stats["failures"] + book_stats["failures"],
                        "warnings": lang_stats["warnings"] + book_stats["warnings"],
                        "total_files": lang_stats["total_files"] + book_stats["total_files"],
                        "lang": lang_stats,
                        "book": book_stats,
                    }
                    yield {**update, "stats": combined, "phase": "book"}
                else:
                    yield {**update, "phase": "book"}
            else:
                yield update
    except Exception as e:
        book_error = str(e)
    if lang_error or book_error:
        yield {"dual_errors": {"lang": lang_error, "book": book_error}, "error": True}

def preview_extraction_generator(mods_dir: str, mode: str) -> Generator[Dict[str, Any], None, None]:
    """預覽提取結果。

    Args:
        mods_dir: Mod 目錄路徑
        mode: 預覽模式

    Yields:
        進度字典
    """
    yield from preview_extraction_generator_impl(
        mods_dir,
        mode,
        find_jar_files_fn=find_jar_files,
        book_path_regex=BOOK_PATH_REGEX_DUAL_STRUCTURE,
    )

__all__ = [
    "find_jar_files",
    "_extract_from_jar",
    "_run_extraction_process",
    "extract_lang_files_generator",
    "extract_book_files_generator",
    "preview_extraction_generator",
    "ExtractionSummary",
    "generate_preview_report",
    "BOOK_PATH_REGEX_DUAL_STRUCTURE",
    "get_lang_codes",
    "build_lang_file_regex",
]
