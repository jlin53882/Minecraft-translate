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
    r"^(assets|data)/([^/]+)/"
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

def build_lang_file_regex(*, codes: list[str] | None = None, skip_zh_cn: bool = False) -> re.Pattern:
    """根據 lang_codes 動態建 lang file regex。

    Args:
        codes: 指定語言代碼列表，若為 None則從 config 讀取。
        skip_zh_cn: 是否跳過 zh_cn（兩個路徑都生效,不只是 codes=None）。
            設計說明:2026-07-14 user review 發現 production caller
            (extract_lang_files_generator / extract_dual_files_generator)
            總是傳 codes=get_lang_codes() 結果 + skip_zh_cn=True,
            原本只在 codes=None 生效的設計 bug — production 路徑
            完全沒生效。修法:else branch 也尊重 skip_zh_cn,跟 get_lang_codes
            對稱。
    """
    if codes is None:
        codes = get_lang_codes(skip_zh_cn=skip_zh_cn)
    elif skip_zh_cn and "zh_cn" in codes:
        # 🐛 修法:即使 caller 傳了 codes,skip_zh_cn 也要生效
        # (跟 codes=None 路徑的 get_lang_codes(skip_zh_cn=True) 對稱)
        codes = [c for c in codes if c != "zh_cn"]
    codes_str = "|".join(map(re.escape, codes))
    regex = re.compile(rf"(?:assets/([^/]+)/)?lang/({codes_str})\.(json|lang)$", re.IGNORECASE)
    return regex


def build_book_path_regex(*, codes: list[str] | None = None, skip_zh_cn: bool = False) -> re.Pattern:
    """根據 lang_codes 動態建 Book 檔案 regex。

    Args:
        codes: 指定語言代碼列表，若為 None 則從 config 讀取。
        skip_zh_cn: 是否跳過 zh_cn（兩個路徑都生效,不只是 codes=None）。
            設計說明:2026-07-14 user review 補發現 — book 模式也需要跟 lang 模式對稱,
            extract_book_files_generator / preview 內部 caller 總是傳 codes=get_lang_codes() 結果
            + skip_zh_cn=True,跟 build_lang_file_regex 同樣 bug pattern。
            修法:跟 build_lang_file_regex 一致,兩個路徑都尊重 skip_zh_cn。
    """
    if codes is None:
        codes = get_lang_codes(skip_zh_cn=skip_zh_cn)
    elif skip_zh_cn and "zh_cn" in codes:
        # 🐛 修法:即使 caller 傳了 codes,skip_zh_cn 也要生效
        # (跟 codes=None 路徑對稱,跟 build_lang_file_regex 一致)
        codes = [c for c in codes if c != "zh_cn"]
    codes_str = "|".join(map(re.escape, codes))
    return re.compile(
        rf"(?:(?:assets|data)/([^/]+)/"
        rf"(?:patchouli_books|book|manual|guidebook)/"
        rf"(?:([^/]+)/)?"
        # 🐛 2026-07-14 user review 修法:移除 |book\.json fallback
        # 之前 fallback 是讓 <modid>/book.json (沒 lang code) 也算 book,
        # 但 user 明確說「zh_cn 資料夾 後面全部的內容」要跳過 — 既然 fallback 會讓
        # zh_cn/book.json 還是 match,就跟 user 期望衝突。修法後 lang code 必須匹配。
        rf"(_?(?:{codes_str}))(?:/.*)?"
        rf"$)",
        re.IGNORECASE,
    )

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

def extract_lang_files_generator(mods_dir: str, output_dir: str, *, lang_codes: list[str] | None = None, skip_zh_cn: bool = False) -> Generator[Dict[str, Any], None, None]:
    """從 mods 目錄提取語言檔。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        lang_codes: 指定語言代碼列表，若為 None 則從 config 讀取。
        skip_zh_cn: 是否跳過 zh_cn（僅在 lang_codes=None 時生效）。

    Yields:
        進度字典（含 progress、log 等）
    """
    lang_file_regex = build_lang_file_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)
    yield from _run_extraction_process(
        mods_dir=mods_dir,
        output_dir=output_dir,
        target_regex=lang_file_regex,
        process_name="Lang",
    )

def extract_book_files_generator(mods_dir: str, output_dir: str, *, lang_codes: list[str] | None = None, skip_zh_cn: bool = False) -> Generator[Dict[str, Any], None, None]:
    """從 mods 目錄提取 Patchouli 書本檔。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        lang_codes: 指定語言代碼列表，若為 None 則從 config 讀取。
        skip_zh_cn: 是否跳過 zh_cn（2026-07-14 user review 補發現,
            book 模式也支援跟 lang 模式對稱的 skip_zh_cn 過濾）。

    Yields:
        進度字典
    """
    book_regex = build_book_path_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)
    yield from _run_extraction_process(
        mods_dir,
        output_dir,
        book_regex,
        "Patchouli Book",
    )

