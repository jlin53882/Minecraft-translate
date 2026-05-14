"""translation_tool/core/output_bundler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/core/output_bundler.py (新檔案)

import os
import zipfile
import logging
import time
import json
from typing import Dict, Any, Generator, Optional, List

from ..utils.config_manager import load_config

log = logging.getLogger(__name__)


def _add_folder_to_zip(
    zip_file: zipfile.ZipFile,
    folder_path: str,
    base_path_in_zip: str,
    seen_files: Optional[dict] = None,
) -> tuple[int, dict]:
    """Add folder contents to ZIP with duplicate handling.

    Returns (added_count, seen_files) where seen_files maps archive_name to count.
    """
    added_count = 0
    if seen_files is None:
        seen_files = {}

    if not os.path.exists(folder_path):
        log.warning(f"打包時找不到來源資料夾: {folder_path}，將略過。")
        return 0, seen_files

    for root, _, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, folder_path)
            archive_name = os.path.join(base_path_in_zip, relative_path).replace("\\", "/")

            if archive_name in seen_files:
                base, ext = os.path.splitext(archive_name)
                counter = 1
                while f"{base}_{counter}{ext}" in seen_files:
                    counter += 1
                archive_name = f"{base}_{counter}{ext}"

            seen_files[archive_name] = 1
            zip_file.write(file_path, archive_name)
            added_count += 1

    return added_count, seen_files


def _write_pack_mcmeta(
    zip_file: zipfile.ZipFile,
    description: str,
    min_format: int,
    max_format: int,
) -> None:
    """Write pack.mcmeta file to ZIP root."""
    pack_info = {
        "pack": {
            "description": description,
            "min_format": str(min_format),
            "max_format": str(max_format),
        }
    }

    zip_file.writestr(
        "pack.mcmeta",
        json.dumps(pack_info, ensure_ascii=False, indent=2),
    )


def bundle_outputs_generator(
    input_root_dir: str,
    output_zip_path: str,
    description: str = "",
    min_format: int = 0,
    max_format: int = 0,
    pack_image_path: Optional[str] = None,
    extra_folders: Optional[List[str]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Generator that bundles output folders into a ZIP archive.

    Args:
        input_root_dir: Root folder containing source subfolders
        output_zip_path: Output path for ZIP file
        description: pack.mcmeta description field
        min_format: min_format for pack.mcmeta
        max_format: max_format for pack.mcmeta
        pack_image_path: Optional path to pack.png to copy into ZIP root
        extra_folders: Optional list of extra folder paths to merge into ZIP root
    """
    start_time = time.time()

    if not os.path.exists(input_root_dir):
        yield {"progress": 1.0, "log": f"錯誤：輸入目錄不存在: {input_root_dir}", "error": True}
        return

    subfolders = [d for d in os.listdir(input_root_dir)
                  if os.path.isdir(os.path.join(input_root_dir, d))]

    if not subfolders:
        yield {"progress": 1.0, "log": f"錯誤：輸入目錄中沒有子資料夾: {input_root_dir}", "error": True}
        return

    total_files_added = 0
    yield {"progress": 0.0, "log": f"開始建立 ZIP 檔案於: {output_zip_path}"}

    seen_files: dict = {}

    try:
        with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            if description or min_format > 0:
                _write_pack_mcmeta(zf, description, min_format, max_format)
                total_files_added += 1
                yield {"progress": 0.05, "log": "已寫入 pack.mcmeta"}

            if pack_image_path and os.path.exists(pack_image_path):
                try:
                    ext = os.path.splitext(pack_image_path)[1].lower()
                    if ext in (".png", ".jpg", ".jpeg"):
                        dest_name = "pack.png"
                        if dest_name in seen_files:
                            dest_name = "pack_1.png"
                        seen_files[dest_name] = 1
                        with open(pack_image_path, "rb") as src:
                            zf.writestr(dest_name, src.read())
                        total_files_added += 1
                        yield {"progress": 0.1, "log": f"已複製資源包圖片: {dest_name}"}
                except Exception as ex:
                    yield {"progress": 0.1, "log": f"複製 pack.png 失敗: {ex}"}

            total_steps = len(subfolders) + (len(extra_folders) if extra_folders else 0)
            step = 0

            for folder_name in subfolders:
                step += 1
                progress = 0.15 + (step / total_steps) * 0.7
                full_source_path = os.path.join(input_root_dir, folder_name)
                yield {"progress": progress, "log": f"正在掃描來源: '{folder_name}'..."}

                base = "" if folder_name.lower() == "root" else folder_name
                count, seen_files = _add_folder_to_zip(zf, full_source_path, base, seen_files)

                if count > 0:
                    total_files_added += count
                    yield {"progress": progress, "log": f"成功從 '{folder_name}' 加入 {count} 個檔案。"}
                else:
                    yield {"progress": progress, "log": f"在 '{folder_name}' 中未找到可打包的檔案。"}

            if extra_folders:
                for extra_path in extra_folders:
                    step += 1
                    progress = 0.15 + (step / total_steps) * 0.7
                    yield {"progress": progress, "log": f"正在處理額外資料夾: '{extra_path}'..."}

                    if not os.path.exists(extra_path):
                        yield {"progress": progress, "log": f"額外資料夾不存在: '{extra_path}'"}
                        continue

                    parent_name = os.path.basename(extra_path.rstrip("/\\"))
                    for entry in os.listdir(extra_path):
                        src = os.path.join(extra_path, entry)
                        if os.path.isdir(src):
                            count, seen_files = _add_folder_to_zip(zf, src, "", seen_files)
                            total_files_added += count
                            yield {"progress": progress, "log": f"額外資料夾 '{parent_name}/{entry}': +{count} 個檔案"}
                        elif os.path.isfile(src):
                            archive_name = entry
                            if archive_name in seen_files:
                                base, ext = os.path.splitext(archive_name)
                                counter = 1
                                while f"{base}_{counter}{ext}" in seen_files:
                                    counter += 1
                                archive_name = f"{base}_{counter}{ext}"
                            seen_files[archive_name] = 1
                            zf.write(src, archive_name)
                            total_files_added += 1
                            yield {"progress": progress, "log": f"額外檔案: +1 ({entry})"}

        duration = time.time() - start_time
        yield {"progress": 1.0, "log": f"--- 打包完成！總共 {total_files_added} 個檔案被加入 ZIP。耗時 {duration:.2f} 秒 ---"}

    except Exception as e:
        log.error(f"打包時發生嚴重錯誤: {e}", exc_info=True)
        yield {"progress": 1.0, "log": f"錯誤：打包失敗: {e}", "error": True}
        if os.path.exists(output_zip_path):
            os.remove(output_zip_path)