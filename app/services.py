# /minecraft_translator_flet/app/services.py 
import os
import json
import traceback
import time
from collections import deque
from typing import Generator, Dict, Any
from pathlib import Path

# services.py
import logging
logger = logging.getLogger(__name__)
from translation_tool.utils.ui_logging_handler import UISessionLogHandler


# ----------------- 日誌限制器（強化版） -----------------
class LogLimiter:
    def __init__(self, max_logs=3000, flush_interval=0.1):
        """
        max_logs: 最多保留多少條 log
        flush_interval: 多久允許送出一次 log（秒）
        """
        self.max_logs = max_logs
        self.flush_interval = flush_interval
        self.log_queue = deque(maxlen=max_logs)

        # buffer 來累積大量 log（避免 UI 每筆重繪）
        self.pending_logs = []

        self.last_flush = 0

    def filter(self, update_dict: Dict[str, Any]):
        """批次合併 log + 限制輸出頻率"""

        # 若沒有 log 欄位 → 直接放行（如 progress）
        if "log" not in update_dict:
            return update_dict

        log_text = update_dict["log"]
        self.log_queue.append(log_text)
        self.pending_logs.append(log_text)

        now = time.time()

        # 不到 flush 週期 → 不更新 UI
        if now - self.last_flush < self.flush_interval:
            return None

        # 準備批次輸出
        self.last_flush = now

        # 結合 pending logs
        merged = "\n".join(self.pending_logs)
        self.pending_logs.clear()

        return {"log": merged, "progress": update_dict.get("progress")}
    
    def flush(self):
        """強制輸出尚未送出的 pending logs"""
        if not self.pending_logs:
            return None

        merged = "\n".join(self.pending_logs)
        self.pending_logs.clear()
        self.last_flush = time.time()
        return {"log": merged}


# 建立全域限制器
GLOBAL_LOG_LIMITER = LogLimiter(max_logs=5000, flush_interval=0.1)
# ------------------------------------------------------

# 核心演算法層的匯入
from translation_tool.core.ftb_translator import translate_directory_generator
from translation_tool.core.lang_merger import merge_zhcn_to_zhtw_from_zip
from translation_tool.core.jar_processor import extract_lang_files_generator, extract_book_files_generator
from translation_tool.utils.text_processor import load_replace_rules as load_rules_core, save_replace_rules as save_rules_core
from translation_tool.utils.config_manager import load_config, save_config
from translation_tool.utils.species_cache import lookup_species_name, is_potential_species_name
from translation_tool.core.lm_translator import translate_directory_generator as lm_translate_gen
from translation_tool.core.output_bundler import bundle_outputs_generator
from translation_tool.checkers.untranslated_checker import check_untranslated_generator
from translation_tool.checkers.variant_comparator import compare_variants_generator
from translation_tool.checkers.english_residue_checker import check_english_residue_generator
from translation_tool.checkers.variant_comparator_tsv import compare_variants_tsv_generator
from translation_tool.utils import cache_manager

from translation_tool.utils.log_unit import log_warning, log_error, log_debug, log_info

# 1. 確保 Handler 設定正確
UI_LOG_HANDLER = UISessionLogHandler()
UI_LOG_HANDLER.setLevel(logging.INFO)
# UI 顯示建議簡潔，只留 message
UI_LOG_HANDLER.setFormatter(logging.Formatter("%(message)s"))


# --- 檔案路徑設定 ---
CONFIG_PATH = os.path.join(os.getcwd(), "config.json")
REPLACE_RULES_PATH = os.path.join(os.getcwd(), "replace_rules.json")

# --- 檔案讀寫服務 ---
def load_replace_rules():
    return load_rules_core(REPLACE_RULES_PATH)

def save_replace_rules(rules):
    save_rules_core(REPLACE_RULES_PATH, rules)

def load_config_json():
    return load_config(CONFIG_PATH)

def save_config_json(config):
    save_config(config, CONFIG_PATH)

# ------------------------------------------------------
# ---------------- 核心功能服務（含 log 限制） ----------------
# ------------------------------------------------------

