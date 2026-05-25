"""translation_tool/core/lm_translator_main.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import time

import requests

from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_config_rules import (
    get_current_key_index,  # 取得目前 Key 索引（向後相容）
    get_current_api_key,  # 取得目前使用中的 key
    rotate_api_key,  # 輪替 key
)
from translation_tool.core.lm_response_parser import safe_json_loads
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info, log_warning, log_error, log_debug

# =========================================================
# Time Constants - 時間相關常數
# =========================================================
RPM_COOLDOWN_SEC = 12  # RPM 限制冷卻秒數
OVERLOAD_RETRY_WAIT_SEC = 12  # Overload 重試等待秒數

# =========================================================
# Size Constants - 大小相關常數
# =========================================================
MIN_LANG_BATCH_SIZE = 20  # Lang 類型最小批次大小
DEFAULT_BATCH_SIZE = 50  # 預設批次大小


# =========================================================
# 預設參數
# =========================================================
DEFAULT_DRY_RUN = False  # 預設不跳過 API
DEFAULT_EXPORT_CACHE_ONLY = False  # 預設進行完整翻譯

# =========================================================
# 翻譯入口函數（新結構）
# =========================================================


def translate_batch_smart(
    batch_items,
    total=None,
    dry_run: bool = DEFAULT_DRY_RUN,
    export_cache_only: bool = DEFAULT_EXPORT_CACHE_ONLY,
):
    """
    智慧批次翻譯函數（主入口）

    參數:
        batch_items: 翻譯項目列表
        total: 總項目數（可選）
        dry_run: True = 不呼叫API，只模擬流程（測試用）
        export_cache_only: True = 只輸出快取中的內容

    職責：協調各子流程，不直接處理細節
    """
    # 1. 驗證與正規化
    items = _validate_batch_items(batch_items)
    if not items:
        return [], "AUTO"

    # 2. 偵測 profile（TODO: 舊函數會重新計算，目前是被丟棄的死碼）
    # batch_profile = _detect_batch_profile(items)

    # 3. 計算批次大小（TODO: 舊函數會重新計算，目前是被丟棄的死碼）
    # batch_size = _calculate_batch_size(batch_profile)

    # 4. 執行翻譯
    results, status = _execute_translation(items, total, dry_run, export_cache_only)

    # 5. 處理輸出
    return _process_output(results, status)


def _validate_batch_items(items):
    """
    驗證與正規化輸入資料

    參數：
        items: 原始項目列表
    回傳：
        驗證後的項目列表
    """
    if not items:
        return []

    validated = []
    for item in items:
        # 跳过无效项目
        if not isinstance(item, dict):
            continue
        # 跳过空文本
        text = item.get("text", "")
        if not text or not str(text).strip():
            continue

        # 確保有 cache_type
        if "cache_type" not in item:
            item["cache_type"] = "patchouli"

        validated.append(item)

    return validated


def _execute_translation(items, total, dry_run=False, export_cache_only=False):
    """
    執行翻譯主循環

    參數：
        items: 項目列表
        total: 總項目數
        dry_run: 是否為測試模式（不呼叫 API）
        export_cache_only: 是否只輸出快取
    回傳：
        (結果列表, 狀態字串)
    """
    # 代理到舊函數（正確傳遞所有參數）
    return translate_batch_smart_old(items, total, dry_run, export_cache_only)


def _process_output(results, status):
    """
    處理輸出結果

    參數：
        results: 翻譯結果（可能是元組或列表）
        status: 翻譯狀態
    回傳：
        (結果列表, 狀態字串)
    """
    # 處理元組情況（從舊函數返回）
    if isinstance(results, tuple):
        return results

    # 處理 None（代表 ALL_KEYS_EXHAUSTED），不要被當成空結果
    if results is None:
        return [], status

    # 處理空結果（真正沒有結果）
    if not results:
        return [], status  # 保留原始 status，不強制改成 AUTO

    return results, status


# =========================================================
# 舊翻譯函數（保留原邏輯）
# =========================================================


def translate_batch_smart_old(
    batch_items, total=None, dry_run=False, export_cache_only=False
):
    """
    智慧型分批翻譯函式
    支援動態縮減 Batch Size、模型切換、以及自動處理輸出截斷問題。
    """
    # dry_run：模擬翻譯流程，不呼叫任何 API
    if dry_run:
        return [], "DRY_RUN"

    # export_cache_only：目前與 dry_run 等效（快取實作後再擴充）
    if export_cache_only:
        return [], "EXPORT_CACHE_ONLY"

    # 統一從 lm_cfg 讀取 batch size 設定
    lm_cfg = load_config().get("lm_translator", {})

    # 起始 batch（Lang）
    INITIAL_BATCH_SIZE_LANG = lm_cfg.get("initial_batch_size_lang", 200)

    # Patchouli / 其他（預設小）
    INITIAL_BATCH_SIZE_PATCHOULI = lm_cfg.get("initial_batch_size_patchouli", 100)

    # ✅ 新增：FTB / KubeJS 專用 batch 上限（你可以在 config 調整）
    INITIAL_BATCH_SIZE_FTB = lm_cfg.get("initial_batch_size_ftb", 100)
    INITIAL_BATCH_SIZE_KUBEJS = lm_cfg.get("initial_batch_size_kubejs", 200)
    INITIAL_BATCH_SIZE_MD = lm_cfg.get("initial_batch_size_md", 100)

    # ATK-A-6: 動態 RPM 等待時間（可從 config 設定，預設用 module-level 常數）
    rpm_cooldown_sec = lm_cfg.get("rpm_cooldown_sec", RPM_COOLDOWN_SEC)
    key_rotation_buffer_sec = lm_cfg.get("key_rotation_buffer_sec", 5)
    overload_retry_sec = lm_cfg.get("overload_retry_sec", OVERLOAD_RETRY_WAIT_SEC)
    request_interval_sec = lm_cfg.get("request_interval_sec", 4)

    remaining_items = list(batch_items)  # 尚未處理的
    # ⭐ 已成功送出的 API 次數
    completed_calls = 0
    all_results = []  # 累積所有翻譯結果
    # ⭐⭐⭐ 新增：503 連續過載計數器（放在 while 迴圈外）
    overload_retry_count = 0

    # 判斷這批次類型（影響 System Prompt 與 batch 上限）
    def _norm_file(item):
        return str(item.get("file", "")).replace("\\", "/").lower()

    def detect_batch_profile(items):
        # ✅ 優先用 cache_type（最可靠）
        cache_types = [
            str(i.get("cache_type", "")).lower() for i in items if isinstance(i, dict)
        ]
        cache_types = [c for c in cache_types if c]

        if cache_types:
            # 如果整批都是同一種 cache_type，就直接採用
            uniq = set(cache_types)
            if len(uniq) == 1:
                ct = next(iter(uniq))
                if ct in ("lang", "patchouli", "ftbquests", "kubejs", "md"):
                    return (
                        "lang" if ct == "lang" else ("ftb" if ct == "ftbquests" else ct)
                    )

            # 混合批次：優先級（你可以調）
            if "lang" in cache_types:
                return "lang"
            if "ftbquests" in cache_types:
                return "ftb"
            if "kubejs" in cache_types:
                return "kubejs"
            if "md" in cache_types:
                return "md"
            if "patchouli" in cache_types:
                return "patch"

        # ⬇️ fallback：沿用你原本的檔案路徑判斷
        files = [_norm_file(i) for i in items if isinstance(i, dict)]
        if files and all("/lang/" in f for f in files):
            return "lang"
        if any("/ftbquests/" in f for f in files):
            return "ftb"
        if any("/kubejs/" in f for f in files):
            return "kubejs"
        if any("/md/" in f for f in files):
            return "md"
        return "patch"

    batch_profile = detect_batch_profile(batch_items)
    is_lang = batch_profile == "lang"

    if batch_profile == "lang":
        max_bs = INITIAL_BATCH_SIZE_LANG
    elif batch_profile == "ftb":
        max_bs = INITIAL_BATCH_SIZE_FTB
    elif batch_profile == "kubejs":
        max_bs = INITIAL_BATCH_SIZE_KUBEJS
    elif batch_profile == "md":
        max_bs = INITIAL_BATCH_SIZE_MD
    else:
        max_bs = INITIAL_BATCH_SIZE_PATCHOULI

    batch_size = min(len(batch_items), max_bs)

    # 記錄原始總量，用於進度顯示
    original_total = total  # 外部總量

    # 模型導入設定
    models_cfg = load_config().get("lm_translator", {}).get("models", {})
    # 目前使用模型序列
    MODEL_POOL = [name for name, cfg in models_cfg.items() if cfg.get("enabled", False)]

    # 模型溫度
    MODEL_TEMP = load_config().get("lm_translator", {}).get("temperature", 0.2)

    # 使用提示詞 手冊（確保為字串）
    _patchouli_raw = (
        load_config()
        .get("lm_translator", {})
        .get("patchouli_system_prompt", "你是專業的 Minecraft Patchouli 手冊翻譯員")
    )
    if isinstance(_patchouli_raw, str):
        PATCHOULI_SYSTEM_PROMPT = _patchouli_raw
    elif isinstance(_patchouli_raw, dict):
        # 支援 {"content": "..."} 或 {"text": "..."} 格式的 dict
        PATCHOULI_SYSTEM_PROMPT = (
            _patchouli_raw.get("content")
            or _patchouli_raw.get("text")
            or str(_patchouli_raw)
        )
    else:
        PATCHOULI_SYSTEM_PROMPT = str(_patchouli_raw)

    # 使用提示詞 lang（確保為字串）
    _lang_raw = (
        load_config()
        .get("lm_translator", {})
        .get("lang_system_prompt", "你正在翻譯 Minecraft 語言檔案（JSON格式）。")
    )
    if isinstance(_lang_raw, str):
        LANG_SYSTEM_PROMPT = _lang_raw
    elif isinstance(_lang_raw, dict):
        LANG_SYSTEM_PROMPT = (
            _lang_raw.get("content") or _lang_raw.get("text") or str(_lang_raw)
        )
    else:
        LANG_SYSTEM_PROMPT = str(_lang_raw)

    pinned_model_index = None  # None = 正常模式，非 None = 鎖定指定模型
    # 進入動態 Batch 迴圈
    while remaining_items:
        hit_rpm = False  # ⭐ 新增：是否因 RPM 而切換模型
        success_this_round = False  # ⭐ 新增
        hit_overload_retry = False  # ⭐ 新增：標記是否因為 503 需要原地重試
        current_batch = remaining_items[:batch_size]

        # 建立一個臨時對照表，用來把 ID 對應回原始物件  改成 ID 對照
        id_to_item_map = {str(i): item for i, item in enumerate(current_batch)}

        # [DEBUG] 記錄發送摘要
        first_path = current_batch[0]["path"] if current_batch else "N/A"
        log_debug(
            f"[🔍 DEBUG] 準備發送 Payload: 總量={len(current_batch)} | ID 範圍: 0-{len(current_batch) - 1} | 起點: {first_path}"
        )

        payload = {
            "items": [
                {
                    "id": str(i),  # 使用簡單的字串 ID
                    "value": item["text"],
                }
                for i, item in enumerate(current_batch)
            ]
        }

        # 遍歷可用模型池
        # for model_name in MODEL_POOL:
        model_indices = (
            [pinned_model_index]
            if pinned_model_index is not None
            else range(len(MODEL_POOL))
        )

        for i in model_indices:
            model_name = MODEL_POOL[i]
            try:
                # print(f"[→] 嘗試模型 {model_name} | Batch={batch_size}/{original_total} | 類型={'Lang' if is_lang else 'Patch'}")
                # log_info(f"[→] 嘗試模型 {model_name} | Batch={batch_size}/{original_total} | 類型={'Lang' if is_lang else 'Patch'}")

                profile_name = {
                    "lang": "Lang",
                    "ftb": "FTB",
                    "kubejs": "KubeJS",
                    "md": "MD",
                    "patch": "Patch",
                }.get(batch_profile, batch_profile)
                # log_info(f"[→] 嘗試模型 {model_name} | Batch={batch_size}/{original_total} | 類型={profile_name}")
                log_info(
                    f"[→] 嘗試模型 {model_name} | "
                    f"Batch={len(current_batch)}/{max_bs} | "
                    f"翻譯總量={original_total} | "
                    f"類型={profile_name}"
                )

                # 選擇對應的 System Prompt
                # prompt = LANG_SYSTEM_PROMPT if is_lang else PATCHOUI_SYSTEM_PROMPT

                # 選擇對應的 System Prompt
                if batch_profile in ("lang", "kubejs"):
                    prompt = LANG_SYSTEM_PROMPT
                else:
                    # ftb / patchouli / 其他
                    prompt = PATCHOULI_SYSTEM_PROMPT

                log_debug(
                    "Batch profile=%s -> System Prompt=%s",
                    batch_profile,
                    "LANG" if prompt is LANG_SYSTEM_PROMPT else "PATCHOUI",
                )

                raw_text = call_gemini_requests(
                    model_name=model_name,
                    system_prompt=prompt,
                    payload=payload,
                    api_key=get_current_api_key(),
                    temperature=MODEL_TEMP,
                ).strip()

                # ✅ 空內容檢查（改成 raw_text）
                if not raw_text:
                    # print(f"[!] 模型 {model_name} 回傳空內容，切換模型...")
                    log_info(f"[!] 模型 {model_name} 回傳空內容，切換模型...")
                    continue

                # --- 核心改進：檢查輸出是否被截斷（ATK-B-1）---
                def _is_truncated(text: str) -> bool:
                    """判斷 API 回應是否被截斷。

                    三層檢查：
                    1. 嘗試直接解析 JSON（最準確）
                    2. JSON 失敗時，檢查大括號平衡（} 比 { 先出現代表截斷）
                    3. 簡單檢查結尾是否完整
                    """
                    try:
                        import json

                        json.loads(text)
                        return False  # 成功解析，代表沒截斷
                    except json.JSONDecodeError:
                        pass
                    # 大括號平衡檢查
                    count = 0
                    for ch in text:
                        if ch == "{":
                            count += 1
                        elif ch == "}":
                            count -= 1
                        if count < 0:
                            return True  # } 比 { 先出現，代表截斷
                    return count != 0  # 括號不平衡代表截斷

                if _is_truncated(raw_text):
                    overload_retry_count = 0  # 重置過載計數器
                    log_info(
                        "[!] 偵測到 JSON 被截斷（結尾不完整或格式錯誤），將縮小 Batch 重試"
                    )
                    break

                # 解析 JSON
                parsed = safe_json_loads(raw_text)
                # print(raw_text) # 除錯用：印出原始回傳內容

                # 1. 將任何模型輸出標準化為 {id: text}
                # ===============================
                normalized_translations = {}

                if isinstance(parsed, dict):
                    if "items" in parsed:  # 標準格式
                        for item in parsed["items"]:
                            if "id" in item and "value" in item:
                                normalized_translations[str(item["id"])] = item["value"]

                    elif {"file", "path", "text"} <= parsed.keys():  # 單一物件
                        normalized_translations["0"] = parsed["text"]

                    else:  # 簡化格式 {"0":"...", "1":"..."}
                        for k, v in parsed.items():
                            normalized_translations[str(k)] = v

                elif isinstance(parsed, list):
                    for i, item in enumerate(parsed):
                        if isinstance(item, dict):
                            res_id = str(item.get("id", i))
                            normalized_translations[res_id] = item.get(
                                "value", item.get("text", "")
                            )

                # ===============================
                # 2. 漏翻檢查
                # ===============================
                sent_count = len(id_to_item_map)
                received_count = len(normalized_translations)

                if received_count < sent_count:
                    missing_ids = list(
                        set(id_to_item_map.keys()) - set(normalized_translations.keys())
                    )
                    log_warning(
                        f"[❌ 漏翻] 送出 {sent_count} 條，實收 {received_count} 條"
                    )
                    log_warning(f"[❌ 缺失 ID] {missing_ids[:5]}")
                    if missing_ids:
                        log_debug(
                            f"[🔍 缺失範例] {id_to_item_map[missing_ids[0]]['path']}"
                        )
                    break

                # ===============================
                # 3. 精準還原
                # ===============================
                merged_result = []
                lazy_count = 0

                for temp_id, original_item in id_to_item_map.items():
                    new_item = original_item.copy()
                    translated_text = normalized_translations.get(
                        temp_id, original_item["text"]
                    )

                    if translated_text == original_item["text"] and any(
                        c.isalpha() for c in translated_text
                    ):
                        lazy_count += 1
                        if lazy_count <= 3:
                            log_debug(f"[⚠️ 疑似未翻] {original_item['path']}")

                    # ATK-B-2: 翻譯品質驗證
                    # 1. 空翻譯
                    if not translated_text or translated_text.strip() == "":
                        log_warning(
                            "[⚠️ 空翻譯] path=%s：原文='%s'",
                            original_item["path"],
                            original_item["text"],
                        )
                    # 2. 異常長度（翻譯後長度是原文 3 倍以上）
                    orig_len = len(original_item["text"])
                    if orig_len > 0 and len(translated_text) / orig_len > 3:
                        log_warning(
                            f"[⚠️ 異常長度] {original_item['path']}：原文 {orig_len} 字，翻譯 {len(translated_text)} 字"
                        )

                    new_item["text"] = translated_text
                    merged_result.append(new_item)

                if lazy_count > 0:
                    log_info(
                        f"[📊 本批次疑似未翻，建議Cache內容查詢 {lazy_count}/{sent_count}]"
                    )

                result = merged_result

                log_info(f"[✓] 成功取得翻譯：{model_name}")

                completed_calls += 1

                # ⭐ 累積結果
                all_results.extend(result)

                # ⭐ 先把已處理的 batch 移除
                remaining_items = remaining_items[batch_size:]

                # ⭐ 再計算剩餘數量（這時才準）
                remaining_count = len(remaining_items)

                # ⭐ 動態調整 batch_size
                batch_size = min(batch_size, remaining_count)
                overload_retry_count = 0
                success_this_round = True  # ⭐⭐⭐ 關鍵 ：標記本輪成功
                pinned_model_index = None  # ⭐ 解鎖 模型

                if remaining_count == 0:
                    # log_info(f"📊 已完成 API 呼叫：{completed_calls} 次 | 所有 items 已完成")
                    log_info(
                        f"📊 本批次已完成：calls={completed_calls} | 本批 items={len(batch_items)}"
                    )
                    # 免費層保護
                    log_info("⏳ 等待 12 秒以避免觸發 RPM 限制…")
                    time.sleep(rpm_cooldown_sec)
                # else: #本批次 進來不會進來這裡處理
                #    remaining_calls_estimated = math.ceil(
                #        remaining_count / max(batch_size, 1)
                #    )

                #    eta_sec = int(remaining_calls_estimated * avg_time_per_call)
                #    eta_min = eta_sec // 60
                #    eta_sec = eta_sec % 60

                #    log_info(
                #        f"📊 已完成 API 呼叫：{completed_calls} 次 | "
                #        f"剩餘 items：{remaining_count} | "
                #        f"目前 batch={batch_size} | "
                #        f"ETA ≈ {eta_min}m {eta_sec}s"
                #    )
                #    # 免費層保護
                #    log_info("⏳ 等待 12 秒以避免觸發 RPM 限制…")
                #    time.sleep(rpm_cooldown_sec)
                #

                # ⭐ 如果已經沒有剩餘項目，直接結束 while
                if not remaining_items:
                    log_info("✅ 所有項目已翻譯完成")
                    return all_results, "AUTO"

                break  # 跳出 model loop

            except Exception as e:
                status = None
                if isinstance(e, requests.HTTPError) and e.response is not None:
                    status = e.response.status_code

                # ⭐⭐⭐ 這一行是關鍵
                if status != 503:
                    overload_retry_count = 0  # 重置過載計數器
                    pinned_model_index = None  # ⭐ 解除鎖定

                """
                只有「連續的 overloaded」才累積
                中間只要出現別的錯誤就要歸零
                """

                # ========== 404 ==========
                if status == 404:
                    log_info(f"[⛔] 模型 {model_name} 不存在或無法使用，跳過此模型")
                    break  # ⭐ 跳離迴圈

                # ========== 403 ==========
                if status == 403:
                    log_info(
                        f"❌ 403 PERMISSION_DENIED：API Key 無權限 (index {get_current_key_index()})"
                    )
                    if not rotate_api_key():
                        log_error("[❌] 所有 API Key 均無權限 → 回傳 PARTIAL 保護進度")
                        return all_results, "PARTIAL"
                    continue  # 換模型

                # ========== 400 ==========
                if status == 400:
                    msg = str(e).lower()
                    if "failed_precondition" in msg:
                        raise RuntimeError(
                            "❌ FAILED_PRECONDITION：此地區未啟用 Gemini API 免費方案，請啟用付費"
                        )
                    log_info(
                        "[⚠️] 400 INVALID_ARGUMENT：payload 格式錯誤或過大，縮小 batch"
                    )
                    break  # ⭐ 交給 batch shrink

                # ========== 429 RESOURCE_EXHAUSTED ==========
                if status == 429:
                    try:
                        # 1. 嘗試解析 JSON 錯誤格式
                        error_json = e.response.json().get("error", {})
                        remote_msg = error_json.get("message", "").upper()

                        # 2. 從細節中抓取具體的 Quota ID (這是判斷 RPD/RPM 的關鍵)
                        details = error_json.get("details", [])
                        quota_id = ""
                        retry_after = 0

                        for detail in details:
                            # 判斷是否為每日限額 (Log 中出現的 GENERATEREQUESTSPERDAY...)
                            if (
                                detail.get("@type")
                                == "type.googleapis.com/google.rpc.QuotaFailure"
                            ):
                                quota_id = (
                                    detail.get("violations", [{}])[0]
                                    .get("quotaId", "")
                                    .upper()
                                )

                            # 抓取 Google 建議的重試等待秒數 (Log 中的 RETRYDELAY)
                            if (
                                detail.get("@type")
                                == "type.googleapis.com/google.rpc.RetryInfo"
                            ):
                                retry_delay_str = detail.get(
                                    "retryDelay", "0s"
                                ).replace("s", "")
                                retry_after = int(float(retry_delay_str))

                        # 3. 根據 Quota ID 進行分類處理
                        if "PERDAY" in quota_id or "DAILY" in remote_msg:
                            # 情況 A：每日額度 (RPD) 滿了 (Log 顯示：GENERATEREQUESTSPERDAY...)
                            log_warning(
                                f"[🚫] 每日限額已滿 (RPD)：Key Index {get_current_key_index()} 今日失效"
                            )
                            hit_rpm = True
                            # ⭐ 檢查換 Key 是否成功
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                        elif "PERMINUTE" in quota_id or "RPM" in remote_msg:
                            # 情況 B：每分鐘頻率 (RPM) 太快
                            wait_time = retry_after if retry_after > 0 else 10
                            log_info(
                                f"[⏳] 每分鐘頻率限制 (RPM)：稍後重試，預計等待 {wait_time} 秒"
                            )
                            time.sleep(wait_time)
                            hit_rpm = True
                            continue

                        else:
                            # 情況 C：其他或未知的 429 (例如 Free Tier 的總請求限制)
                            log_warning(
                                f"[❓] 偵測到 429 限制 ({quota_id if quota_id else remote_msg})，嘗試切換 Key"
                            )
                            hit_rpm = True
                            # ⭐ 檢查換 Key 是否成功
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                    except Exception as parse_err:
                        # 備援比對邏輯
                        err_msg = str(e).upper()
                        log_error(f"[⚠️] 無法解析 429 JSON，使用備援。錯誤: {parse_err}")

                        if "QUOTA" in err_msg or "EXCEEDED" in err_msg:
                            # ⭐ 這裡之前會崩潰，現在這樣改就安全了
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                        # 兜底處理
                        if not rotate_api_key():
                            return None, "ALL_KEYS_EXHAUSTED"
                        continue

                # ========== 504 ==========
                if status == 504:
                    log_info(
                        "[⏱️] 504 DEADLINE_EXCEEDED：請求過大或模型計算太久，縮小 batch"
                    )
                    break

                if status == 503:
                    try:
                        error_json = e.response.json()
                        remote_msg = error_json.get("error", {}).get("message", "")
                        remote_status = error_json.get("error", {}).get("status", "")
                    except Exception:
                        remote_msg = e.response.text or ""
                        remote_status = "NON_JSON"

                    log_error("-" * 60)
                    log_error("[🚨 Gemini 503]")
                    log_error(f"狀態: {remote_status}")
                    log_error(f"訊息: {remote_msg}")

                    overload_retry_count += 1
                    is_overloaded = (
                        "overloaded" in remote_msg.lower()
                        or "too many requests" in remote_msg.lower()
                    )

                    if overload_retry_count >= 3:
                        log_warning(
                            f"[🔁] 503 連續重試（{overload_retry_count} 次）→ 嘗試切換 API Key"
                        )
                        if not rotate_api_key():
                            log_error("[❌] 所有 API Key 已用盡 → 回傳 PARTIAL 保護進度")
                            return all_results, "PARTIAL"
                        overload_retry_count = 0
                        pinned_model_index = None
                        log_info("[✅] API Key 切換成功 → 等待後重送同一 batch")
                        time.sleep(key_rotation_buffer_sec)
                        hit_overload_retry = True
                        break

                    wait_sec = overload_retry_sec
                    log_warning(
                        f"[⚠️] 503 重試（第 {overload_retry_count} 次，is_overloaded={is_overloaded}），"
                        f"等待 {wait_sec}s 後用同一 key 重送"
                    )
                    time.sleep(wait_sec)
                    hit_overload_retry = True
                    break

                # ======== 500 ==========
                if status == 500:
                    log_info("[⚠️] 500 INTERNAL：Gemini 後端錯誤，嘗試換模型或縮 batch")
                    break

                # ========== requests timeout ==========
                if isinstance(e, requests.Timeout):
                    log_info("[⏱️] Timeout：模型尚未完成計算，縮小 batch")
                    break

                # ========== fallback ==========
                log_info(f"[!] 未分類錯誤: {e}")
                break

        # ⭐⭐⭐ 關鍵判定：如果是因為過載而跳出，直接進入下一次 while 迴圈（不執行下方的縮小邏輯）
        if hit_overload_retry:
            continue

        # ⭐⭐⭐ 如果這一輪已成功，直接進下一 round
        if success_this_round:
            continue

        # --- 如果所有模型都失敗，或者觸發了 break (截斷 / 數量不符) ---
        # ⭐ hit_rpm 代表「需要重試同一批」（不再代表等待）
        if hit_rpm:
            log_info("[🔁] 使用新 API Key，重新嘗試同一批次")
            hit_rpm = False  # ⭐ 重置旗標，避免無限 continue
            continue
        ## 發生錯誤時縮小比例
        BATCH_SHRINK_FACTOR = (
            load_config().get("lm_translator", {}).get("batch_shrink_factor", 0.75)
        )
        # 計算新的 Batch Size
        new_size = int(batch_size * BATCH_SHRINK_FACTOR)
        ## 最小錯誤 batch
        MIN_BATCH_SIZE = (
            load_config().get("lm_translator", {}).get("min_batch_size", 50)
        )

        # 安全下限檢查
        if is_lang and new_size < 20:  # Lang 檔通常很短，20 是底線
            new_size = 20 if batch_size > 20 else 0
        elif new_size < MIN_BATCH_SIZE:
            new_size = MIN_BATCH_SIZE if batch_size > MIN_BATCH_SIZE else 0

        # ⭐邏輯：Batch 縮到極限 → 直接放過這批，處理後續
        if new_size <= 0 or new_size == batch_size:
            # 情況 A：如果是因為 RPM (Rate Limit) 或 API 請求失敗而需要重試
            if hit_rpm:
                log_info("🔄 觸發頻率限制，嘗試切換 API Key...")
                if not rotate_api_key():
                    log_error("[❌] 致命錯誤：API Key 已全數耗盡，且目前 Batch 無法再縮小。將儲存目前進度並結束任務。")
                    return all_results, "PARTIAL"
                hit_rpm = True
                continue

            # 情況 B：如果是因為 JSON 截斷或模型內容過長 (非 RPM 錯誤)
            else:
                log_warning(
                    f"[⚠️] Batch Size 已縮至極限 ({batch_size}) 仍持續截斷。"
                    "策略：跳過此批（輸出原值），直接處理下一批，避免浪費其他 API Key。"
                )

                # 1. 認輸：直接塞回原始數據，保證結構完整
                all_results.extend(current_batch)

                # 2. 移除指標：讓指標往後跳過這批
                remaining_items = remaining_items[batch_size:]

                # 3. 如果還有剩下的，重置 batch_size
                if remaining_items:
                    batch_size = min(
                        len(remaining_items),
                        MIN_BATCH_SIZE if not is_lang else MIN_LANG_BATCH_SIZE,
                    )

                # 4. 繼續 while 迴圈處理後面的東西
                continue

        log_info(f"[↓] 調整 Batch：{batch_size} → {new_size}")
        batch_size = new_size

    return all_results, "AUTO"  # （只有真的要炸掉時才 raise，你現在這行會吃掉正常流程）
