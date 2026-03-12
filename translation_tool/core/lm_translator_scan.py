from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

import orjson as json

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
    """

    patchouli_files = find_patchouli_json(root)
    lang_files = find_lang_json(root)
    files = patchouli_files + lang_files
    return patchouli_files, lang_files, files


def extract_items_parallel(
    *,
    files: list[Path],
    export_lang: bool,
    work_thread: int,
    logger,
) -> tuple[dict[str, dict], list[dict[str, Any]]]:
    """並行讀取/解析/抽取可翻譯文字。

    - 回傳：
      - file_cache：key=檔案路徑字串，value=原始 json dict
      - all_items：抽取後的 items（已含 cache_type 標籤）

    注意：為了保留既有行為與 log，這裡保留與原本 lm_translator.py 相同的處理策略。
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
                    logger.info(f"⚠️ Lang 檔為複合格式（含 list/dict），無法輸出 .lang，將改用 .json：{f}")
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
            logger.error(f"❌ 檔案處理失敗 {f.name}: {e}")
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=work_thread) as executor:
        future_to_file = {executor.submit(process_file_task, f): f for f in files}
        for future in concurrent.futures.as_completed(future_to_file):
            result = future.result()
            if not result:
                continue
            file_cache[result["file_path"]] = result["data"]
            all_items.extend(result["items"])

    return file_cache, all_items