def update_logger_config():
    """重新讀取 config 並套用最新的 Log 等級"""
    _config = load_config()
    _log_cfg = _config.get("logging", {})
    
    _level_name = _log_cfg.get("log_level", "INFO").upper()
    _numeric_level = getattr(logging, _level_name, logging.INFO)
    _format_str = _log_cfg.get("log_format", "%(message)s")

    # 獲取 Logger 對象
    root_logger = logging.getLogger()
    target_logger = logging.getLogger("translation_tool")

    # ⭐ 2. 關鍵：檢查是否已經掛載過，避免重複顯示
    # 我們統一掛在 Root Logger 即可，子 Logger 會透過 propagate 傳上來
    if UI_LOG_HANDLER not in root_logger.handlers:
        root_logger.addHandler(UI_LOG_HANDLER)

    target_logger = logging.getLogger("translation_tool") 
    target_logger.setLevel(logging.INFO)
    target_logger.propagate = True # 確保訊息會向傳遞

    # ⭐ 3. 動態更新等級與格式
    # 這樣你在 config 改 DEBUG，這裡就會立刻變成 10
    root_logger.setLevel(_numeric_level)
    target_logger.setLevel(_numeric_level)
    UI_LOG_HANDLER.setLevel(_numeric_level)
    
    # 套用 config 裡的格式
    UI_LOG_HANDLER.setFormatter(logging.Formatter(_format_str))

    # 確保子模組訊息會向上傳遞
    target_logger.propagate = True 
    
    logger.debug(f"Log 系統已同步：Level={_level_name}, Format={_format_str}")



def run_lm_translation_service(
    input_dir: str,
    output_dir: str,
    session,
    dry_run: bool = False ,
    export_lang: bool = False,  # 新增參數
    write_new_cache: bool = True,  # 新增參數
):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 

    logger.debug(f"DEBUG [2. Service]: 接收到的 export_lang 為 -> {export_lang}")

    try:
        # 初始化 Session 狀態
        session.start()
        UI_LOG_HANDLER.set_session(session)
        # ⭐ Dry Run 模式提示
        if dry_run:
            session.add_log(
                "[DRY-RUN] 啟用：僅進行分析與預覽，不會送出任何 API 請求"
            )

        # ⭐ 把 dry_run 明確傳遞給 generator
        for update_dict in lm_translate_gen(
            input_dir,
            output_dir,
            dry_run=dry_run,   # ⭐ 關鍵
            export_lang=export_lang,
            write_new_cache=write_new_cache # ⭐ 傳遞下去
        ):
            # ---- log ----
            log = update_dict.get("log")
            if log:
                session.add_log(log)

            # ---- progress ----
            if "progress" in update_dict and update_dict["progress"] is not None:
                session.set_progress(update_dict["progress"])

            # ---- error ----
            if update_dict.get("error"):
                session.set_error()
                return

        # ---- 正常結束 ----
        final = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])

        if dry_run:
            session.add_log(
                "[DRY-RUN] 分析完成，未執行實際翻譯"
            )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"LM 服務失敗: {e}\n{full_traceback}")
        session.add_log(
            f"[致命錯誤] LM 翻譯服務失敗：{e}\n{full_traceback}"
        )
        session.set_error()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)



def run_lang_extraction_service(mods_dir: str, output_dir: str, session):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        for update in extract_lang_files_generator(mods_dir, output_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update)
            if filtered is None:
                continue

            if "log" in filtered:
                session.add_log(filtered["log"])

            if "progress" in filtered:
                session.set_progress(filtered["progress"])

            if filtered.get("error"):
                session.set_error()
                return
        # for loop 結束後
        final = GLOBAL_LOG_LIMITER.flush()
        if final and "log" in final:
            session.add_log(final["log"])
        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Lang 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Lang 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()
    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)    


def run_book_extraction_service(mods_dir: str, output_dir: str, session):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        for update in extract_book_files_generator(mods_dir, output_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update)
            if filtered is None:
                continue

            if "log" in filtered:
                session.add_log(filtered["log"])

            if "progress" in filtered:
                session.set_progress(filtered["progress"])

            if filtered.get("error"):
                session.set_error()
                return

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] Book 檔案提取失敗：{e}\n{full_traceback}")
        session.set_error()

    finally:
        # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)


