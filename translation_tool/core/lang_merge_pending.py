"""translation_tool/core/lang_merge_pending.py 模組。

用途：待翻譯語言檔案的處理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
from ..utils.log_unit import log_info, log_warning, log_error, log_debug

import shutil


def remove_empty_dirs_impl(root_dir: str, *, logger_override=None) -> None:
    """遞迴刪除空資料夾。"""
    # 使用 centralized logger，logger_override 參數保留相容性
    if not os.path.exists(root_dir):
        return
    for dirpath, _, _ in os.walk(root_dir, topdown=False):
        if dirpath == root_dir:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError as e:
            log_warning(f"刪除空目錄失敗 {dirpath}: {e}")

def export_filtered_pending_impl(
    pending_root: str,
    output_root: str,
    min_count: int,
    *,
    json_module,
) -> None:
    """輸出符合門檻的 pending.json，並先清掉舊輸出。"""
    if not os.path.isdir(pending_root):
        return
    if os.path.exists(output_root):
        shutil.rmtree(output_root)
    os.makedirs(output_root, exist_ok=True)

    for dirpath, _, filenames in os.walk(pending_root):
        for filename in filenames:
            if not filename.lower().endswith(".json"):
                continue
            pending_path = os.path.join(dirpath, filename)
            try:
                with open(pending_path, "rb") as f:
                    data = json_module.loads(f.read())
            except Exception:
                continue
            if len(data) >= int(min_count):
                rel_path = os.path.relpath(pending_path, pending_root).lstrip(os.sep)
                out_path = os.path.join(output_root, rel_path)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(json_module.dumps(data, option=json_module.OPT_INDENT_2))
