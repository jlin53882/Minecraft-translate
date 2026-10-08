"""translation_tool/core/lang_merger.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import os
import zipfile
from collections import defaultdict
from collections.abc import Generator
from typing import Any

from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor

from ..utils.cancellation import raise_if_cancelled
from ..utils.config_manager import load_config
from ..utils.log_unit import log_debug, log_error, log_exception, log_info
from ..utils.text_processor import load_replace_rules
from ..utils.zip_safety import ZipReadBudget
from .lang_merge_content import (
    _process_content_or_copy_file,
    export_filtered_pending,
    remove_empty_dirs,
)
from .lang_merge_content_copy import detect_content_wrapper_prefix
from .lang_merge_db import merge_db_fill
from .lang_merge_io import FolderReader, ZipReader
from .lang_merge_pipeline import _process_single_mod, detect_mod_wrapper_prefix

# 掃描檔名清單時每隔多少筆檢查一次取消（逐筆檢查成本小，但清單可達數十萬筆）。
_CANCEL_CHECK_EVERY = 256


def _checkpoint(index: int) -> None:
    """長迴圈的取消檢查點：每 _CANCEL_CHECK_EVERY 筆檢查一次，已取消則拋出 TaskCancelled。"""
    if index % _CANCEL_CHECK_EVERY == 0:
        raise_if_cancelled()


_FILE_FAILED_MESSAGE = "有檔案處理失敗（原因請查看日誌中的 ERROR 記錄）"


def _scale_progress(value: float, start: float, end: float) -> float:
    """Map source-local progress into the caller's progress range."""
    return start + max(0.0, min(1.0, value)) * (end - start)