# app/services.py
def run_ftb_translation_service(
    directory_path: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,   # ✅ 新增
    step_export: bool = True,
    step_clean: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,   # ✅ 新增
):
    # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        from translation_tool.core.ftb_translator import run_ftb_pipeline

        run_ftb_pipeline(
            directory_path,
            session=session,
            output_dir=output_dir,
            dry_run=dry_run,
            step_export=step_export,
            step_clean=step_clean,
            step_translate=step_translate,
            step_inject=step_inject,
            write_new_cache=write_new_cache,
        )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] FTB 服務失敗：{e}\n{full_traceback}")
        session.add_log(f"[致命錯誤] {e}\n{full_traceback}")
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)


def run_kubejs_tooltip_service(
    input_dir: str,
    session,
    output_dir: str | None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
):
    update_logger_config()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        # ✅ 只呼叫 pipeline 入口（import 路徑請對齊你檔案放的位置）
        from translation_tool.core.kubejs_translator import run_kubejs_pipeline

        run_kubejs_pipeline(
            input_dir=input_dir,
            output_dir=output_dir,
            session=session,
            dry_run=dry_run,
            step_extract=step_extract,
            step_translate=step_translate,
            step_inject=step_inject,
            write_new_cache=write_new_cache,
        )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] KubeJS 服務失敗：{e}\n{full_traceback}")
        # ✅ UI_LOG_HANDLER 已接好：logger.error 會出現在 UI，所以不用 session.add_log
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)


def run_md_translation_service(
    input_dir: str,
    session,
    output_dir: str | None = None,
    dry_run: bool = False,
    step_extract: bool = True,
    step_translate: bool = True,
    step_inject: bool = True,
    write_new_cache: bool = True,
    lang_mode: str = "non_cjk_only",
):
    update_logger_config()
    try:
        session.start()
        UI_LOG_HANDLER.set_session(session)

        from translation_tool.core.md_translation_assembly import run_md_pipeline

        run_md_pipeline(
            input_dir=input_dir,
            session=session,
            output_dir=output_dir,
            dry_run=dry_run,
            step_extract=step_extract,
            step_translate=step_translate,
            step_inject=step_inject,
            write_new_cache=write_new_cache,
            lang_mode=lang_mode,
        )

        session.finish()

    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[非預期錯誤] MD 流程失敗：{e}\n{full_traceback}")
        session.add_log(f"[非預期錯誤] {e}\n{full_traceback}")
        session.set_error()

    finally:
        UI_LOG_HANDLER.set_session(None)


def run_merge_zip_batch_service(
    zip_paths: list[str],
    output_dir: str,
    session,
    only_process_lang,
):
    """
    以 ZIP 為單位進行合併（支援 generator merge）
    - ZIP 層級 progress
    - merge_zhcn_to_zhtw_from_zip 內部 progress 疊加
    - log / error 完整轉交給 session
    """
     # ⭐ 每次任務開始，都重新讀取一次 config 並設定 Logger
    update_logger_config() 
    UI_LOG_HANDLER.set_session(session)
    try:
        total = len(zip_paths)
        if total == 0:
            session.add_log("[系統] 未選擇任何 ZIP 檔案")
            session.finish()
            return

        for idx, zip_path in enumerate(zip_paths):
            zip_name = Path(zip_path).name
            zip_base_progress = idx / total

            session.add_log(f"[ZIP {idx + 1}/{total}] 開始處理：{zip_name}")

            try:
                # ⚠️ 關鍵：一定要 iterate generator，否則 merge 不會執行
                for update in merge_zhcn_to_zhtw_from_zip(zip_path, output_dir, only_process_lang):
                    # ---- log ----
                    if "log" in update and update["log"]:
                        session.add_log(update["log"])

                    # ---- progress（疊加 ZIP 進度）----
                    if "progress" in update and update["progress"] is not None:
                        merged_progress = zip_base_progress + (
                            update["progress"] / total
                        )
                        session.set_progress(min(merged_progress, 0.999))

                    # ---- error ----
                    if update.get("error"):
                        session.set_error()
                        return

                session.add_log(f"[ZIP {idx + 1}/{total}] 完成：{zip_name}")

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}"
                )
                session.add_log(
                    f"[ZIP {idx + 1}/{total}] 錯誤：{zip_name}\n{e}\n{tb}"
                )
                session.set_error()
                return

            # ZIP 完成後，至少推進一次 progress
            session.set_progress((idx + 1) / total)

        session.finish()

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.add_log(f"[致命錯誤] ZIP 合併失敗：{e}\n{tb}")
        session.set_error()
    
    finally:
    # ⭐ 避免 handler 留著舊 session
        UI_LOG_HANDLER.set_session(None)