def extract_dual_files_generator(
    mods_dir: str,
    output_dir: str,
    *,
    lang_codes: list[str] | None = None,
    skip_zh_cn: bool = False,
) -> Generator[Dict[str, Any], None, None]:
    """從 mods 目錄依序提取語言檔與書本檔（dual 模式）。

    Args:
        mods_dir: Mod 目錄路徑
        output_dir: 輸出目錄路徑
        lang_codes: 指定語言代碼列表,若為 None 則從 config 讀取。
        skip_zh_cn: 是否跳過 zh_cn 抽取（僅在 lang_codes=None 時生效）。

    Yields:
        進度字典
    """
    lang_file_regex = build_lang_file_regex(codes=lang_codes, skip_zh_cn=skip_zh_cn)
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
                yield {**update, "phase": "lang"}
    except Exception as e:
        lang_error = str(e)
    if lang_stats:
        yield {"phase": "lang", "stats": lang_stats}
    yield {"phase": "book", "log": "[系統] Lang 提取完成，開始提取 Book..."}
    book_error = None
    last_book_stats = None
    try:
        for update in _run_extraction_process(
            mods_dir,
            output_dir,
            BOOK_PATH_REGEX_DUAL_STRUCTURE,
            "Patchouli Book",
        ):
            if "stats" in update:
                book_stats = update["stats"]
                last_book_stats = book_stats
                if lang_stats:
                    # 🐛 Phase 3 (2026-07-13 user 選項 B fix): 原本 yield combined
                    # (lang+book 加總),但這讓 run_extraction_loop 把 combined 寫進
                    # stats["book"] sub-dict,user 看到「BOOK 成功 13」其實是 lang+book 合計,
                    # 誤導。
                    # 改成 yield 純 book_stats 給 phase sub-dict,頂層合計由最終 combined yield 處理。
                    yield {**update, "stats": book_stats, "phase": "book"}
                else:
                    # Lang 階段無 stats（空 JAR 目錄無 lang 檔），Book 仍要 yield book stats，
                    # 否則 UI 統計徽章永遠顯示 0/0/0
                    yield {**update, "stats": book_stats, "phase": "book"}
            else:
                yield {**update, "phase": "book"}
    except Exception as e:
        book_error = str(e)
    # Phase 3 fix: book phase 結束後補一個 combined yield(無 phase,不污染 sub-dict),
    # 確保 run_extraction_loop 頂層 stats["success"] 是 lang+book 合計。
    if lang_stats and last_book_stats:
        combined = {
            "success": lang_stats["success"] + last_book_stats["success"],
            "failures": lang_stats["failures"] + last_book_stats["failures"],
            "warnings": lang_stats["warnings"] + last_book_stats["warnings"],
            "total_files": lang_stats["total_files"] + last_book_stats["total_files"],
            "lang": lang_stats,
            "book": last_book_stats,
        }
        # 🐛 Phase 3 (2026-07-13 user 選項 B fix): phase="book_final" 不在 run_extraction_loop
        # 拆解範圍 ("lang","book"),所以 combined 只更新頂層 stats["success"] 等,
        # 不會覆寫 stats["book"] sub-dict,避免 BOOK row 顯示 lang+book 合計。
        yield {"stats": combined, "phase": "book_final"}
    if lang_error or book_error:
        yield {"dual_errors": {"lang": lang_error, "book": book_error}, "error": True}

def preview_extraction_generator(
    mods_dir: str,
    mode: str,
    lang_codes: list[str] | None = None,
    skip_zh_cn: bool = False,
) -> Generator[Dict[str, Any], None, None]:
    """預覽提取結果。

    Args:
        mods_dir: Mod 目錄路徑
        mode: 預覽模式
        lang_codes: 指定語言代碼列表，若為 None 則從 config 讀取
        skip_zh_cn: 是否跳過 zh_cn（preview 路徑也支援,
            跟 extract 路徑行為一致 — 2026-07-14 user review 補發現）

    Yields:
        進度字典
    """
    yield from preview_extraction_generator_impl(
        mods_dir,
        mode,
        find_jar_files_fn=find_jar_files,
        book_path_regex=BOOK_PATH_REGEX_DUAL_STRUCTURE,
        lang_codes=lang_codes,
        skip_zh_cn=skip_zh_cn,
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
