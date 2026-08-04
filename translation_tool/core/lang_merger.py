"""translation_tool/core/lang_merger.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import concurrent.futures
import os
import zipfile
from collections import defaultdict
from typing import Any, Dict, Generator, List

from ..utils.config_manager import load_config
from ..utils.log_unit import log_error, log_info, log_warning, log_debug, log_exception
from ..utils.text_processor import load_replace_rules
from .lang_merge_content import _process_content_or_copy_file, export_filtered_pending, remove_empty_dirs
from .lang_merge_io import ZipReader, FolderReader
from .lang_merge_pipeline import _process_single_mod

def merge_zhcn_to_zhtw_from_zip(zip_file: str, output_dir: str,
                                 only_process_lang: bool = False,
                                 process_zh_cn: bool | None = None,
                                 patchouli_skip: bool | None = None,
                                 patchouli_threshold: float | None = None,
                                 zh_en_threshold: int | None = None) -> Generator[Dict[str, Any], None, None]:
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
    # 新結構：輸出分為三個子目錄
    # - lang_output/：lang 合併輸出（含待翻譯）
    # - patchouli_output/：Patchouli 書籍內容輸出
    # - other_output/：manual、book.json 等其他內容
    lang_output_dir = os.path.join(output_dir, "lang_output")
    patchouli_output_dir = os.path.join(output_dir, "patchouli_output")
    other_output_dir = os.path.join(output_dir, "other_output")
    errordata_output_dir = os.path.join(output_dir, "errordata_output")
    os.makedirs(lang_output_dir, exist_ok=True)
    os.makedirs(patchouli_output_dir, exist_ok=True)
    os.makedirs(other_output_dir, exist_ok=True)
    os.makedirs(errordata_output_dir, exist_ok=True)
    must_translate_dir = os.path.join(lang_output_dir, load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯"))
    os.makedirs(must_translate_dir, exist_ok=True)

    try:
        rules = load_replace_rules(load_config().get("replace_rules_path", "replace_rules.json"))
    except Exception as e:
        log_error(f"載入替換規則失敗: {e}")
        yield {"progress": 0.0, "error": True}
        return

    # --- 新增：檢查 ZIP 檔案是否存在 ---
    if not os.path.exists(zip_file):
        full_path = os.path.abspath(zip_file) # 取得絕對路徑，方便除錯
        log_warning(f"檔案不存在，已跳過: {full_path}")
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

            # ============================================================
            # 統一前綴自動剝離（Universal Wrapper Prefix Stripping）
            # 原則：不管前綴叫什麼名字，只要整個 ZIP 的所有路徑都被包在
            # 同一個頂層資料夾下，就剝離它。不再使用白名單。
            # ============================================================
            all_names = zf.namelist()
            strip_wrapper = None  # 預設不剝離

            if all_names:
                top_prefixes = set()
                for name in all_names:
                    parts = name.replace("\\", "/").split("/")
                    if parts and parts[0]:
                        top_prefixes.add(parts[0])

                # 只有一個頂層前綴 → 代表整個 ZIP 被包了一層，剝離它
                if len(top_prefixes) == 1:
                    wrapper_prefix = list(top_prefixes)[0]
                    prefix_to_strip = wrapper_prefix + "/"

                    # 只在有實質內容時才剝離（避免空前綴或只有頂層目錄的情況）
                    sample_stripped = all_names[0][len(prefix_to_strip):] if all_names[0].startswith(prefix_to_strip) else all_names[0]
                    if sample_stripped and sample_stripped != all_names[0]:
                        def strip_wrapper(path):
                            if path.startswith(prefix_to_strip):
                                return path[len(prefix_to_strip):]
                            return path
                        log_info(f"偵測到統一包裝前綴 '{wrapper_prefix}/'，已自動剝離。")

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

                # ⚠️ 這裡保持原始路徑，剝離只在 _process_single_mod 輸出時進行
                # （避免用剝離後路徑讀 ZIP 讀不到的問題）
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
                log_info("未找到任何可處理的文件，處理結束。")
                yield {"progress": 1.0, "error": False}
                return
            log_info(f"找到 {total_lang_mods} 個語言模組與 {total_content_files} 個內容檔案，開始處理...")
            yield {"progress": 0.0}

            # 使用 ThreadPoolExecutor 處理（你可以依需求調整 max_workers）
            #讀取config 設定資料
            cpu_count = os.cpu_count() or 2
            max_allowed_workers = max(1, cpu_count // 2)
            config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
            if isinstance(config_workers, int) and config_workers > 0:
                max_workers = min(config_workers, max_allowed_workers)
            else:
                max_workers = max_allowed_workers

            futures = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                
                # ✅ 優化點：在啟動 ThreadPool 前，先完成一次性的路徑標準化快取
                all_files_cache = [n.lower().replace("\\", "/") for n in zf.namelist()]
                
                # 提交每個 mod 的處理（這裡每個 mod 的 paths 會包含 zh_cn/zh_tw/en_us 任一或多個）
                for mod_key, paths in mods_to_process.items():
                    futures.append(executor.submit(_process_single_mod, ZipReader(zf), paths, rules, lang_output_dir, must_translate_dir, errordata_output_dir, all_files_cache=all_files_cache))

                # 提交其他檔案處理（例如圖片、md、json5、localized files 等）
                for input_path in other_files:
                    futures.append(
                        executor.submit(
                            _process_content_or_copy_file,
                            ZipReader(zf), input_path, rules,
                            output_dir, only_process_lang,
                            all_files_cache=all_files_cache,
                            patchouli_output_dir=patchouli_output_dir,
                            other_output_dir=other_output_dir,
                            errordata_dir=errordata_output_dir,
                            process_zh_cn=process_zh_cn,
                            patchouli_skip=patchouli_skip,
                            patchouli_threshold=patchouli_threshold,
                            zh_en_threshold=zh_en_threshold,
                        ))

                completed = 0
                for fut in concurrent.futures.as_completed(futures):
                    completed += 1
                    try:
                        res = fut.result()
                    except Exception as e:
                        log_error(f"處理時發生未預期錯誤: {e}")
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
                        log_info(log_msg)
                    else:
                        log_debug(f"靜默處理完成 (進度: {progress:.2%})")

                    # 3. 核心重點：無論有沒有 log，每一條任務完成都 yield 一次
                    # 這樣進度條 (progress) 就會隨著任務完成一個個跳動
                    yield yield_data
            # <--- 在這裡插入清理代碼 --->
            log_info("正在清理空的待翻譯資料夾...")
            remove_empty_dirs(must_translate_dir)
            # 🔥 新增：輸出整理後的待翻譯檔案（位於 lang_output/）
            #讀取config 設定資料
            folder_name=load_config().get("lang_merger", {}).get("pending_organized_folder_name", "待翻譯整理")
            filtered_pending_dir = os.path.join(lang_output_dir, folder_name)
            log_info("正在產生待翻譯整理 檔案...")
            #config 讀取資料
            filtered_pending_min_count=load_config().get("lang_merger", {}).get("filtered_pending_min_count", 2)
            export_filtered_pending(must_translate_dir, filtered_pending_dir, min_count=filtered_pending_min_count)
            # <--- 插入結束 --->
            log_info(f"--- 全部處理完成: {total_tasks} 個任務完成 ---")
            yield {"progress": 1.0}

    except zipfile.BadZipFile:
        log_error(f"錯誤：檔案 '{zip_file}' 不是有效 ZIP。")
        yield {"progress": 1.0, "error": True}
    except Exception as e:
        log_exception(f"處理 ZIP 發生錯誤: {e}")
        yield {"progress": 1.0, "error": True}


def merge_zhcn_to_zhtw_from_folder(
    input_dir: str,
    output_dir: str,
    only_process_lang: bool = False,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
) -> Generator[Dict[str, Any], None, None]:
    """將資料夾中的簡體中文合併為繁體中文。

    與 merge_zhcn_to_zhtw_from_zip 邏輯相同，但使用 FolderReader 讀取目錄內容。

    Args:
        input_dir: 輸入的資料夾路徑
        output_dir: 輸出目錄路徑
        only_process_lang: 是否只處理 lang 檔案
        process_zh_cn: 是否處理 zh_cn 檔案
        patchouli_skip: 是否啟用 Patchouli en_us skip
        patchouli_threshold: Patchouli 有效翻譯閾值
        zh_en_threshold: zh 英文含量閾值

    Yields:
        進度字典，包含 progress、log、error 等資訊
    """
    os.makedirs(output_dir, exist_ok=True)
    lang_output_dir = os.path.join(output_dir, "lang_output")
    patchouli_output_dir = os.path.join(output_dir, "patchouli_output")
    other_output_dir = os.path.join(output_dir, "other_output")
    errordata_output_dir = os.path.join(output_dir, "errordata_output")
    os.makedirs(lang_output_dir, exist_ok=True)
    os.makedirs(patchouli_output_dir, exist_ok=True)
    os.makedirs(other_output_dir, exist_ok=True)
    os.makedirs(errordata_output_dir, exist_ok=True)
    must_translate_dir = os.path.join(
        lang_output_dir,
        load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯"),
    )
    os.makedirs(must_translate_dir, exist_ok=True)

    try:
        rules = load_replace_rules(load_config().get("replace_rules_path", "replace_rules.json"))
    except Exception as e:
        log_error(f"載入替換規則失敗: {e}")
        yield {"progress": 0.0, "error": True}
        return

    if not os.path.exists(input_dir):
        full_path = os.path.abspath(input_dir)
        log_warning(f"資料夾不存在，已跳過: {full_path}")
        yield {"progress": 1.0, "error": False}
        return

    try:
        reader = FolderReader(input_dir)
        yield {"progress": 0.0, "log": f"分析資料夾: {os.path.basename(input_dir)}"}

        all_names = reader.list_all()
        strip_wrapper = None

        if all_names:
            top_prefixes = set()
            for name in all_names:
                parts = name.replace("\\", "/").split("/")
                if parts and parts[0]:
                    top_prefixes.add(parts[0])

            if len(top_prefixes) == 1:
                wrapper_prefix = list(top_prefixes)[0]
                prefix_to_strip = wrapper_prefix + "/"
                sample_stripped = all_names[0][len(prefix_to_strip):] if all_names[0].startswith(prefix_to_strip) else all_names[0]
                if sample_stripped and sample_stripped != all_names[0]:
                    def strip_wrapper(path):
                        if path.startswith(prefix_to_strip):
                            return path[len(prefix_to_strip):]
                        return path
                    log_info(f"偵測到統一包裝前綴 '{wrapper_prefix}/'，已自動剝離。")

        lang_files_by_mod = defaultdict(dict)
        other_files: List[str] = []

        for file_path in all_names:
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
            else:
                other_files.append(normalized)

        mods_to_process = {k: v for k, v in lang_files_by_mod.items() if v}
        total_lang_mods = len(mods_to_process)
        total_content_files = len(other_files)
        total_tasks = total_lang_mods + total_content_files
        if total_tasks == 0:
            log_info("未找到任何可處理的文件，處理結束。")
            yield {"progress": 1.0, "error": False}
            return
        log_info(f"找到 {total_lang_mods} 個語言模組與 {total_content_files} 個內容檔案，開始處理...")
        yield {"progress": 0.0}

        cpu_count = os.cpu_count() or 2
        max_allowed_workers = max(1, cpu_count // 2)
        config_workers = load_config().get("translator", {}).get("parallel_execution_workers")
        if isinstance(config_workers, int) and config_workers > 0:
            max_workers = min(config_workers, max_allowed_workers)
        else:
            max_workers = max_allowed_workers

        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            all_files_cache = [n.lower().replace("\\", "/") for n in all_names]

            for mod_key, paths in mods_to_process.items():
                futures.append(
                    executor.submit(
                        _process_single_mod,
                        FolderReader(input_dir),
                        paths,
                        rules,
                        lang_output_dir,
                        must_translate_dir,
                        errordata_output_dir,
                        all_files_cache=all_files_cache,
                    )
                )

            for input_path in other_files:
                futures.append(
                    executor.submit(
                        _process_content_or_copy_file,
                        FolderReader(input_dir),
                        input_path,
                        rules,
                        output_dir,
                        only_process_lang,
                        all_files_cache=all_files_cache,
                        patchouli_output_dir=patchouli_output_dir,
                        other_output_dir=other_output_dir,
                        errordata_dir=errordata_output_dir,
                        process_zh_cn=process_zh_cn,
                        patchouli_skip=patchouli_skip,
                        patchouli_threshold=patchouli_threshold,
                        zh_en_threshold=zh_en_threshold,
                    )
                )

            completed = 0
            for fut in concurrent.futures.as_completed(futures):
                completed += 1
                try:
                    res = fut.result()
                except Exception as e:
                    log_error(f"處理時發生未預期錯誤: {e}")
                    res = {"success": False, "error": True}

                progress = completed / total_tasks
                yield_data = {
                    "progress": progress,
                    "error": res.get("error", False),
                    "pending_count": res.get("pending_count", 0),
                }

                log_msg = res.get("log")
                if log_msg:
                    log_info(log_msg)
                else:
                    log_debug(f"靜默處理完成 (進度: {progress:.2%})")

                yield yield_data

        log_info("正在清理空的待翻譯資料夾...")
        remove_empty_dirs(must_translate_dir)
        folder_name = load_config().get("lang_merger", {}).get("pending_organized_folder_name", "待翻譯整理")
        filtered_pending_dir = os.path.join(lang_output_dir, folder_name)
        log_info("正在產生待翻譯整理 檔案...")
        filtered_pending_min_count = load_config().get("lang_merger", {}).get("filtered_pending_min_count", 2)
        export_filtered_pending(must_translate_dir, filtered_pending_dir, min_count=filtered_pending_min_count)
        log_info(f"--- 全部處理完成: {total_tasks} 個任務完成 ---")
        yield {"progress": 1.0}

    except Exception as e:
        log_exception(f"處理資料夾發生錯誤: {e}")
        yield {"progress": 1.0, "error": True}
