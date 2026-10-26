"""translation_tool/core/lm_translator_scan.py 模組。

用途：語言檔案掃描與發現功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import orjson as json

from translation_tool.core.guide_translation import (
    GuidePath,
    find_guide_files,
    load_guide,
)
from translation_tool.core.translatable_extractor import (
    extract_translatables,
    find_advancement_json,
    find_lang_json,
    find_patchouli_json,
    is_advancement_file,
    is_lang_file,
)
from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor

from ..utils.log_unit import log_error, log_info, log_warning


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

    回傳：(content_files, lang_files, files)；content_files 包含 Patchouli、指南與 Advancement。
    若掃描過程中發生錯誤，只 log warning 並繼續，回傳空結果。
    """
    try:
        content_files = (
            find_patchouli_json(root)
            + find_guide_files(root)
            + find_advancement_json(root)
        )
        lang_files = find_lang_json(root)
        files = content_files + lang_files
        return content_files, lang_files, files
    except Exception as e:  # noqa: BLE001 - 錯誤已記錄或回報給呼叫端，不中斷整批流程
        log_warning(f"掃描 {root} 時失敗: {e!r}")
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
    items_by_file: dict[str, list[dict[str, Any]]] = {}

    def process_file_task(f: Path):
        try:
            raise_if_cancelled()
            if isinstance(f, GuidePath):
                doc, guide_items = load_guide(f)
                return {"file_path": str(f), "data": doc, "items": guide_items}
            data = json.loads(f.read_bytes())
            raise_if_cancelled()

            # Advancement 必須優先於 Lang。路徑內即使出現名為 ``lang`` 的
            # 子資料夾，也要保留專用抽取器與安全 writer 的完整閉環。
            if is_advancement_file(f):
                c_type = "advancement"
            elif is_lang_file(f):
                c_type = "lang"

                # 若要輸出 .lang，但內容不是純 key->str，就只能退回輸出 json
                if export_lang and not is_plain_lang_json(data):
                    log_info(
                        f"⚠️ Lang 檔為複合格式（含 list/dict），無法輸出 .lang，將改用 .json：{f}"
                    )
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
        except Exception as e:  # noqa: BLE001 - 錯誤已記錄或回報給呼叫端，不中斷整批流程
            log_error(f"❌ 檔案處理失敗 {f.name}: {e!r}")
            return None

    def submit_file(executor, file_path: Path):
        return executor.submit(process_file_task, file_path)

    with (
        ContextThreadPoolExecutor(max_workers=work_thread) as executor,
        bounded_as_completed(
            executor,
            files,
            submit_file,
            max_in_flight=max(1, work_thread * 2),
        ) as completed,
    ):
        for future, _file_path in completed:
            result = future.result()
            if not result:
                continue
            file_cache[result["file_path"]] = result["data"]
            items_by_file[result["file_path"]] = result["items"]
            all_items.extend(result["items"])
            # 每完成一個檔案就 yield，讓 caller 可以更新 UI 進度
            yield file_cache, all_items

    # 完成順序取決於執行緒排程；結束前就地改回輸入檔案順序（呼叫端持有的是同一份
    # list／dict），讓項目順序（批次切分、續跑指紋）在每次執行都相同。
    ordered_cache = {str(f): file_cache[str(f)] for f in files if str(f) in file_cache}
    ordered_items = [item for f in files for item in items_by_file.get(str(f), ())]
    file_cache.clear()
    file_cache.update(ordered_cache)
    all_items[:] = ordered_items