def merge_zhcn_to_zhtw_from_zip(
    zip_file: str,
    output_dir: str,
    only_process_lang: bool = False,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """語系合併（zip）：純英文條目會先向 Mod 資料庫補譯（若設定啟用），其餘見 ``_merge_zhcn_to_zhtw_from_zip``。"""
    with merge_db_fill(use_translation_db, translation_db_version) as db_fill:
        yield from _merge_zhcn_to_zhtw_from_zip(
            zip_file,
            output_dir,
            only_process_lang,
            process_zh_cn,
            patchouli_skip,
            patchouli_threshold,
            zh_en_threshold,
            progress_start,
            progress_end,
            db_fill=db_fill,
        )


def _merge_zhcn_to_zhtw_from_zip(
    zip_file: str,
    output_dir: str,
    only_process_lang: bool = False,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    db_fill: Any = None,
) -> Generator[dict[str, Any], None, None]:
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
    processing_end = progress_start + (progress_end - progress_start) * 0.90
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
    must_translate_dir = os.path.join(
        lang_output_dir,
        load_config().get("lang_merger", {}).get("pending_folder_name", "待翻譯"),
    )
    os.makedirs(must_translate_dir, exist_ok=True)

    try:
        rules = load_replace_rules(
            load_config()
            .get("translator", {})
            .get("replace_rules_path", "replace_rules.json")
        )
    except Exception as e:  # noqa: BLE001
        log_error(f"載入替換規則失敗: {e!r}")
        yield {
            "progress": _scale_progress(0.0, progress_start, progress_end),
            "error": True,
        }
        return

    # --- 新增：檢查 ZIP 檔案是否存在 ---
    if not os.path.exists(zip_file):
        full_path = os.path.abspath(zip_file)  # 取得絕對路徑，方便除錯
        message = f"輸入 ZIP 不存在，無法合併: {full_path}"
        log_error(message)
        # 軟性錯誤：批次服務會把這個 ZIP 記為失敗並繼續處理下一個 ZIP（不會中斷整批），
        # 不能回報 error=False，否則缺檔會被當成成功完成。
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "log": message,
            "error": True,
        }
        return  # 直接結束這個產生器，不執行後面的 ZipFile 開啟動作
    if not os.path.isfile(zip_file):
        # 路徑存在但不是檔案（例如資料夾）：不能交給 ZipFile，否則錯誤訊息很不直觀
        message = f"輸入路徑不是 ZIP 檔案，無法合併: {os.path.abspath(zip_file)}"
        log_error(message)
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "log": message,
            "error": True,
        }
        return
    # --------------------------------

    try:
        with zipfile.ZipFile(zip_file, "r") as zf:
            yield {
                "progress": _scale_progress(0.0, progress_start, progress_end),
                "log": f"分析 ZIP 檔案: {os.path.basename(zip_file)}",
            }

            # ============================================================
            # 統一前綴自動剝離（Universal Wrapper Prefix Stripping）
            # 原則：不管前綴叫什麼名字，只要整個 ZIP 的所有路徑都被包在
            # 同一個頂層資料夾下，就剝離它。不再使用白名單。
            # ============================================================
            all_names = zf.namelist()
            strip_wrapper = None  # 預設不剝離

            if all_names:
                top_prefixes = set()
                for index, name in enumerate(all_names):
                    _checkpoint(index)
                    parts = name.replace("\\", "/").split("/")
                    if parts and parts[0]:
                        top_prefixes.add(parts[0])

                # 只有一個頂層前綴 → 代表整個 ZIP 被包了一層，剝離它
                if len(top_prefixes) == 1:
                    wrapper_prefix = list(top_prefixes)[0]  # noqa: RUF015
                    prefix_to_strip = wrapper_prefix + "/"

                    # 只在有實質內容時才剝離（避免空前綴或只有頂層目錄的情況）
                    sample_stripped = all_names[0].removeprefix(prefix_to_strip)
                    if sample_stripped and sample_stripped != all_names[0]:

                        def strip_wrapper(path):
                            if path.startswith(prefix_to_strip):
                                return path[len(prefix_to_strip) :]
                            return path

                        log_info(
                            f"偵測到統一包裝前綴 '{wrapper_prefix}/'，已自動剝離。"
                        )

            # 建立模組索引：以 mod_key 為單位，收集該 mod 下的 zh_cn/zh_tw/en_us 路徑
            lang_files_by_mod = defaultdict(dict)
            other_files: list[str] = []
            # for file_path in zf.namelist():
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

            for index, file_path in enumerate(all_names):
                _checkpoint(index)
                normalized = file_path.replace("\\", "/")
                if normalized.endswith("/") or not normalized:
                    continue

                # ⚠️ 這裡保持原始路徑，剝離只在 _process_single_mod 輸出時進行
                # （避免用剝離後路徑讀 ZIP 讀不到的問題）
                norm_low = normalized.lower()

                if "/lang/" in norm_low and (
                    norm_low.endswith(".json") or norm_low.endswith(".lang")  # noqa: PIE810
                ):
                    mod_key = normalized.split("/lang/")[0] + "/lang/"

                    if norm_low.endswith("zh_cn.json") or norm_low.endswith(  # noqa: PIE810
                        "zh_cn.lang"
                    ):
                        lang_files_by_mod[mod_key]["zh_cn"] = normalized
                    elif norm_low.endswith("zh_tw.json") or norm_low.endswith(  # noqa: PIE810
                        "zh_tw.lang"
                    ):
                        lang_files_by_mod[mod_key]["zh_tw"] = normalized
                    elif norm_low.endswith("en_us.json") or norm_low.endswith(  # noqa: PIE810
                        "en_us.lang"
                    ):
                        lang_files_by_mod[mod_key]["en_us"] = normalized
                    # else:
                    #    other_files.append(normalized)  # 🔒 保險：避免直接消失
                else:
                    other_files.append(normalized)

            # 計算任務數量（模組 + 其他檔案）
            mods_to_process = {
                k: v for k, v in lang_files_by_mod.items() if v
            }  # 只取有任何 lang 檔的 mod
            total_lang_mods = len(mods_to_process)
            eligible_other_files = [] if only_process_lang else other_files
            total_content_files = len(eligible_other_files)
            total_tasks = total_lang_mods + total_content_files
            if total_tasks == 0:
                log_info("未找到任何可處理的文件，處理結束。")
                yield {
                    "progress": _scale_progress(1.0, progress_start, progress_end),
                    "error": False,
                }
                return
            log_info(
                f"找到 {total_lang_mods} 個語言模組與 {total_content_files} 個內容檔案，開始處理..."
            )
            yield {"progress": _scale_progress(0.0, progress_start, progress_end)}

            # 使用 ThreadPoolExecutor 處理（你可以依需求調整 max_workers）
            # 讀取config 設定資料
            cpu_count = os.cpu_count() or 2
            max_allowed_workers = max(1, cpu_count // 2)
            config_workers = (
                load_config().get("translator", {}).get("parallel_execution_workers")
            )
            if isinstance(config_workers, int) and config_workers > 0:
                max_workers = min(config_workers, max_allowed_workers)
            else:
                max_workers = max_allowed_workers

            # 所有任務共用同一個累計讀取預算（防止大量合法大小成員的 ZIP bomb）
            zip_budget = ZipReadBudget.for_pack(label=str(zip_file))
            patchouli_eff_cache: dict = {}

            def iter_work_items():
                yield from ((True, paths) for paths in mods_to_process.values())
                yield from ((False, path) for path in eligible_other_files)

            def submit_work(executor, task):
                is_mod, payload = task
                if is_mod:
                    return executor.submit(
                        _process_single_mod,
                        ZipReader(zf, zip_budget),
                        payload,
                        rules,
                        lang_output_dir,
                        must_translate_dir,
                        errordata_output_dir,
                        # 需保留原始大小寫：用於偵測/剝離包裝前綴，
                        # 小寫版 all_files_cache 會讓 startswith 比對失敗
                        all_files_cache=all_names_raw,
                        wrapper_prefix=mod_wrapper_prefix,
                        db_fill=db_fill,
                    )
                return executor.submit(
                    _process_content_or_copy_file,
                    ZipReader(zf, zip_budget),
                    payload,
                    rules,
                    output_dir,
                    only_process_lang,
                    all_files_cache=all_files_cache,
                    wrapper_prefix=content_wrapper_prefix,
                    patchouli_eff_cache=patchouli_eff_cache,
                    patchouli_output_dir=patchouli_output_dir,
                    other_output_dir=other_output_dir,
                    errordata_dir=errordata_output_dir,
                    process_zh_cn=process_zh_cn,
                    patchouli_skip=patchouli_skip,
                    patchouli_threshold=patchouli_threshold,
                    zh_en_threshold=zh_en_threshold,
                )

            with ContextThreadPoolExecutor(max_workers=max_workers) as executor:
                # ✅ 優化點：在啟動 ThreadPool 前，先完成一次性的路徑標準化快取
                all_names_raw = all_names  # 同一份清單：不再重複呼叫 namelist()
                all_files_cache = [n.lower().replace("\\", "/") for n in all_names_raw]
                # 包裝前綴只算一次,避免每個 mod / 內容檔各掃一次全部檔名
                mod_wrapper_prefix = detect_mod_wrapper_prefix(all_names_raw)
                # 前綴剝離必須用原始大小寫的檔名 (input_path 保留原始大小寫);
                # all_files_cache 是小寫版,只供 case-insensitive 查找
                content_wrapper_prefix = detect_content_wrapper_prefix(all_names_raw)

                completed = 0
                with bounded_as_completed(
                    executor,
                    iter_work_items(),
                    submit_work,
                    max_in_flight=max_workers * 2,
                ) as work:
                    for fut, _task in work:
                        raise_if_cancelled()
                        completed += 1
                        try:
                            res = fut.result()
                        except Exception as e:  # noqa: BLE001
                            log_error(f"處理時發生未預期錯誤: {e!r}")
                            res = {"success": False, "error": True}

                        progress = _scale_progress(
                            completed / total_tasks, progress_start, processing_end
                        )
                        # ⭐ 修改重點：無論有沒有 log，都要 yield 進度
                        # 這樣 UI 才會收到 progress 並更新進度條

                        # 1. 準備回傳給 UI 的資料包
                        yield_data = {
                            "progress": progress,
                            "error": res.get("error", False),
                            "pending_count": res.get("pending_count", 0),
                        }
                        if yield_data["error"]:
                            yield_data["message"] = _FILE_FAILED_MESSAGE

                        # 2. 終端機日誌處理
                        log_msg = res.get("log")
                        if log_msg:
                            log_info(log_msg)
                        else:
                            log_debug(f"靜默處理完成 (進度: {progress:.2%})")

                        # 3. 核心重點：無論有沒有 log，每一條任務完成都 yield 一次
                        # 這樣進度條 (progress) 就會隨著任務完成一個個跳動
                        yield yield_data
            # 累計讀取預算用盡是 sticky 的：之後所有讀取都會失敗並被視為「單檔失敗」，
            # 輸出只會是部分合併。必須在 pack 層級明確回報，不能顯示成功（issue #109）。
            budget_exhausted = zip_budget.exhausted
            if budget_exhausted:
                log_error(
                    f"ZIP 累計讀取預算已用盡，輸出不完整：{os.path.basename(zip_file)}"
                    "（預算用盡後的檔案都未處理，請檢查 ZIP 是否異常龐大）"
                )
            # Worker processing is complete; finalize work owns the remaining
            # progress range so the UI does not claim 100% prematurely.
            yield {"progress": _scale_progress(0.90, progress_start, progress_end)}
            # <--- 在這裡插入清理代碼 --->
            log_info("正在清理空的待翻譯資料夾...")
            remove_empty_dirs(must_translate_dir)
            yield {"progress": _scale_progress(0.94, progress_start, progress_end)}
            # 🔥 新增：輸出整理後的待翻譯檔案（位於 lang_output/）
            # 讀取config 設定資料
            folder_name = (
                load_config()
                .get("lang_merger", {})
                .get("pending_organized_folder_name", "待翻譯整理")
            )
            filtered_pending_dir = os.path.join(lang_output_dir, folder_name)
            log_info("正在產生待翻譯整理 檔案...")
            # config 讀取資料
            filtered_pending_min_count = (
                load_config()
                .get("lang_merger", {})
                .get("filtered_pending_min_count", 2)
            )
            export_filtered_pending(
                must_translate_dir,
                filtered_pending_dir,
                min_count=filtered_pending_min_count,
            )
            yield {"progress": _scale_progress(0.98, progress_start, progress_end)}
            # <--- 插入結束 --->
            if budget_exhausted:
                incomplete_msg = (
                    f"--- 處理結束但輸出不完整：ZIP 累計讀取預算已用盡"
                    f"（{os.path.basename(zip_file)}），部分檔案未處理 ---"
                )
                log_error(incomplete_msg)
                yield {
                    "progress": _scale_progress(1.0, progress_start, progress_end),
                    "error": True,
                    "log": incomplete_msg,
                }
                return
            log_info(f"--- 全部處理完成: {total_tasks} 個任務完成 ---")
            yield {"progress": _scale_progress(1.0, progress_start, progress_end)}

    except zipfile.BadZipFile:
        log_error(f"錯誤：檔案 '{zip_file}' 不是有效 ZIP。")
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "error": True,
        }
    except Exception as e:  # noqa: BLE001
        log_exception(f"處理 ZIP 發生錯誤: {e}")
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "error": True,
        }