def run_manual_lookup_service(name: str) -> str:
    if not is_potential_species_name(name):
        return f"'{name}' 不像是一個有效的學名格式 (例如：Felis catus)。"
    result = lookup_species_name(name)
    return result if result else "在本地快取和線上查詢中均未找到結果。"

def run_batch_lookup_service(json_text: str):
    try:
        names = json.loads(json_text)
        if not isinstance(names, list):
            yield {"log": "錯誤：JSON 內容必須是一個列表 (List)。", "error": True}
            return

        results = {}
        total = len(names)

        first = GLOBAL_LOG_LIMITER.filter({"log": f"開始批次查詢 {total} 個學名...", "progress": 0})
        if first is not None:
            yield first

        for i, name in enumerate(names):
            if is_potential_species_name(name):
                result = lookup_species_name(name)
                results[name] = result if result else "未找到"
            else:
                results[name] = "格式錯誤"

            update = GLOBAL_LOG_LIMITER.filter({"log": f"({i+1}/{total}) 已查詢: {name}", "progress": (i + 1) / total})
            if update is not None:
                yield update

        final = GLOBAL_LOG_LIMITER.filter({
            "log": "--- 批次查詢完成 ---",
            "result": json.dumps(results, indent=2, ensure_ascii=False)
        })
        if final is not None:
            yield final

    except json.JSONDecodeError:
        logger.error( {"log": "輸入的不是有效的 JSON 格式。"})
        yield {"log": "輸入的不是有效的 JSON 格式。", "error": True}
    except Exception as e:
        logger.error( {"log": f"查詢時發生錯誤: {e}"})
        yield {"log": f"查詢時發生錯誤: {e}", "error": True}

def run_bundling_service(input_root_dir: str, output_zip_path: str):
    try:
        for update_dict in bundle_outputs_generator(input_root_dir, output_zip_path):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 打包服務失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_untranslated_check_service(en_dir: str, tw_dir: str, out_dir: str):
    try:
        for update_dict in check_untranslated_generator(en_dir, tw_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 未翻譯檢查失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 未翻譯檢查失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_variant_compare_service(cn_dir: str, tw_dir: str, out_dir: str):
    try:
        for update_dict in compare_variants_generator(cn_dir, tw_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 簡繁差異比較失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 簡繁差異比較失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_english_residue_check_service(input_dir: str, out_dir: str):
    try:
        for update_dict in check_english_residue_generator(input_dir, out_dir):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] 殘留英文檢查失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] 殘留英文檢查失敗：{e}\n{full_traceback}", "error": True, "progress": 0}

def run_variant_compare_tsv_service(tsv_path: str, output_csv_path: str):
    try:
        for update_dict in compare_variants_tsv_generator(tsv_path, output_csv_path):
            filtered = GLOBAL_LOG_LIMITER.filter(update_dict)
            if filtered is not None:
                yield filtered
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"[致命錯誤] TSV 簡繁差異比較失敗：{e}\n{full_traceback}")
        yield {"log": f"[致命錯誤] TSV 簡繁差異比較失敗：{e}\n{full_traceback}", "error": True, "progress": 0}


# ------------------------------------------------------
# ---------------- Cache Manager UI Services -----------
# ------------------------------------------------------
def cache_get_overview_service() -> Dict[str, Any]:
    """
    取得目前所有翻譯快取（cache）的整體概覽資訊。

    回傳內容通常包含：
    - 各 cache_type 的筆數
    - shard 狀態
    - 是否有未寫入磁碟的資料
    - 其他 cache_manager 定義的統計資訊
    """
    return cache_manager.get_cache_overview()


