"""translation_tool/core/lang_merge_pending.py 模組。

用途：待翻譯語言檔案的處理功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
import shutil

from ..utils.log_unit import log_warning


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
    """增量更新條目數達門檻的 pending JSON。

    整理目錄是 pending 的衍生視圖，不需要每次整個刪除重建。以來源檔案
    的相對路徑、大小與修改時間判斷是否變更；未變更檔案直接跳過，只有
    新增/變更/低於門檻的檔案才會讀取或更新。
    """
    if not os.path.isdir(pending_root):
        return
    os.makedirs(output_root, exist_ok=True)

    min_count = int(min_count)
    eligible_paths: set[str] = set()

    for dirpath, _, filenames in os.walk(pending_root):
        for filename in filenames:
            if not filename.lower().endswith(".json"):
                continue
            pending_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(pending_path, pending_root).lstrip(os.sep)
            out_path = os.path.join(output_root, rel_path)
            data = None
            try:
                source_stat = os.stat(pending_path)
                if os.path.isfile(out_path):
                    output_stat = os.stat(out_path)
                    if (
                        source_stat.st_size == output_stat.st_size
                        and source_stat.st_mtime_ns == output_stat.st_mtime_ns
                    ):
                        # mtime/size 只能判斷來源內容未變更，不能判斷本次
                        # filtered_pending_min_count 是否改變；門檻提高時仍
                        # 必須重新計算條目數，避免保留不再符合的整理檔。
                        with open(pending_path, "rb") as f:
                            data = json_module.loads(f.read())
                        try:
                            if len(data) >= min_count:
                                eligible_paths.add(rel_path)
                                continue
                        except TypeError:
                            pass
                if data is None:
                    with open(pending_path, "rb") as f:
                        raw = f.read()
                        data = json_module.loads(raw)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"略過無法讀取的待翻譯檔 {pending_path}: {exc!r}")
                continue

            try:
                data_count = len(data)
            except TypeError:
                data_count = 0
            if data_count >= min_count:
                eligible_paths.add(rel_path)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                # 保留來源 bytes 與修改時間，避免第二次 JSON 序列化。
                shutil.copy2(pending_path, out_path)

    # 只移除過期 JSON，不刪除整個輸出目錄或其他非 JSON 檔案。
    for dirpath, _, filenames in os.walk(output_root, topdown=False):
        for filename in filenames:
            if not filename.lower().endswith(".json"):
                continue
            out_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(out_path, output_root).lstrip(os.sep)
            if rel_path not in eligible_paths:
                try:
                    os.remove(out_path)
                except OSError as exc:
                    log_warning(f"刪除過期整理檔失敗 {out_path}: {exc}")
        if dirpath != output_root:
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass
