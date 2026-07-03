"""translation_tool/core/lm_translator.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# lm_translator.py
import json as json_std
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, Generator, Optional

import orjson as json

from translation_tool.utils.log_unit import log_info, log_warning, log_debug
from translation_tool.utils.cache_manager import (
    add_to_cache,
    save_translation_cache,
    reload_translation_cache,
    get_cache_dict_ref,
)
from translation_tool.core.lm_translator_main import (
    DEFAULT_DRY_RUN,
    DEFAULT_EXPORT_CACHE_ONLY,
    translate_batch_smart,
)
from translation_tool.core.translation_path_writer import (
    map_lang_output_path,
    set_by_path,
)
from translation_tool.core.lm_config_rules import (
    validate_api_keys,
    value_fully_translated,
)
from translation_tool.core.lm_translator_scan import (
    extract_items_parallel,
    scan_translatable_files,
)
from translation_tool.utils.config_manager import load_config

# ============================================================
# B-3: 快取寫入頻率優化（每 N 個批次才寫一次硬碟）
# ============================================================
BATCH_WRITE_INTERVAL = 5  # 每 N 個批次寫一次硬碟

# ============================================================
# B-4: 斷點續傳機制
# ============================================================
CHECKPOINT_FILE = "logs/translation_checkpoint.json"


def _get_batch_write_interval():
    return load_config().get("lm_translator", {}).get("batch_write_interval", 2)


def save_checkpoint(
    batch_index: int, completed_count: int, total: int, remaining: list, output_dir: str
):
    """寫入 checkpoint（每批次完成後）。

    Args:
        batch_index: 目前處理的批次編號
        completed_count: 已完成的項目數量（用於恢復時計算正確的剩餘切片起點）
        total: 總項目數量
        remaining: 剩餘待翻譯項目清單（用於恢復時取樣比对）
        output_dir: 輸出目錄路徑
    """
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json_std.dump(
            {
                "batch_index": batch_index,
                "completed_count": completed_count,
                "total": total,
                "remaining_sample": remaining[:3]
                if remaining
                else [],  # 只保留前三筆範例，不存完整清單
                "output_dir": output_dir,
            },
            f,
            ensure_ascii=False,
        )


def load_checkpoint() -> dict | None:
    """讀取 checkpoint，若不存在或讀取失敗回傳 None。

    Returns:
        checkpoint 字典，若無 checkpoint 則回傳 None
    """
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json_std.load(f)
    except Exception:
        return None


def clear_checkpoint():
    """清除 checkpoint 檔案（恢复成功后调用）。"""
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


def get_formatted_duration(start_tick: float) -> str:
    """將開始時間轉換為人類可讀的格式。

    Args:
        start_tick: 開始時間（由 time.perf_counter() 取得）

    Returns:
        人類可讀的時間字串，如 "1 小時 30 分 45 秒" 或 "30 分 45 秒"
    """
    # 使用 perf_counter 計算目前時間（高精度、單調遞增）
    current_tick = time.perf_counter()

    # 計算經過的秒數（轉為整數秒）
    duration = int(current_tick - start_tick)

    # 拆解為 小時 / 分 / 秒
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)

    # 超過 1 小時才顯示「小時」欄位
    if hours > 0:
        return f"{hours} 小時 {minutes} 分 {seconds} 秒"
    else:
        return f"{minutes} 分 {seconds} 秒"


# 剩餘時間
def format_duration_seconds(seconds: int) -> str:
    """
    將「秒數」格式化為人類可讀的時間字串。

    用途：
    - ETA（預計剩餘時間）
    - 批次處理剩餘時間顯示
    - 任意以秒為單位的時間估算輸出

    範例：
    - 75        -> "1 分 15 秒"
    - 3661      -> "1 小時 1 分 1 秒"
    - 59        -> "0 分 59 秒"

    設計原則：
    - 不依賴系統時間（僅處理純秒數）
    - 自動處理負值或非整數輸入
    - 小於 1 小時時不顯示「小時」欄位，保持輸出簡潔
    """

    # 安全防護：確保秒數為非負整數
    seconds = max(0, int(seconds))

    # 拆解為 小時 / 分 / 秒
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # 超過 1 小時才顯示「小時」欄位，避免 UI 冗長
    if hours > 0:
        return f"{hours} 小時 {minutes} 分 {seconds} 秒"
    else:
        return f"{minutes} 分 {seconds} 秒"


# ============================================================
# 對外唯一入口（UI / CLI 共用）
# ============================================================


def translate_directory_generator(
    input_dir: str,
    output_dir: str,
    *,
    dry_run: Optional[bool] = None,
    export_lang: bool = False,
    write_new_cache: bool = False,
) -> Generator[Dict[str, Any], None, None]:
    """翻譯目錄的 generator 入口。

    Args:
        input_dir: 輸入目錄路徑
        output_dir: 輸出目錄路徑
        dry_run: 是否為模擬執行（None 使用預設值）
        export_lang: 是否匯出語言檔
        write_new_cache: 是否寫入新快取

    Yields:
        進度字典，包含 progress、log 等資訊

    Note:
        - dry_run=None → 使用 lm_translate_task.py 的 DRY_RUN
        - dry_run=True/False → 由 UI / argparse 覆寫
    """
    # 批次大小決定 LM 送出多少條文字進行翻譯
    INITIAL_BATCH_SIZE_LANG = (
        load_config().get("lm_translator", {}).get("initial_batch_size_lang", 300)
    )
    INITIAL_BATCH_SIZE_PATCHOULI = (
        load_config().get("lm_translator", {}).get("initial_batch_size_patchouli", 100)
    )

    # =========================
    # DRY_RUN 覆寫邏輯（核心）
    # =========================

    # 如果使用者沒有特別指定 dry_run 的值（即為 None），就使用系統全域定義的預設變數 DEFAULT_DRY_RUN；否則，就遵從使用者傳入的值。
    dry_run = DEFAULT_DRY_RUN if dry_run is None else dry_run

    log_debug(
        f"DEBUG [3. Translator Gen]: 接收到的 export_lang 為 -> {export_lang}"
    )  # <--- 加在這裡

    # 驗證API 翻譯key 是否有效
    validate_api_keys()
    # 每次執行都重新讀取快取分片，確保手動修改的快取可立即生效
    reload_translation_cache()
    log_info("[3. Translator Gen]: 重新載入快取完成")

    # 獲取 並建立輸入、輸出路徑
    root = Path(input_dir).resolve()
    out_root = Path(output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    # --- 1. 初始化路徑 Log ---
    msg_init = f"\n📂 輸入資料夾：{root}\n📤 輸出資料夾：{out_root}"
    log_info(msg_init)  # 同步到日誌檔案 (log 檔)
    yield {
        "progress": 0.0,
        # "log": msg_init,
    }

    # =========================
    # 掃描檔案
    # =========================
    try:
        patchouli_files, lang_files, files = scan_translatable_files(root)
    except Exception as e:
        log_warning(f"⚠️ 掃描可翻譯檔案失敗，已跳過本次掃描：{e}")
        patchouli_files, lang_files, files = [], [], []

    msg_scan = f"🔍 掃描完成：Patchouli={len(patchouli_files)}，Lang={len(lang_files)}"
    log_info(msg_scan)  # 同步到日誌檔案 (log 檔)
    yield {
        "progress": 0.0,
        # "log": msg_scan,
    }

    if not files:
        msg_no_files = "⚠️ 未找到任何可翻譯 JSON 檔案"
        log_info(msg_no_files)  # 同步到日誌檔案 (log 檔)
        yield {
            "progress": 1.0,
            # "log": msg_no_files,
        }
        return

    # =========================
    # 抽取可翻譯文字 (並行優化版)
    # =========================
    file_cache: dict[str, dict] = {}
    all_items = []
    translation_log: list[dict] = []

    log_info(f"🚀 開始並行抽取文字 (檔案數量: {len(files)})")
    work_thread = (
        load_config().get("translator", {}).get("parallel_execution_workers", 4)
    )

    # 使用 generator 迭代， 每完成一個檔案就更新一次進度（從 0.0 → 0.2）
    _prev_pct = 0.0
    for file_cache, all_items in extract_items_parallel(
        files=files,
        export_lang=export_lang,
        work_thread=work_thread,
    ):
        # 每處理 5% 的檔案（根據已完成的檔案數，非 items 數），yield 一次 UI 進度
        if len(files) > 0:
            _pct = len(file_cache) / len(files)
            if _pct - _prev_pct >= 0.05 or _pct >= 1.0:
                _prev_pct = _pct
                yield {
                    "progress": 0.2 * _pct,
                    "log": f"✂️ 抽取中... ({len(file_cache)}/{len(files)} 檔)",
                }

    msg_extract = f"✂️ 抽取完成：共 {len(all_items)} 段文字"
    log_info(msg_extract)
    yield {
        "progress": 0.2,
        # "log": msg_extract,
    }

    # ============================================================
    # Cache 命中處理（針對萬筆數據的極速比對版）
    # ============================================================
    cached_items = []  # 快取數據
    items_to_translate = []  # 待翻譯數據

    # 1. 透過正式 façade 取得 live reference，避免直接越權碰 private state
    lang_cache = get_cache_dict_ref("lang")
    patch_cache = get_cache_dict_ref("patchouli")

    log_info(f"⚡ 正在進行 Cache 比對 (總量: {len(all_items)} 筆)...")
    # 開始計時 cache 比對時間
    start_match = time.perf_counter()

    # 2. 分流處理：利用 List Comprehension 快速將項目歸類
    # 這樣後續的 for 迴圈內部邏輯會非常純粹，有利於 CPU 流水線優化
    patch_items = [i for i in all_items if i["cache_type"] == "patchouli"]
    lang_items = [i for i in all_items if i["cache_type"] != "patchouli"]

    # 處理 Patchouli 項目 (Key: path|source_text)
    for item in patch_items:
        src_text = item.get("source_text")
        if not src_text:
            # 防呆：沒有 source_text 就當作一定要翻
            items_to_translate.append(item)
            continue

        unique_key = f"{item['path']}|{src_text}"
        entry = patch_cache.get(unique_key)

        cached_value = entry.get("dst") if isinstance(entry, dict) else None

        if cached_value and value_fully_translated(cached_value):
            item["text"] = cached_value
            cached_items.append(item)
        else:
            items_to_translate.append(item)

    # 處理 Lang 項目 (Key: path)
    for item in lang_items:
        unique_key = item["path"]
        entry = lang_cache.get(unique_key)

        src_text = (
            item.get("source_text") or item.get("text") or ""
        )  # 這行你下面翻譯階段也這樣做
        entry_src = entry.get("src") if isinstance(entry, dict) else None
        entry_dst = entry.get("dst") if isinstance(entry, dict) else None

        # ✅ 只有 src 一致才命中
        if entry_dst and value_fully_translated(entry_dst) and entry_src == src_text:
            item["text"] = entry_dst
            cached_items.append(item)
        else:
            items_to_translate.append(item)

    match_duration = time.perf_counter() - start_match
    msg_cache = f"🧠 Cache 命中 {len(cached_items)} 筆，需翻譯 {len(items_to_translate)} 筆 (耗時: {match_duration:.2f}s)"
    log_info(msg_cache)

    # =========================
    # DEBUG：列出 Cache 命中來源
    # =========================
    if cached_items:
        hit_by_file = defaultdict(list)

        # 先把命中按檔名聚合，避免 log 太長
        for it in cached_items:
            hit_by_file[Path(it["file"]).name].append(it)

        log_debug(
            "🎯 [CACHE HIT] total=%d files=%d", len(cached_items), len(hit_by_file)
        )

        for fname, file_hits in hit_by_file.items():
            log_debug("🎯 [CACHE HIT] %s (%d)", fname, len(file_hits))

            # 將每個檔案的 hits 處理邏輯內嵌到此迴圈，確保完整的檔案對應
            for it in file_hits:
                f = it["file"]
                path = it["path"]
                ctype = it.get("cache_type")

                src_text = it.get("source_text") or it.get("text") or ""
                dst_text = it.get("text") or ""

                if ctype == "patchouli":
                    ukey = f"{path}|{src_text}"
                    entry = patch_cache.get(ukey)
                    key_info = f"patchouli:{ukey}"
                else:
                    ukey = path
                    entry = lang_cache.get(ukey)
                    key_info = f"lang:{ukey}"

                entry_src = entry.get("src") if isinstance(entry, dict) else None
                entry_dst = entry.get("dst") if isinstance(entry, dict) else None

                log_debug(
                    "   - [%s] %s | %s\n"
                    "     key=%s\n"
                    "     src=%r\n"
                    "     dst=%r\n"
                    "     cache.src=%r\n"
                    "     cache.dst=%r",
                    ctype,
                    Path(f).name,
                    path,
                    key_info,
                    src_text,
                    dst_text,
                    entry_src,
                    entry_dst,
                )

    yield {
        "progress": 0.2,
        # "log": msg_cache, # 如果 UI 需要顯示比對結果
    }

    # 輸出 Cache 命中內容 (優化版)
    # 先把所有資料填入 file_cache，最後「一次性」遍歷 touched_files 進行寫入。
    # =========================
    # if cached_items:
    if cached_items and not dry_run:
        touched_files = set()

        # 1. 批次更新記憶體中的 file_cache
        # 這個循環非常快，不用並行
        for item in cached_items:
            f_key = item["file"]
            set_by_path(
                file_cache[f_key],
                item["path"],
                item["text"],
            )
            touched_files.add(f_key)

        # 2. 針對受影響的檔案進行一次性寫入
        # 這裡可以使用 orjson 的快遞優勢
        log_info(f"💾 正在更新 {len(touched_files)} 個受影響的檔案...")

        for file in touched_files:
            src = Path(file)
            # 預先計算相對路徑，減少 Path 物件轉換開銷
            rel = map_lang_output_path(src.relative_to(root))

            # 判斷格式
            is_lang_folder = "lang" in src.parts
            dst_base = out_root / rel

            if export_lang and is_lang_folder:
                # 輸出為 .lang (純文字拼接)
                dst = dst_base.with_suffix(".lang")
                dst.parent.mkdir(parents=True, exist_ok=True)

                # 優化：使用 list comprehension 配合 join，比不斷 lines.append 快
                content = "\n".join([f"{k}={v}" for k, v in file_cache[file].items()])
                dst.write_text(content, encoding="utf-8")
            else:
                # 輸出為 .json
                dst = dst_base
                dst.parent.mkdir(parents=True, exist_ok=True)

                # 使用 orjson 進行極速序列化
                dst.write_bytes(
                    json.dumps(
                        file_cache[file],
                        option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
                    )
                )

        msg_cache_done = f"✅ 已輸出 Cache 命中內容（{len(touched_files)} 個檔案）"
        log_info(msg_cache_done)
        yield {"progress": 0.2}

        if DEFAULT_EXPORT_CACHE_ONLY and not items_to_translate and not dry_run:
            msg_cache_pass = "🎉 僅 Cache 命中，無需翻譯，流程結束"
            log_info(msg_cache_pass)  # 同步到日誌檔案 (log 檔)
            yield {
                "progress": 1.0,
                # "log": msg_cache_pass,
            }
            return

    # =========================
    # DRY RUN（不送 API）
    # =========================
    if dry_run:
        preview_path = out_root / "_dry_run_preview.json"
        preview_path.write_bytes(
            json.dumps(
                items_to_translate,
                option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
            )
        )

        # ✅ 新增：cache 命中清單（不影響翻譯區）
        cache_hit_preview_path = out_root / "_dry_run_cache_hit_preview.json"
        cache_preview = [
            {
                "file": it["file"],
                "path": it["path"],
                "text": it.get("text"),
                "source_text": it.get("source_text"),
                "cache_type": it.get("cache_type"),
            }
            for it in cached_items
        ]
        cache_hit_preview_path.write_bytes(
            json.dumps(cache_preview, option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS)
        )

        lang_count = sum(1 for i in all_items if i["cache_type"] == "lang")
        patch_count = sum(1 for i in all_items if i["cache_type"] == "patchouli")

        msg_dry = (
            f"\n🚧 DRY-RUN 完成，預覽檔已產生\n"
            f"Lang={lang_count}（≈{math.ceil(lang_count / INITIAL_BATCH_SIZE_LANG)} batches）\n"
            f"Patchouli={patch_count}（≈{math.ceil(patch_count / INITIAL_BATCH_SIZE_PATCHOULI)} batches）\n"
            f"📄 待翻譯預覽：{preview_path}\n"
            f"🎯 Cache 命中預覽：{cache_hit_preview_path}"
        )
        log_info(msg_dry)

        yield {"progress": 1.0}
        return

    # =========================
    # 正式翻譯流程
    # =========================
    total = len(items_to_translate)

    # 1. 先判斷是否需要翻譯 (預防除以零)
    if total == 0:
        msg_finish = "🎉 所有項目皆已從 Cache 恢復，無需翻譯。"
        log_info(msg_finish)
        yield {"progress": 1.0, "log": msg_finish}
        return

    # 2. 確定有東西要翻，再發送開始訊息
    msg_start = f"🚀 開始翻譯（共 {total} 筆）"
    log_info(msg_start)
    yield {
        "progress": 0.2,
        # "log": msg_start,
    }

    remaining = items_to_translate[:]
    total = len(remaining)
    processed = 0
    batch_index = 1
    start_time = time.perf_counter()

    # ============================================================
    # B-4: 斷點續傳 - 嘗試從 checkpoint 恢復
    # ============================================================
    checkpoint = load_checkpoint()
    if checkpoint:
        cp_completed = checkpoint.get("completed_count", 0)
        cp_total = checkpoint.get("total", 0)
        # 檢查 checkpoint 的 completed_count 是否合理（completed <= 原始 total）
        if cp_completed <= total and cp_total == total:
            log_info(f"🔄 偵測到 checkpoint，已完成 {cp_completed}/{total} 筆")
            # 從 items_to_translate 的正確偏移位置恢復剩餘清單
            remaining = items_to_translate[cp_completed:]
            batch_index = checkpoint.get("batch_index", 1)
            log_info(
                f"✅ checkpoint 已載入，將從 Batch {batch_index} 繼續翻譯（remaining={len(remaining)} 筆）"
            )
        elif cp_completed > total:
            log_warning(
                f"⚠️ checkpoint 數量異常（已完成 {cp_completed} > 總數 {total}），忽略並重新開始"
            )
            clear_checkpoint()
        else:
            # total 不一致，但 completed_count 合理，可能是檔案結構變更，仍從頭開始
            log_warning(
                f"⚠️ checkpoint 與目前總數不一致（checkpoint total={cp_total}，current total={total}），忽略並重新開始"
            )
            clear_checkpoint()

    # B-3: 快取寫入頻率優化 - 批次計數器
    _batch_write_counter = 0
    # 進度更新頻率：每 yield_every 筆翻譯更新一次（約 50 次 / 總任務）
    yield_every = max(1, total // 50)
    _item_idx = 0

    while remaining:
        is_lang = remaining[0]["cache_type"] == "lang"
        batch_size = (
            INITIAL_BATCH_SIZE_LANG if is_lang else INITIAL_BATCH_SIZE_PATCHOULI
        )
        batch = remaining[:batch_size]

        # ⭐ 1. 接收 status (原本是 _, 現在改為 status)
        translated, status = translate_batch_smart(batch, total)
        log_debug("翻譯結果：%s", translated)
        log_debug("翻譯狀態：%s", status)

        # ⭐ 2. 判斷是否發生「額度用盡」或「無法翻譯」的情況
        is_interrupted = status in ["PARTIAL", "FAILED", "ALL_KEYS_EXHAUSTED"]
        # ⭐ 3. 安全檢查：防止 translated 為 None 導致後面的 for item in translated 崩潰
        safe_translated = translated if translated is not None else []
        # log_debug("安全翻譯結果：",safe_translated)

        # 無論是否中斷，只要有翻出來的東西 (translated)，就先處理掉
        touched_files = set()

        # 建立一個快速對照表，避免在迴圈內反覆搜尋
        # 用 (file, path) 作為 key 來比對原始文本
        # src_map = {(i["file"], i["path"]): i["text"] for i in batch}
        # src_map = {(i["file"], i["path"]): i.get("source_text") for i in batch}
        src_map = {
            (i["file"], i["path"]): (i.get("source_text") or i.get("text") or "")
            for i in batch
        }

        for item in safe_translated:
            _item_idx += 1
            processed += 1
            # 每處理的 N 筆 yield 一次進度（約 50 次 / 總任務）
            if _item_idx % yield_every == 0:
                yield {"progress": min(0.2 + 0.8 * (processed / total), 1.0)}
            file = item["file"]
            path = item["path"]
            text = item["text"]
            c_type = item["cache_type"]

            # 從對照表取得原始文字
            src_text = src_map.get((file, path))  # 取得原文

            if src_text:
                # 1. 紀錄到 Log
                translation_log.append(
                    {
                        "file": file,
                        "path": path,
                        "cache_type": c_type,
                        "source": src_text,
                        "translated": text,
                    }
                )

            # --- 第一步：登記到快取管理員 (這決定了存檔有沒有內容) ---
            # 2. 存入記憶體快取
            if src_text and src_text.strip():
                if c_type == "patchouli":
                    # Patchouli 的文本可能隨 path 變動，故使用組合 Key
                    u_key = f"{path}|{src_text}"
                    add_to_cache("patchouli", u_key, src_text, text)
                    log_debug(
                        "加入快取 [type=%s] key=%s",
                        "patchouli",
                        u_key,
                    )
                else:
                    add_to_cache("lang", path, src_text, text)
                    log_debug(
                        "加入快取 [type=%s] key=%s",
                        "lang",
                        path,
                    )

            # --- 第二步：更新準備輸出的遊戲 JSON 物件 ---
            set_by_path(file_cache[file], path, text)
            touched_files.add(file)

        # --- 第三步：根據類型「定向存檔」 (優化效能) ---
        # B-3/B-4 的實際寫入會在 remaining 更新後執行（見下方）

        # ⭐ checkpoint：立刻寫檔
        for file in touched_files:
            src = Path(file)
            rel = map_lang_output_path(src.relative_to(root))

            # 檢查是否為語言檔案 (通常路徑包含 /lang/)
            # 以及使用者是否要求輸出 .lang 格式
            is_lang_folder = "lang" in src.parts
            log_debug(
                f"DEBUG [4. Main Logic]: 正在處理檔案 {src.name}, export_lang={export_lang}, is_lang_folder={is_lang_folder}"
            )

            if export_lang and is_lang_folder:
                # 變更副檔名為 .lang
                log_debug("DEBUG [4. Action]: 執行輸出為 .lang 格式")
                dst = (out_root / rel).with_suffix(".lang")
                dst.parent.mkdir(parents=True, exist_ok=True)

                # 將 dict 轉換為 key=value 格式
                lines = []
                for k, v in file_cache[file].items():
                    lines.append(f"{k}={v}")
                content = "\n".join(lines)
                dst.write_text(content, encoding="utf-8")
            else:
                # 預設行為：輸出 .json
                log_debug("DEBUG [4. Action]: 執行輸出為 .json 格式")
                dst = out_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(
                    json.dumps(
                        file_cache[file],
                        option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
                    )
                )

        # 更新進度數值
        # 注意：如果中斷了，這批次可能只翻了部分，len(translated) 才是真正完成的數量
        # actual_processed = len(translated)
        actual_processed = len(safe_translated)

        # ✅ 只移除真的翻過的
        # 移動剩餘指標
        # remaining = remaining[len(translated):]
        remaining = remaining[actual_processed:]

        # ============================================================
        # B-3: 快取寫入頻率優化 - 每 N 個批次才寫一次硬碟
        # ============================================================
        _batch_write_counter += 1
        batch_write_interval = _get_batch_write_interval()
        if _batch_write_counter % batch_write_interval == 0 or len(remaining) == 0:
            if is_lang:
                save_translation_cache("lang", write_new_shard=write_new_cache)
                log_debug("lang cache written every %d batches", batch_write_interval)
            else:
                save_translation_cache("patchouli", write_new_shard=write_new_cache)
                log_debug("patchouli cache written every %d batches", batch_write_interval)
            _batch_write_counter = 0  # reset counter

        # ============================================================
        # B-4: 斷點續傳 - 每批次完成後寫入 checkpoint
        # ============================================================
        save_checkpoint(batch_index, processed, total, remaining, str(out_root))

        # 計算 ETA
        elapsed = time.perf_counter() - start_time
        avg_per_item = elapsed / processed if processed > 0 else 0.0
        eta_seconds = int(len(remaining) * avg_per_item)

        # 組合進度訊息
        current_type = "Lang" if is_lang else "Patchouli"

        ## 如果沒被中斷，就顯示時間；否則顯示空字串
        eta_text = (
            f"預計剩餘: {format_duration_seconds(eta_seconds)}"
            if (not is_interrupted and processed > 0)
            else ""
        )

        progress_msg = (
            f"[{current_type} - Batch {batch_index}] "
            f"{'✅ 成功' if not is_interrupted else '⚠️ 部分完成並停止'} | "
            f"進度: {processed}/{total} ({(processed / total):.1%})"
            + (f" | {eta_text}" if eta_text else "")
        )

        log_info(progress_msg)

        # 3. 回傳給 UI 或 CLI
        yield {
            "progress": min(0.2 + 0.8 * (processed / total), 1.0),
            "log": progress_msg,
        }

        # ⭐ 5. 如果中斷，發送最後訊息並跳出
        if is_interrupted:
            stop_msg = "⚠️ 翻譯中斷：部分批次失敗。"
            if status == "ALL_KEYS_EXHAUSTED":
                stop_msg = "❌ 所有 API Key 額度已耗盡，已儲存進度並停止翻譯。"
            elif status == "FAILED":
                stop_msg = "❌ 發生嚴重錯誤，無法繼續翻譯。"

            log_warning(stop_msg)
            yield {"progress": processed / total if total > 0 else 1.0}
            break  # 結束 while 迴圈

        batch_index += 1

    # ============================================================
    # 這裡就是迴圈結束後會跑的地方 (不論是正常翻完，還是被 break)
    # ============================================================

    # B-4: 翻譯結束後清除 checkpoint（無論成功或中斷都清除）
    clear_checkpoint()

    duration = get_formatted_duration(start_time)

    final_logs = []

    if translation_log:
        table_path = out_root / "translation_map.json"
        table_path.write_bytes(
            json.dumps(
                translation_log,
                option=json.OPT_INDENT_2 | json.OPT_NON_STR_KEYS,
            )
        )
        msg_table = f"📘 翻譯對照表已輸出：{table_path}"
        final_logs.append(msg_table)
        log_info(msg_table)

    # 判斷是正常完成還是中斷完成
    if processed < total:
        final_status_msg = f"⚠️ 翻譯中斷，僅完成 {processed}/{total} 筆，耗時 {duration}"
    else:
        final_status_msg = f"🎉 翻譯完全完成，耗時 {duration} "

    final_logs.append(final_status_msg)
    log_info(final_status_msg)

    yield {
        "progress": 1.0 if processed >= total else processed / total,
        "log": "\n".join(final_logs),
    }
