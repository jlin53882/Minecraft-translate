"""translation_tool/core/output_bundler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/translation_tool/core/output_bundler.py (新檔案)

import os
import zipfile
import logging
import time
from typing import Dict, Any, Generator

# --- 導入我們自訂的工具 ---
from ..utils.config_manager import load_config

log = logging.getLogger(__name__)

def _add_folder_to_zip(zip_file: zipfile.ZipFile, folder_path: str, base_path_in_zip: str) -> int:
    """
    將一個資料夾中的所有內容（含子資料夾）加入到 ZIP 檔案中。
    
    :param zip_file: zipfile.ZipFile 物件
    :param folder_path: 要加入的來源資料夾 (例如 "output/zh_tw_generated")
    :param base_path_in_zip: 檔案在 ZIP 中應有的基礎路徑 (例如 "assets")
    :return: 成功加入的檔案數量
    """
    added_count = 0
    if not os.path.exists(folder_path):
        log.warning(f"打包時找不到來源資料夾: {folder_path}，將略過。")
        return 0

    for root, _, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            # 計算檔案在 ZIP 檔案中的相對路徑
            # 例如: "output/zh_tw_generated/assets/modid/lang/zh_tw.json"
            # -> "assets/modid/lang/zh_tw.json"
            relative_path = os.path.relpath(file_path, folder_path)
            # 組合成最終在 ZIP 中的路徑
            archive_name = os.path.join(base_path_in_zip, relative_path)
            
            # 將反斜線替換為正斜線，確保 ZIP 格式的相容性
            archive_name = archive_name.replace('\\', '/')
            
            zip_file.write(file_path, archive_name)
            added_count += 1
            
    return added_count

def bundle_outputs_generator(input_root_dir: str, output_zip_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    (核心打包函式) 
    根據 load_config().json 的設定，從多個來源資料夾收集檔案，
    並打包成一個單一的 .zip 檔案。
    """
    start_time = time.time()
    
    # 讀取打包設定
    bundle_cfg = load_config().get("output_bundler", {})
    source_folders_map = bundle_cfg.get("source_folders", {})
    
    if not source_folders_map:
        yield {"progress": 1.0, "log": "錯誤：load_config().json 中未定義 'output_bundler.source_folders'。無法打包。", "error": True}
        return

    total_files_added = 0
    yield {"progress": 0.0, "log": f"開始建立 ZIP 檔案於: {output_zip_path}"}

    try:
        with zipfile.ZipFile(output_zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            
            total_steps = len(source_folders_map)
            step = 0
            
            # 遍歷 config 中定義的所有來源
            # 例如: "assets": "zh_tw_generated"
            # "root_files": "pack_mcmeta"
            for base_path_in_zip, folder_name in source_folders_map.items():
                step += 1
                progress = step / total_steps
                
                # 來源資料夾的完整路徑
                full_source_path = os.path.join(input_root_dir, folder_name)
                
                yield {"progress": progress - 0.1, "log": f"正在掃描來源: '{folder_name}'..."}
                
                if not os.path.exists(full_source_path):
                    log_msg = f"警告：找不到來源資料夾 '{folder_name}' (路徑: {full_source_path})，將略過。"
                    yield {"progress": progress, "log": log_msg}
                    continue

                # 'root' 是一個特殊鍵，表示裡面的檔案應放在 ZIP 的根目錄
                if base_path_in_zip.lower() == 'root':
                    base_path_in_zip = '' # 設為空字串

                count = _add_folder_to_zip(zf, full_source_path, base_path_in_zip)
                
                if count > 0:
                    log_msg = f"成功從 '{folder_name}' 加入 {count} 個檔案到 '{base_path_in_zip}/'。"
                    total_files_added += count
                    yield {"progress": progress, "log": log_msg}
                else:
                    yield {"progress": progress, "log": f"在 '{folder_name}' 中未找到可打包的檔案。"}

        duration = time.time() - start_time
        yield {"progress": 1.0, "log": f"--- 打包完成！總共 {total_files_added} 個檔案被加入 ZIP。耗時 {duration:.2f} 秒 ---"}

    except Exception as e:
        log.error(f"打包時發生嚴重錯誤: {e}", exc_info=True)
        yield {"progress": 1.0, "log": f"錯誤：打包失敗: {e}", "error": True}
        # 如果失敗，嘗試刪除不完整的 ZIP 檔案
        if os.path.exists(output_zip_path):
            os.remove(output_zip_path)