def merge_zhcn_to_zhtw_from_folder(
    input_dir: str,
    output_dir: str,
    only_process_lang: bool = False,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    use_translation_db: bool | None = None,
    translation_db_version: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """語系合併（folder）：純英文條目會先向 Mod 資料庫補譯（若設定啟用），其餘見 ``_merge_zhcn_to_zhtw_from_folder``。"""
    with merge_db_fill(use_translation_db, translation_db_version) as db_fill:
        yield from _merge_zhcn_to_zhtw_from_folder(
            input_dir,
            output_dir,
            only_process_lang,
            process_zh_cn,
            patchouli_skip,
            patchouli_threshold,
            zh_en_threshold,
            progress_start,
            progress_end,
            db_fill=db_fill,
        )


def _merge_zhcn_to_zhtw_from_folder(
    input_dir: str,
    output_dir: str,
    only_process_lang: bool = False,
    process_zh_cn: bool | None = None,
    patchouli_skip: bool | None = None,
    patchouli_threshold: float | None = None,
    zh_en_threshold: int | None = None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    db_fill: Any = None,
) -> Generator[dict[str, Any], None, None]:
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
    processing_end = progress_start + (progress_end - progress_start) * 0.90
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
        rules = load_replace_rules(
            load_config()
            .get("translator", {})
            .get("replace_rules_path", "replace_rules.json")
        )
    except Exception as e:  # noqa: BLE001
        log_error(f"載入替換規則失敗: {e!r}")
        yield {
            "progress": _scale_progress(0.0, progress_start, progress_end),
            "error": True,
        }
        return

    if not os.path.exists(input_dir):
        full_path = os.path.abspath(input_dir)
        message = f"輸入資料夾不存在，無法合併: {full_path}"
        log_error(message)
        # 軟性錯誤：服務層會記為資料夾失敗（跳過階段 2、任務標為 ERROR），
        # 不能回報 error=False，否則缺資料夾會顯示「翻譯已完成」。
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "log": message,
            "error": True,
        }
        return
    if not os.path.isdir(input_dir):
        # 路徑存在但不是資料夾（例如誤填成 ZIP 或一般檔案）：os.walk 對檔案不會報錯、
        # 只會回傳空內容，結果會變成「找不到任何可處理的文件」的假成功，所以明確判為錯誤。
        message = f"輸入路徑不是資料夾，無法合併: {os.path.abspath(input_dir)}"
        log_error(message)
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "log": message,
            "error": True,
        }
        return

    try:
        reader = FolderReader(input_dir)
        yield {
            "progress": _scale_progress(0.0, progress_start, progress_end),
            "log": f"分析資料夾: {os.path.basename(input_dir)}",
        }

        all_names = reader.list_all()
        strip_wrapper = None

        if all_names:
            top_prefixes = set()
            for index, name in enumerate(all_names):
                _checkpoint(index)
                parts = name.replace("\\", "/").split("/")
                if parts and parts[0]:
                    top_prefixes.add(parts[0])

            if len(top_prefixes) == 1:
                wrapper_prefix = list(top_prefixes)[0]  # noqa: RUF015
                prefix_to_strip = wrapper_prefix + "/"
                sample_stripped = all_names[0].removeprefix(prefix_to_strip)
                if sample_stripped and sample_stripped != all_names[0]:

                    def strip_wrapper(path):
                        if path.startswith(prefix_to_strip):
                            return path[len(prefix_to_strip) :]
                        return path

                    log_info(f"偵測到統一包裝前綴 '{wrapper_prefix}/'，已自動剝離。")

        lang_files_by_mod = defaultdict(dict)
        other_files: list[str] = []

        for index, file_path in enumerate(all_names):
            _checkpoint(index)
            normalized = file_path.replace("\\", "/")
            if normalized.endswith("/") or not normalized:
                continue

            norm_low = normalized.lower()

            if "/lang/" in norm_low and (
                norm_low.endswith(".json") or norm_low.endswith(".lang")  # noqa: PIE810
            ):
                mod_key = normalized.split("/lang/")[0] + "/lang/"

                if norm_low.endswith("zh_cn.json") or norm_low.endswith("zh_cn.lang"):  # noqa: PIE810
                    lang_files_by_mod[mod_key]["zh_cn"] = normalized
                elif norm_low.endswith("zh_tw.json") or norm_low.endswith("zh_tw.lang"):  # noqa: PIE810
                    lang_files_by_mod[mod_key]["zh_tw"] = normalized
                elif norm_low.endswith("en_us.json") or norm_low.endswith("en_us.lang"):  # noqa: PIE810
                    lang_files_by_mod[mod_key]["en_us"] = normalized
            else:
                other_files.append(normalized)

        mods_to_process = {k: v for k, v in lang_files_by_mod.items() if v}
        total_lang_mods = len(mods_to_process)
        eligible_other_files = [] if only_process_lang else other_files
        total_content_files = len(eligible_other_files)
        total_tasks = total_lang_mods + total_content_files
        if total_tasks == 0:
            log_info("未找到任何可處理的文件，處理結束。")
            yield {
                "progress": _scale_progress(1.0, progress_start, progress_end),
                "error": False,
            }
            return
        log_info(
            f"找到 {total_lang_mods} 個語言模組與 {total_content_files} 個內容檔案，開始處理..."
        )
        yield {"progress": _scale_progress(0.0, progress_start, progress_end)}

        cpu_count = os.cpu_count() or 2
        max_allowed_workers = max(1, cpu_count // 2)
        config_workers = (
            load_config().get("translator", {}).get("parallel_execution_workers")
        )
        if isinstance(config_workers, int) and config_workers > 0:
            max_workers = min(config_workers, max_allowed_workers)
        else:
            max_workers = max_allowed_workers

        all_files_cache = [n.lower().replace("\\", "/") for n in all_names]
        # 包裝前綴只算一次,避免每個 mod / 內容檔各掃一次全部檔名
        mod_wrapper_prefix = detect_mod_wrapper_prefix(all_names)
        # 前綴剝離必須用原始大小寫的檔名（同 ZIP 模式說明）
        content_wrapper_prefix = detect_content_wrapper_prefix(all_names)
        patchouli_eff_cache: dict = {}

        def iter_work_items():
            yield from ((True, paths) for paths in mods_to_process.values())
            yield from ((False, path) for path in eligible_other_files)

        def submit_work(executor, task):
            is_mod, payload = task
            if is_mod:
                return executor.submit(
                    _process_single_mod,
                    FolderReader(input_dir),
                    payload,
                    rules,
                    lang_output_dir,
                    must_translate_dir,
                    errordata_output_dir,
                    # 需保留原始大小寫（同 ZIP 模式說明）
                    all_files_cache=all_names,
                    wrapper_prefix=mod_wrapper_prefix,
                    db_fill=db_fill,
                )
            return executor.submit(
                _process_content_or_copy_file,
                FolderReader(input_dir),
                payload,
                rules,
                output_dir,
                only_process_lang,
                all_files_cache=all_files_cache,
                wrapper_prefix=content_wrapper_prefix,
                patchouli_eff_cache=patchouli_eff_cache,
                patchouli_output_dir=patchouli_output_dir,
                other_output_dir=other_output_dir,
                errordata_dir=errordata_output_dir,
                process_zh_cn=process_zh_cn,
                patchouli_skip=patchouli_skip,
                patchouli_threshold=patchouli_threshold,
                zh_en_threshold=zh_en_threshold,
            )

        with ContextThreadPoolExecutor(max_workers=max_workers) as executor:
            completed = 0
            with bounded_as_completed(
                executor,
                iter_work_items(),
                submit_work,
                max_in_flight=max_workers * 2,
            ) as work:
                for fut, _task in work:
                    raise_if_cancelled()
                    completed += 1
                    try:
                        res = fut.result()
                    except Exception as e:  # noqa: BLE001
                        log_error(f"處理時發生未預期錯誤: {e!r}")
                        res = {"success": False, "error": True}

                    progress = _scale_progress(
                        completed / total_tasks, progress_start, processing_end
                    )
                    yield_data = {
                        "progress": progress,
                        "error": res.get("error", False),
                        "pending_count": res.get("pending_count", 0),
                    }
                    if yield_data["error"]:
                        yield_data["message"] = _FILE_FAILED_MESSAGE

                    log_msg = res.get("log")
                    if log_msg:
                        log_info(log_msg)
                    else:
                        log_debug(f"靜默處理完成 (進度: {progress:.2%})")

                    yield yield_data

        yield {"progress": _scale_progress(0.90, progress_start, progress_end)}
        log_info("正在清理空的待翻譯資料夾...")
        remove_empty_dirs(must_translate_dir)
        yield {"progress": _scale_progress(0.94, progress_start, progress_end)}
        folder_name = (
            load_config()
            .get("lang_merger", {})
            .get("pending_organized_folder_name", "待翻譯整理")
        )
        filtered_pending_dir = os.path.join(lang_output_dir, folder_name)
        log_info("正在產生待翻譯整理 檔案...")
        filtered_pending_min_count = (
            load_config().get("lang_merger", {}).get("filtered_pending_min_count", 2)
        )
        export_filtered_pending(
            must_translate_dir,
            filtered_pending_dir,
            min_count=filtered_pending_min_count,
        )
        yield {"progress": _scale_progress(0.98, progress_start, progress_end)}
        log_info(f"--- 全部處理完成: {total_tasks} 個任務完成 ---")
        yield {"progress": _scale_progress(1.0, progress_start, progress_end)}

    except Exception as e:  # noqa: BLE001
        log_exception(f"處理資料夾發生錯誤: {e}")
        yield {
            "progress": _scale_progress(1.0, progress_start, progress_end),
            "error": True,
        }
