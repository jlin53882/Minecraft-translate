"""translation_tool/core/lang_merger.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import zipfile
from collections import defaultdict
from typing import Any, Dict, Generator, List

from ..utils.config_manager import load_config
from ..utils.text_processor import load_replace_rules
from .lang_merge_content import _process_content_or_copy_file, export_filtered_pending, remove_empty_dirs
from .lang_merge_pipeline import _process_single_mod

logger = logging.getLogger(__name__)

def merge_zhcn_to_zhtw_from_zip(zip_file: str, output_dir: str,only_process_lang: bool = False ) -> Generator[Dict[str, Any], None, None]:
    """將 ZIP 檔案中的簡體中文合併為繁體中文。

    Args:
        zip_file: 輸入的 ZIP 檔案路徑
        output_dir: 輸出目錄路徑
        only_process_lang: 是否只處理 lang 檔案

    Yields:
        進度字典，包含 progress、log、error 等資訊

    Note:
        負責掃描 ZIP、分類每個 mod 的 zh_cn/zh_tw/en_us、
        決定各模組執行哪些步驟，最終回傳產生的 log/progress
    """
    os.makedirs(output_dir, exist_ok=True)
    must_translate_dir = os.path.join(output_dir, load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯"))
    os.makedirs(must_translate_dir, exist_ok=True)

    try:
        rules = load_replace_rules(load_config().get("replace_rules_path", "replace_rules.json"))
    except Exception as e:
        logger.error(f"載入替換規則失敗: {e}")
        yield {"progress": 0.0, "error": True}
        return

    # --- 新增：檢查 ZIP 檔案是否存在 ---
    if not os.path.exists(zip_file):
        full_path = os.path.abspath(zip_file) # 取得絕對路徑，方便除錯
        logger.warning(f"檔案不存在，已跳過: {full_path}")
        yield {
            "progress": 1.0, 
            #"log": f"跳過：找不到檔案 {full_path}", 
            "error": False  # 設為 False 是為了讓程式繼續執行下一個任務而不中斷
        }
        return # 直接結束這個產生器，不執行後面的 ZipFile 開啟動作
    # --------------------------------

    try:
        with zipfile.ZipFile(zip_file, 'r') as zf:
            yield {"progress": 0.0, "log": f"分析 ZIP 檔案: {os.path.basename(zip_file)}"}

            # 建立模組索引：以 mod_key 為單位，收集該 mod 下的 zh_cn/zh_tw/en_us 路徑
            lang_files_by_mod = defaultdict(dict)
            other_files: List[str] = []
            #for file_path in zf.namelist():
            #    normalized = file_path.replace('\\', '/')
            #    if normalized.endswith('/') or normalized == '':
            #        continue
            #    # 標準 /lang/*.json 的處理
            #    #if '/lang/' in normalized and normalized.endswith('.json'):
            #    if '/lang/' in normalized and (normalized.endswith('.json') or normalized.endswith('.lang')):
            #        # mod_key 用來區分不同模組的 lang 資料夾
            #        mod_key = normalized.split('/lang/')[0] + '/lang/'
            #        if normalized.endswith('zh_cn.json') or normalized.endswith('zh_cn.lang'):
            #            #lang_files_by_mod[normalized.split('/lang/')[0] + '/lang/']['zh_cn'] = normalized
            #            lang_files_by_mod[mod_key]['zh_cn'] = normalized
            #        elif normalized.endswith('zh_tw.json') or normalized.endswith('zh_tw.lang'):
            #            #lang_files_by_mod[normalized.split('/lang/')[0] + '/lang/']['zh_tw'] = normalized
            #            lang_files_by_mod[mod_key]['zh_tw'] = normalized
            #        elif normalized.endswith('en_us.json') or normalized.endswith('en_us.lang'):
            #            #lang_files_by_mod[normalized.split('/lang/')[0] + '/lang/']['en_us'] = normalized
            #            lang_files_by_mod[mod_key]['en_us'] = normalized
            #        #else:
            #            # 其他 lang json
            #        #    other_files.append(normalized)
            #    else:
            #        other_files.append(normalized)
                    

            for file_path in zf.namelist():
                normalized = file_path.replace("\\", "/")
                if normalized.endswith("/") or not normalized:
                    continue
                
                norm_low = normalized.lower()

                if "/lang/" in norm_low and (norm_low.endswith(".json") or norm_low.endswith(".lang")):
                    mod_key = normalized.split("/lang/")[0] + "/lang/"

                    if norm_low.endswith("zh_cn.json") or norm_low.endswith("zh_cn.lang"):
                        lang_files_by_mod[mod_key]["zh_cn"] = normalized
                    elif norm_low.endswith("zh_tw.json") or norm_low.endswith("zh_tw.lang"):
                        lang_files_by_mod[mod_key]["zh_tw"] = normalized
                    elif norm_low.endswith("en_us.json") or norm_low.endswith("en_us.lang"):
                        lang_files_by_mod[mod_key]["en_us"] = normalized
                    #else:
                    #    other_files.append(normalized)  # 🔒 保險：避免直接消失
                else:
                    other_files.append(normalized)

            # 計算任務數量（模組 + 其他檔案）
            mods_to_process = {k: v for k, v in lang_files_by_mod.items() if v}  # 只取有任何 lang 檔的 mod
            total_lang_mods = len(mods_to_process)
            total_content_files = len(other_files)
            total_tasks = total_lang_mods + total_content_files
            if total_tasks == 0:
                logger.info("未找到任何可處理的文件，處理結束。")
                yield {"progress": 1.0, "error": False}
                return
            logger.info(f"找到 {total_lang_mods} 個語言模組與 {total_content_files} 個內容檔案，開始處理...")
            yield {"progress": 0.0}

            # 使用 ThreadPoolExecutor 處理（你可以依需求調整 max_workers）
            #讀取config 設定資料
            max_workers = load_config().get("translator", {}).get("parallel_execution_workers") or os.cpu_count()

            futures = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                
                # ✅ 優化點：在啟動 ThreadPool 前，先完成一次性的路徑標準化快取
                all_files_cache = [n.lower().replace("\\", "/") for n in zf.namelist()]
                
                # 提交每個 mod 的處理（這裡每個 mod 的 paths 會包含 zh_cn/zh_tw/en_us 任一或多個）
                for mod_key, paths in mods_to_process.items():
                    futures.append(executor.submit(_process_single_mod, zf, paths, rules, output_dir, must_translate_dir))

                # 提交其他檔案處理（例如圖片、md、json5、localized files 等）
                for input_path in other_files:
                    futures.append(
                        executor.submit(
                            _process_content_or_copy_file, 
                            zf, input_path, rules, 
                            output_dir,only_process_lang,
                            all_files_cache=all_files_cache  # 傳遞快取
                            ))

                completed = 0
                for fut in concurrent.futures.as_completed(futures):
                    completed += 1
                    try:
                        res = fut.result()
                    except Exception as e:
                        logger.error(f"處理時發生未預期錯誤: {e}")
                        res = {"success": False, "error": True}

                    progress = completed / total_tasks
                    # ⭐ 修改重點：無論有沒有 log，都要 yield 進度
                    # 這樣 UI 才會收到 progress 並更新進度條

                    # 1. 準備回傳給 UI 的資料包
                    yield_data = {
                        "progress": progress,
                        "error": res.get("error", False),
                        "pending_count": res.get("pending_count", 0),
                    }

                    # 2. 終端機日誌處理
                    log_msg = res.get("log")
                    if log_msg:
                        logger.info(log_msg)
                    else:
                        logger.debug(f"靜默處理完成 (進度: {progress:.2%})")

                    # 3. 核心重點：無論有沒有 log，每一條任務完成都 yield 一次
                    # 這樣進度條 (progress) 就會隨著任務完成一個個跳動
                    yield yield_data
            # <--- 在這裡插入清理代碼 --->
            logger.info("正在清理空的待翻譯資料夾...")
            remove_empty_dirs(must_translate_dir)
            # 🔥 新增：輸出整理後的待翻譯檔案
            #讀取config 設定資料
            folder_name=load_config().get("lang_merger", {}).get("pending_organized_folder_name", "待翻譯整理需翻譯")
            filtered_pending_dir = os.path.join(output_dir, folder_name)
            logger.info("正在產生待翻譯整理需翻譯 檔案...")
            #config 讀取資料
            filtered_pending_min_count=load_config().get("lang_merger", {}).get("filtered_pending_min_count", 2)
            export_filtered_pending(must_translate_dir, filtered_pending_dir, min_count=filtered_pending_min_count)
            # <--- 插入結束 --->
            logger.info(f"--- 全部處理完成: {total_tasks} 個任務完成 ---")
            yield {"progress": 1.0}

    except zipfile.BadZipFile:
        logger.error(f"錯誤：檔案 '{zip_file}' 不是有效 ZIP。")
        yield {"progress": 1.0, "error": True}
    except Exception as e:
        logger.exception(f"處理 ZIP 發生錯誤: {e}")
        yield {"progress": 1.0, "error": True}
