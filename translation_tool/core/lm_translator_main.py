"""translation_tool/core/lm_translator_main.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
import time

import requests

from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_config_rules import (
    _current_key_index,  # 目前使用的 API Key 索引
    get_current_api_key,  # 取得正在使用的 key
    rotate_api_key,  # 切換 key
)
from translation_tool.core.lm_response_parser import safe_json_loads
from translation_tool.utils.config_manager import load_config

logger = logging.getLogger(__name__)

# 設定區

DRY_RUN = False  # True = 不送 API，只做分析 / 預覽 測試使用
EXPORT_CACHE_ONLY = True  # True = 先輸出 cache 命中內容


def translate_batch_smart(batch_items, total=None):
    """
    智慧型分批翻譯函式
    支援動態縮減 Batch Size、模型切換、以及自動處理輸出截斷問題。
    """
    # 起始 batch（你 TPM 很夠） Lang 專用
    INITIAL_BATCH_SIZE_LANG = (
        load_config().get("lm_translator", {}).get("iniital_batch_size_lang", 300)
    )
    # ⭐ 新增（建議 80~150） Patchoui 專用
    INITIAL_BATCH_SIZE_PATCHOULI = (
        load_config().get("lm_translator", {}).get("iniital_batch_size_patchouli", 100)
    )

    lm_cfg = load_config().get("lm_translator", {})  # ✅ 只讀一次

    # 起始 batch（Lang）
    INITIAL_BATCH_SIZE_LANG = lm_cfg.get("iniital_batch_size_lang", 200)

    # Patchouli / 其他（預設小）
    INITIAL_BATCH_SIZE_PATCHOULI = lm_cfg.get("iniital_batch_size_patchouli", 100)

    # ✅ 新增：FTB / KubeJS 專用 batch 上限（你可以在 config 調整）
    INITIAL_BATCH_SIZE_FTB = lm_cfg.get("initial_batch_size_ftb", 100)
    INITIAL_BATCH_SIZE_KUBEJS = lm_cfg.get("initial_batch_size_kubejs", 200)
    INITIAL_BATCH_SIZE_MD = lm_cfg.get("initial_batch_size_md", 100)

    remaining_items = list(batch_items)  # 尚未處理的
    # ⭐ 已成功送出的 API 次數
    completed_calls = 0
    all_results = []  # 累積所有翻譯結果
    # ⭐⭐⭐ 新增：503 連續過載計數器（放在 while 迴圈外）
    overload_retry_count = 0

    # 判斷這批次類型（影響 System Prompt 與 batch 上限）
    def _norm_file(item):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
        return str(item.get("file", "")).replace("\\", "/").lower()

    def detect_batch_profile(items):
        # ✅ 優先用 cache_type（最可靠）
        """處理此函式的工作（細節以程式碼為準）。

        回傳：依函式內 return path。
        """
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

    """
    def detect_batch_profile(items):
        files = [_norm_file(i) for i in items if isinstance(i, dict)]
        if files and all("/lang/" in f for f in files):
            return "lang"
        # ✅ 你的規則：包含 /ftbquests/ 判定為 FTB
        if any("/ftbquests/" in f for f in files):
            return "ftb"
        # ✅ 你的規則：包含 /kubejs/ 判定為 KubeJS
        if any("/kubejs/" in f for f in files):
            return "kubejs"
        if any("/md/" in f for f in files):
            return "md"

        return "patch"
    """
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

    # 使用提示詞 手冊
    PATCHOUI_SYSTEM_PROMPT = (
        load_config()
        .get("lm_translator", {})
        .get("patchouli_system_prompt", {"你是專業的 Minecraft Patchouli 手冊翻譯員"})
    )

    # 使用提示詞 lang
    LANG_SYSTEM_PROMPT = (
        load_config()
        .get("lm_translator", {})
        .get("lang_system_prompt", {"你正在翻譯 Minecraft 語言檔案（JSON格式）。"})
    )

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
        logger.debug(
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
                # logger.info(f"[→] 嘗試模型 {model_name} | Batch={batch_size}/{original_total} | 類型={'Lang' if is_lang else 'Patch'}")

                profile_name = {
                    "lang": "Lang",
                    "ftb": "FTB",
                    "kubejs": "KubeJS",
                    "md": "MD",
                    "patch": "Patch",
                }.get(batch_profile, batch_profile)
                # logger.info(f"[→] 嘗試模型 {model_name} | Batch={batch_size}/{original_total} | 類型={profile_name}")
                logger.info(
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
                    prompt = PATCHOUI_SYSTEM_PROMPT

                logger.debug(
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
                    logger.info(f"[!] 模型 {model_name} 回傳空內容，切換模型...")
                    continue

                # --- 核心改進：檢查輸出是否被截斷 ---
                if not raw_text.endswith(("}", "]")):
                    # print(f"[!] 偵測到 JSON 可能被截斷（結尾不完整），將縮小 Batch 重試")
                    overload_retry_count = 0  # ⭐ 重置過載計數器
                    logger.info(
                        "[!] 偵測到 JSON 可能被截斷（結尾不完整），將縮小 Batch 重試"
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
                    logger.warning(
                        f"[❌ 漏翻] 送出 {sent_count} 條，實收 {received_count} 條"
                    )
                    logger.warning(f"[❌ 缺失 ID] {missing_ids[:5]}")
                    if missing_ids:
                        logger.debug(
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
                            logger.debug(f"[⚠️ 疑似未翻] {original_item['path']}")

                    new_item["text"] = translated_text
                    merged_result.append(new_item)

                if lazy_count > 0:
                    logger.info(
                        f"[📊 本批次疑似未翻，建議Cache內容查詢 {lazy_count}/{sent_count}]"
                    )

                result = merged_result

                logger.info(f"[✓] 成功取得翻譯：{model_name}")

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
                    # logger.info(f"📊 已完成 API 呼叫：{completed_calls} 次 | 所有 items 已完成")
                    logger.info(
                        f"📊 本批次已完成：calls={completed_calls} | 本批 items={len(batch_items)}"
                    )
                    # 免費層保護
                    logger.info("⏳ 等待 12 秒以避免觸發 RPM 限制…")
                    time.sleep(12)
                # else: #本批次 進來不會進來這裡處理
                #    remaining_calls_estimated = math.ceil(
                #        remaining_count / max(batch_size, 1)
                #    )

                #    eta_sec = int(remaining_calls_estimated * avg_time_per_call)
                #    eta_min = eta_sec // 60
                #    eta_sec = eta_sec % 60

                #    logger.info(
                #        f"📊 已完成 API 呼叫：{completed_calls} 次 | "
                #        f"剩餘 items：{remaining_count} | "
                #        f"目前 batch={batch_size} | "
                #        f"ETA ≈ {eta_min}m {eta_sec}s"
                #    )
                #    # 免費層保護
                #    logger.info("⏳ 等待 12 秒以避免觸發 RPM 限制…")
                #    time.sleep(12)
                #

                # ⭐ 如果已經沒有剩餘項目，直接結束 while
                if not remaining_items:
                    logger.info("✅ 所有項目已翻譯完成")
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
                    logger.info(f"[⛔] 模型 {model_name} 不存在或無法使用，跳過此模型")
                    break  # ⭐ 跳離迴圈

                # ========== 403 ==========
                if status == 403:
                    logger.info(
                        f"❌ 403 PERMISSION_DENIED：API Key 無權限 (index {_current_key_index})"
                    )
                    try:
                        rotate_api_key()
                        continue  # 換模型
                    except RuntimeError:
                        raise RuntimeError("❌ 所有 API Key 均無權限")

                # ========== 400 ==========
                if status == 400:
                    msg = str(e).lower()
                    if "failed_precondition" in msg:
                        raise RuntimeError(
                            "❌ FAILED_PRECONDITION：此地區未啟用 Gemini API 免費方案，請啟用付費"
                        )
                    logger.info(
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
                            logger.warning(
                                f"[🚫] 每日限額已滿 (RPD)：Key Index {_current_key_index} 今日失效"
                            )
                            hit_rpm = True
                            # ⭐ 檢查換 Key 是否成功
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                        elif "PERMINUTE" in quota_id or "RPM" in remote_msg:
                            # 情況 B：每分鐘頻率 (RPM) 太快
                            wait_time = retry_after if retry_after > 0 else 10
                            logger.info(
                                f"[⏳] 每分鐘頻率限制 (RPM)：稍後重試，預計等待 {wait_time} 秒"
                            )
                            time.sleep(wait_time)
                            hit_rpm = True
                            continue

                        else:
                            # 情況 C：其他或未知的 429 (例如 Free Tier 的總請求限制)
                            logger.warning(
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
                        logger.error(
                            f"[⚠️] 無法解析 429 JSON，使用備援。錯誤: {parse_err}"
                        )

                        if "QUOTA" in err_msg or "EXCEEDED" in err_msg:
                            # ⭐ 這裡之前會崩潰，現在這樣改就安全了
                            if not rotate_api_key():
                                return None, "ALL_KEYS_EXHAUSTED"
                            continue

                        # 兜底處理
                        if not rotate_api_key():
                            return None, "ALL_KEYS_EXHAUSTED"
                        continue

                    except RuntimeError:
                        # ⭐ 關鍵：當 rotate_api_key 拋出 RuntimeError，代表沒 Key 了
                        # 不要只用 break，要直接 return 狀態給外層
                        error_final = (
                            "❌ 所有 API Key 均已耗盡每日配額 (RPD)，請等待重置時間。"
                        )
                        logger.error(error_final)
                        return None, "ALL_KEYS_EXHAUSTED"

                # ========== 504 ==========
                if status == 504:
                    logger.info(
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

                    logger.error("-" * 60)
                    logger.error("[🚨 Gemini 503]")
                    logger.error(f"狀態: {remote_status}")
                    logger.error(f"訊息: {remote_msg}")

                    is_overloaded = (
                        "overloaded" in remote_msg.lower()
                        or "too many requests" in remote_msg.lower()
                    )

                    # ===== A. 真 overload：同一 batch 原地等 =====
                    if is_overloaded:
                        overload_retry_count += 1  # ⭐ 累積過載次數

                        pinned_model_index = i  # ⭐ 記住是哪個 model 過載

                        # if overload_retry_count >= 3:
                        #    logger.error(f"[❌] 持續 overload（{overload_retry_count} 次）→ 回傳 PARTIAL 保護進度")
                        #    return all_results, "PARTIAL"
                        if overload_retry_count >= 3:
                            logger.warning(
                                f"[🔁] 模型連續 overload（{overload_retry_count} 次），嘗試切換 API Key"
                            )

                            try:
                                # ⭐ 嘗試換 Key
                                if rotate_api_key():
                                    overload_retry_count = 0  # ⭐ 重置過載計數
                                    pinned_model_index = None  # ⭐ 解鎖模型，允許重新選
                                    logger.info(
                                        "[✅] API Key 切換成功 → 原地重送同一 batch,等待12秒"
                                    )
                                    time.sleep(12)  # ⭐ 給新 Key 一點緩衝
                                    hit_overload_retry = True  # ⭐ 重送同一 batch
                                    break  # ← 跳出 model loop，回 while
                                else:
                                    raise RuntimeError("NO_MORE_KEYS")

                            except RuntimeError:
                                logger.error(
                                    "[❌] 所有 API Key 在 overload 狀態下均不可用 → 回傳 PARTIAL 保護進度"
                                )
                                return all_results, "PARTIAL"

                        wait_sec = 12
                        logger.warning(
                            f"[⚠️] 模型過載（第 {overload_retry_count} 次），"
                            f"原地等待 {wait_sec}s 後重送【同一 batch / 同一模型】"
                        )

                        time.sleep(wait_sec)
                        hit_overload_retry = True
                        break  # ← 跳出 model pool，回到 while 重新送

                    # ===== B. 非 overload 的 503：換 key / model =====
                    else:
                        logger.warning(
                            "503 非 overload（可能節點或區域異常）→ 嘗試切換 API key"
                        )
                        try:
                            rotate_api_key()
                            time.sleep(5)
                            continue  # 換 key 繼續 model pool
                        except Exception as err:
                            logger.error(f"API key 切換失敗: {err}")
                            break

                # ======== 500 ==========
                if status == 500:
                    logger.info(
                        "[⚠️] 500 INTERNAL：Gemini 後端錯誤，嘗試換模型或縮 batch"
                    )
                    break

                # ========== requests timeout ==========
                if isinstance(e, requests.Timeout):
                    logger.info("[⏱️] Timeout：模型尚未完成計算，縮小 batch")
                    break

                # ========== fallback ==========
                logger.info(f"[!] 未分類錯誤: {e}")
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
            logger.info("[🔁] 使用新 API Key，重新嘗試同一批次")
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
                try:
                    logger.info("🔄 觸發頻率限制，嘗試切換 API Key...")
                    rotate_api_key()
                    # 保持 hit_rpm = True，下一輪會用新 Key 重試這批
                    continue
                except RuntimeError:
                    logger.error(
                        f"[❌] 致命錯誤：API Key 已全數耗盡，且目前 Batch ({batch_size}) 無法再縮小。"
                        "將儲存目前進度並結束任務。"
                    )
                    return all_results, "PARTIAL"

            # 情況 B：如果是因為 JSON 截斷或模型內容過長 (非 RPM 錯誤)
            else:
                logger.warning(
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
                        len(remaining_items), MIN_BATCH_SIZE if not is_lang else 20
                    )

                # 4. 繼續 while 迴圈處理後面的東西
                continue

        logger.info(f"[↓] 調整 Batch：{batch_size} → {new_size}")
        batch_size = new_size

    return all_results, "AUTO"  # （只有真的要炸掉時才 raise，你現在這行會吃掉正常流程）