def cache_reload_service() -> Dict[str, Any]:
    """
    重新載入翻譯快取，並重建全域搜尋索引。
    """
    cache_manager.reload_translation_cache()
    cache_manager.rebuild_search_index()
    return cache_manager.get_cache_overview()


def cache_reload_type_service(cache_type: str) -> Dict[str, Any]:
    """只重新載入單一 cache_type，並重建該分類搜尋索引。"""
    cache_manager.reload_translation_cache_type(cache_type)
    cache_manager.rebuild_search_index_for_type(cache_type)
    return cache_manager.get_cache_overview()


def cache_save_all_service(
    write_new_shard: bool = True,
    only_types: list[str] | None = None,
) -> Dict[str, Any]:
    """
    將目前記憶體中的翻譯快取寫回磁碟。

    參數：
    - write_new_shard:
        是否強制寫入新的 shard 檔案（通常用於 rotation 或版本切換）
    - only_types:
        指定只儲存哪些 cache_type；為 None 時代表全部

    流程：
    1. 決定目標 cache_type 清單
    2. 逐一呼叫 cache_manager.save_translation_cache()
    3. 回傳寫入後的 cache 概覽資訊
    """
    # 若未指定 cache 類型，預設處理所有 CACHE_TYPES
    targets = only_types or list(cache_manager.CACHE_TYPES)

    for cache_type in targets:
        # 將指定 cache_type 的快取寫回磁碟
        cache_manager.save_translation_cache(
            cache_type,
            write_new_shard=write_new_shard,
        )

    return cache_manager.get_cache_overview()


def cache_search_service(
    cache_type: str,
    query: str,
    mode: str = "key",
    limit: int = 5000,
) -> Dict[str, Any]:
    """
    在指定的翻譯快取中搜尋條目（已升級使用 FTS5 全文搜尋）。

    參數：
    - cache_type:
        要搜尋的 cache 類型（如 lang / patchouli / md 等）
    - query:
        搜尋字串（大小寫不敏感）
    - mode:
        - "key": 依 key 搜尋（比對 src 欄位）
        - "dst": 依翻譯後文字（dst）搜尋
        - "ALL": 同時搜尋 key 和 dst
    - limit:
        最多回傳幾筆結果，超過會截斷

    回傳格式：
    {
        "items": [
            {
                "key": "<cache key>",
                "rank": <排序用分數>,
                "preview": "<dst 預覽或空字串>",
                "score": <相關度分數，0~1>  # 新增
            }
        ],
        "truncated": <是否因 limit 被截斷>,
        "limit": <使用的 limit>
    }
    """
    # 正規化搜尋字串
    q = (query or "").strip()
    if not q:
        return {"items": [], "truncated": False, "limit": limit}

    try:
        # 嘗試使用新的 FTS5 搜尋引擎（A3 改進）
        results = cache_manager.search_cache(
            query=q,
            cache_type=cache_type,
            limit=limit,
            use_fuzzy=True  # 啟用模糊比對，結果會更精準
        )
        
        if results:
            # 將新搜尋引擎的格式轉換成原有格式（保持向後相容）
            hits = []
            for r in results:
                # 根據 mode 決定要不要這筆結果
                if mode == "key":
                    # Key 模式：只看 src 是否匹配
                    if q.lower() not in r.get('src', '').lower():
                        continue
                elif mode == "dst":
                    # DST 模式：只看 dst 是否匹配
                    if q.lower() not in r.get('dst', '').lower():
                        continue
                # ALL 模式：兩者都可以
                
                # 計算排序分數（向後相容）
                def _rank(text: str) -> int:
                    t = (text or "").lower()
                    if t == q.lower():
                        return 0
                    if t.startswith(q.lower()):
                        return 1
                    return 2
                
                # 優先以 src 作為 key，如果 mode 是 dst 則用 dst 判斷 rank
                rank_text = r.get('dst', '') if mode == "dst" else r.get('src', '')
                
                hits.append({
                    "key": r.get('key', ''),  # cache key（修正！）
                    "rank": _rank(rank_text),
                    "preview": str(r.get('dst', ''))[:40],
                    "score": r.get('combined_score', r.get('score', 0.0))  # 新增相關度分數
                })
            
            # 按 rank 排序（0 最高優先）
            hits.sort(key=lambda x: (x['rank'], -x['score']))
            
            truncated = len(results) >= limit
            
            return {
                "items": hits,
                "truncated": truncated,
                "limit": limit,
            }
    
    except Exception as e:
        # 如果新搜尋引擎失敗，降級使用舊的線性掃描
        log_warning(f"搜尋引擎失敗，降級使用線性掃描: {e}")
    
    # Fallback：舊的線性掃描（保持原有邏輯）
    cache_ref = cache_manager.get_cache_dict_ref(cache_type)
    if not cache_ref:
        return {"items": [], "truncated": False, "limit": limit}

    q_lower = q.lower()
    hits: list[dict] = []
    truncated = False

    def _rank(text: str) -> int:
        t = (text or "").lower()
        if t == q_lower:
            return 0
        if t.startswith(q_lower):
            return 1
        return 2

    for key, entry in cache_ref.items():
        if not isinstance(entry, dict):
            continue

        dst = entry.get("dst", "")

        if mode == "dst":
            hay = (dst or "").lower()
            if q_lower not in hay:
                continue
            hits.append({
                "key": key,
                "rank": _rank(dst),
                "preview": str(dst)[:40],
                "score": 0.5  # 降級模式沒有真正的相關度分數
            })
        else:
            hay = (key or "").lower()
            if q_lower not in hay:
                continue
            hits.append({
                "key": key,
                "rank": _rank(key),
                "preview": "",
                "score": 0.5
            })

        if len(hits) >= limit:
            truncated = True
            break

    return {
        "items": hits,
        "truncated": truncated,
        "limit": limit,
    }


