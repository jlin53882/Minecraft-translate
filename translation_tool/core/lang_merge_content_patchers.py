"""translation_tool/core/lang_merge_content_patchers.py 模組。

用途：語言檔案的修補與修改功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
import zipfile
from typing import Any, Dict, Callable

from ..utils.log_unit import log_info, log_warning, log_error, log_debug


def patch_localized_content_json_impl(
    reader,
    cn_path: str,
    tw_output_path: str,
    rules: list,
    log_prefix: str,
    output_dir: str,
    *,
    recursive_translate_dict_fn: Callable[[Any, list], Any],
    quarantine_copy_fn: Callable[..., Any],
    json_module,
    logger_override=None,
) -> Dict[str, Any]:
    """將本地化 JSON 檔案（zh_cn）讀取後套用 S2TW 翻譯規則，以格式化 JSON 寫入 tw_output_path；若解析失敗則隔離至 quarantine。"""
    try:
        raw_text = reader.read_text(cn_path)

        try:
            cn_data = json_module.loads(raw_text)
        except Exception as e:
            log_warning(f"{log_prefix} zh_cn JSON 無法解析，已跳過該檔案: {e}")
            quarantine_copy_fn(
                reader=reader,
                rel_path=cn_path,
                output_dir=output_dir,
                reason=f"json_parse_failed: {type(e).__name__}: {e}",
            )
            return {
                "success": True,
                "pending_count": 0,
            }

        translated_data = recursive_translate_dict_fn(cn_data, rules)
        new_content_bytes = json_module.dumps(translated_data, option=json_module.OPT_INDENT_2)

        should_write = True
        log_msg = None

        if os.path.exists(tw_output_path):
            try:
                with open(tw_output_path, "rb") as f:
                    existing_raw = f.read().decode("utf-8")
                try:
                    existing_data = json_module.loads(existing_raw)
                except Exception:
                    log_warning(f"{log_prefix} TW JSON 無法解析，將覆蓋修復")
                    existing_data = None

                if existing_data is not None:
                    existing_normalized_bytes = json_module.dumps(
                        existing_data, option=json_module.OPT_INDENT_2
                    )
                    if new_content_bytes == existing_normalized_bytes:
                        should_write = False
            except Exception as e:
                log_warning(f"{log_prefix} 無法載入現有 TW 檔案 ({e})，將覆蓋寫入")

        if should_write:
            os.makedirs(os.path.dirname(tw_output_path), exist_ok=True)
            with open(tw_output_path, "wb") as f:
                f.write(new_content_bytes)
            log_msg = f"{log_prefix} 內容 JSON 已 S2TW 轉換並寫入（格式化）"
        else:
            log_debug(f"{log_prefix} 內容 JSON 無變動，略過寫入")

        log_info(log_msg)
        return {
            "success": True,
            "pending_count": 0,
        }
    except Exception as exc:
        log_error(f"處理內容 JSON 檔案 {cn_path} 發生錯誤: {exc}", exc_info=True)
        return {
            "success": False,
            "error": True,
        }
