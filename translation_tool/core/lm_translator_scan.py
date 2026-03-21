"""translation_tool/core/lm_translator_scan.py 模組。

用途：語言檔案掃描與發現功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any, Generator

import orjson as json

from ..utils.log_unit import log_info, log_warning, log_error
from translation_tool.core.translatable_extractor import (
    extract_translatables,
    find_lang_json,
    find_patchouli_json,
    is_lang_file,
)

def is_plain_lang_json(data: dict) -> bool:
    """判斷是否為純 lang JSON（key: str -> value: str）。

    只要出現非 str 的 value，就視為複合格式（例如 list/dict），不符合輸出 `.lang` 的條件。
    """

    if not isinstance(data, dict):
        return False

    for v in data.values():
        if not isinstance(v, str):
            return False

    return True

def scan_translatable_files(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """掃描 root 下可翻譯 JSON 檔案。

    回傳：(patchouli_files, lang_files, files)
    若掃描過程中發生錯誤，只 log warning 並繼續，回傳空結果。
    """
    try:
        patchouli_files = find_patchouli_json(root)
        lang_files = find_lang_json(root)
        files = patchouli_files + lang_files
        return patchouli_files, lang_files, files
    except Exception as e:
        log_warning(f"掃描 {root} 時失敗: {e}")
        return [], [], []

def extract_items_parallel(
    *,
    files: list[Path],
    export_lang: bool,
    work_thread: int,
    logger=None,
) -> Generator[tuple[dict[str, dict], list[dict[str, Any]]], None, None]:
    """使用執行緒池並行讀取多個 JSON 檔案並抽取可翻譯項目。

    身為 generator，每完成一個檔案就 yield 一次 (file_cache, all_items)，
    讓子呼叫端可以追蹤進度並回傳給 UI。
    """

    file_cache: dict[str, dict] = {}
    all_items: list[dict[str, Any]] = []

    def process_file_task(f: Path):
        try:
            data = json.loads(f.read_bytes())

            if is_lang_file(f):
                c_type = "lang"

                # ⭐ 若要輸出 .lang，但內容不是純 key->str，就只能退回輸出 json
                if export_lang and not is_plain_lang_json(data):
                    log_info(f"⚠️ Lang 檔為複合格式（含 list/dict），無法輸出 .lang，將改用 .json：{f}")
            else:
                c_type = "patchouli"

            extracted_items = extract_translatables(data, f)
            for item in extracted_items:
                item["cache_type"] = c_type

            return {
                "file_path": str(f),
                "data": data,
                "items": extracted_items,
            }
        except Exception as e:
            log_error(f"❌ 檔案處理失敗 {f.name}: {e}")
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=work_thread) as executor:
        future_to_file = {executor.submit(process_file_task, f): f for f in files}
        for future in concurrent.futures.as_completed(future_to_file):
            result = future.result()
            if not result:
                continue
            file_cache[result["file_path"]] = result["data"]
            all_items.extend(result["items"])
            # 每完成一個檔案就 yield，讓 caller 可以更新 UI 進度
            yield file_cache, all_items