def cache_get_entry_service(cache_type: str, key: str) -> Dict[str, Any] | None:
    """
    取得指定 cache_type 中的單筆快取條目。

    回傳：
    - dict：包含 src / dst / 其他 metadata
    - None：找不到該 key
    """
    return cache_manager.get_cache_entry(cache_type, key)


def cache_update_dst_service(cache_type: str, key: str, new_dst: str) -> bool:
    """
    更新指定快取條目的翻譯結果（dst）。

    行為：
    1. 讀取原本的 cache entry
    2. 保留原始 src
    3. 以 new_dst 覆寫翻譯結果
    4. 寫回記憶體 cache（尚未存檔）

    回傳：
    - True：更新成功
    - False：找不到該 key
    """
    entry = cache_manager.get_cache_entry(cache_type, key)
    if not entry:
        return False

    src = entry.get("src", "")
    cache_manager.add_to_cache(cache_type, key, src, new_dst)
    return True


def cache_rotate_service(cache_type: str) -> bool:
    """
    強制對指定 cache_type 進行 shard rotation。

    通常用途：
    - 當單一 shard 過大
    - 需要切換新 shard 版本
    - 管理 cache 檔案大小與生命週期

    回傳：
    - True / False：依 cache_manager 實作結果
    """
    return cache_manager.force_rotate_shard(cache_type)


def cache_rebuild_index_service() -> Dict[str, Any]:
    """
    重建快取搜尋索引（A3 改進功能）。
    
    功能：
    - 從記憶體快取重建 FTS5 全文搜尋索引
    - 提升搜尋速度（比線性掃描快 10~100 倍）
    - 支援中英文全文搜尋與模糊比對
    
    使用時機：
    - 首次使用搜尋功能時
    - 大量新增/修改快取後
    - 搜尋結果不符預期時
    
    回傳格式：
    {
        "success": <是否成功>,
        "total_indexed": <總共索引幾筆>,
        "message": <訊息>,
        "error": <錯誤訊息（如果失敗）>
    }
    """
    try:
        # 重建索引
        cache_manager.rebuild_search_index()
        
        # 統計總數
        total = sum(
            len(cache_manager.get_cache_dict_ref(ct))
            for ct in cache_manager.CACHE_TYPES
        )
        
        return {
            "success": True,
            "total_indexed": total,
            "message": f"✅ 搜尋索引重建完成，共索引 {total:,} 條翻譯",
            "error": None
        }
    
    except Exception as e:
        log_error(f"重建搜尋索引失敗: {e}")
        return {
            "success": False,
            "total_indexed": 0,
            "message": "重建索引失敗",
            "error": str(e)
        }

