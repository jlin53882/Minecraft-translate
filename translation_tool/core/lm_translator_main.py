"""translation_tool/core/lm_translator_main.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from dataclasses import dataclass

import requests

from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_batch_actions import BatchAction, decide_batch_action
from translation_tool.core.lm_batch_budget import (
    BudgetConfig,
    estimate_text_tokens,
    get_tracker,
    select_batch_size,
)
from translation_tool.core.lm_config_rules import ApiKeyCycle
from translation_tool.core.lm_config_schema import model_output_token_cap
from translation_tool.core.lm_response_parser import safe_json_loads
from translation_tool.utils.cancellation import interruptible_sleep
from translation_tool.utils.config_manager import get_models_config, load_config
from translation_tool.utils.log_unit import log_error, log_info, log_warning
from translation_tool.utils.redaction import redact_text

# =========================================================
# Time Constants - 時間相關常數
# =========================================================
# 每批完成後的固定等待秒數（預設不等待；遇到 429 會依 API 建議秒數重試）。
# 免費層若常遇到 429，可在設定頁把「每批翻譯後等待秒數」調高。
RPM_COOLDOWN_SEC = 0
OVERLOAD_RETRY_WAIT_SEC = 12  # Overload 重試等待秒數
# 同一把 API key 累積這麼多次 503 overload 後，才把「這一把」標記為失敗並換下一把。
# 次數是逐把 key 計算：不同 key 的 overload 不互相累加。
OVERLOAD_KEY_SWITCH_THRESHOLD = 3

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


def _detect_batch_profile(items):
    """依 cache type 或檔案路徑決定批次 profile。"""
    cache_types = [
        str(item.get("cache_type", "")).lower()
        for item in items
        if isinstance(item, dict) and item.get("cache_type")
    ]
    if cache_types:
        unique = set(cache_types)
        if len(unique) == 1:
            cache_type = next(iter(unique))
            if cache_type in {"lang", "patchouli", "ftbquests", "kubejs", "md"}:
                return (
                    "lang"
                    if cache_type == "lang"
                    else ("ftb" if cache_type == "ftbquests" else cache_type)
                )
        for cache_type in ("lang", "ftbquests", "kubejs", "md", "patchouli"):
            if cache_type in cache_types:
                return "ftb" if cache_type == "ftbquests" else cache_type

    files = [
        str(item.get("file", "")).replace("\\", "/").lower()
        for item in items
        if isinstance(item, dict)
    ]
    if files and all("/lang/" in path for path in files):
        return "lang"
    if any("/ftbquests/" in path for path in files):
        return "ftb"
    if any("/kubejs/" in path for path in files):
        return "kubejs"
    if any("/md/" in path for path in files):
        return "md"
    return "patch"


def _is_truncated_response(text: str) -> bool:
    """判斷回應是否不是完整 JSON；供狀態機與測試共用。"""
    try:
        import json

        json.loads(text)
        return False
    except json.JSONDecodeError:
        pass

    balance = 0
    for char in text:
        if char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
        if balance < 0:
            return True
    return balance != 0


def _normalize_translations(parsed) -> dict[str, object]:
    """把模型可能回傳的三種 JSON 形狀統一為 ``{id: value}``."""
    normalized: dict[str, object] = {}
    if isinstance(parsed, dict):
        if "items" in parsed:
            for item in parsed["items"]:
                if isinstance(item, dict) and "id" in item and "value" in item:
                    normalized[str(item["id"])] = item["value"]
        elif {"file", "path", "text"} <= parsed.keys():
            normalized["0"] = parsed["text"]
        else:
            normalized.update({str(key): value for key, value in parsed.items()})
    elif isinstance(parsed, list):
        for index, item in enumerate(parsed):
            if isinstance(item, dict):
                result_id = str(item.get("id", index))
                normalized[result_id] = item.get("value", item.get("text", ""))
    return normalized


# =========================================================
# 截斷診斷（issue #108 階段 0）
# =========================================================


def _describe_truncation(finish_reason, meta, estimate, sent_count) -> str:
    """組出截斷時的診斷訊息：finishReason 判定的原因 + 估算與實際 token 用量。

    用來分辨截斷是「輸出 token 上限」（MAX_TOKENS，含思考 token 占用額度）還是
    「模型正常結束卻產出壞 JSON」（STOP），作為調整批次策略的依據。
    """
    if finish_reason == "MAX_TOKENS":
        cause = "輸出達 token 上限（MAX_TOKENS）"
    elif finish_reason == "STOP":
        cause = "模型正常結束（STOP）但 JSON 不完整或損壞，並非 token 上限"
    elif not finish_reason:
        cause = "沒有 finishReason，無法判斷原因"
    else:
        cause = f"finishReason={finish_reason}"
    return (
        f"[截斷診斷] {cause} | 送出 {sent_count} 條 | "
        f"估算 in/out={estimate.input_tokens:.0f}/{estimate.output_tokens:.0f} | "
        f"實際 prompt={meta.get('prompt_tokens')} "
        f"output={meta.get('candidates_tokens')} "
        f"thoughts={meta.get('thoughts_tokens')}"
    )


# =========================================================
# 翻譯入口函數（新結構）
# =========================================================


def translate_batch_smart(
    batch_items,
    total=None,
    dry_run: bool = DEFAULT_DRY_RUN,
):
    """
    智慧批次翻譯函數（主入口）

    參數:
        batch_items: 翻譯項目列表
        total: 總項目數（可選）
        dry_run: True = 不呼叫API，只模擬流程（測試用）

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
    results, status = _execute_translation(items, total, dry_run)

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


def _execute_translation(items, total, dry_run=False):
    """
    執行翻譯主循環

    參數：
        items: 項目列表
        total: 總項目數
        dry_run: 是否為測試模式（不呼叫 API）
    回傳：
        (結果列表, 狀態字串)
    """
    # 代理到舊函數（正確傳遞所有參數）
    return _translate_batch_smart_impl(items, total, dry_run)


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

    # 處理空結果：保留原始 status。FAILED / PARTIAL / ALL_KEYS_EXHAUSTED 等狀態
    # 若被改成 AUTO，呼叫端會把「沒有結果」當成正常完成，不會中斷或回報額度耗盡。
    if not results:
        return [], status

    return results, status


def _classify_batch_error(error, status: int | None = None) -> str:
    """將 HTTP/timeout 例外映射成狀態機可消費的純 action 類別。

    保留既有 retry 行為，但讓狀態判定不再散落在主迴圈的例外處理中；
    這個 helper 也能用固定輸入直接測試，不需要真正呼叫 API。
    """
    if isinstance(error, requests.Timeout):
        return "timeout"
    if status is None and isinstance(error, requests.HTTPError):
        response = error.response
        status = response.status_code if response is not None else None
    return {
        400: "invalid_argument",
        403: "key_forbidden",
        404: "model_missing",
        429: "rate_limited",
        500: "server_error",
        503: "service_unavailable",
        504: "deadline_exceeded",
    }.get(status, "unknown")


# =========================================================
# 舊翻譯函數（保留原邏輯）
# =========================================================


def _translate_batch_smart_impl(batch_items, total=None, dry_run=False):
    """執行既有翻譯 contract；詳細 state machine 由命名 helper 承擔。"""
    return _run_batch_state_machine(batch_items, total, dry_run)


@dataclass
class _BatchRuntime:
    """Mutable state shared by the small orchestration helpers."""

    batch_items: list[dict]
    total: int | None
    lm_cfg: dict
    batch_profile: str
    batch_size: int
    budget_cfg: BudgetConfig
    budget_tracker: object
    key_cycle: ApiKeyCycle
    model_pool: list[str]
    model_temperature: float
    lang_prompt: str
    patchouli_prompt: str
    fixed_input_tokens: float
    original_total: int | None
    remaining_items: list[dict]
    all_results: list[dict]
    completed_calls: int = 0
    pinned_model_index: int | None = None
    rpm_cooldown_sec: float = RPM_COOLDOWN_SEC
    key_rotation_buffer_sec: float = 5
    overload_retry_sec: float = OVERLOAD_RETRY_WAIT_SEC
    request_interval_sec: float = 4


@dataclass(frozen=True)
class _BatchRound:
    current_batch: list[dict]
    fit_count: int
    estimate: object
    payload: dict
    id_to_item: dict[str, dict]


@dataclass(frozen=True)
class _BatchRoundOutcome:
    action: BatchAction | None = None
    learned_budget: bool = False


def _prompt_text(value: object, fallback: str) -> str:
    """Normalize a configured prompt without mixing config concerns into the loop."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("content") or value.get("text") or value)
    return str(value or fallback)


def _build_batch_runtime(
    batch_items: list[dict],
    total: int | None,
) -> _BatchRuntime | None:
    """Build configuration and mutable batch state once per call."""
    lm_cfg = load_config().get("lm_translator", {})
    profile = _detect_batch_profile(batch_items)
    max_sizes = {
        "lang": lm_cfg.get("initial_batch_size_lang", 200),
        "ftb": lm_cfg.get("initial_batch_size_ftb", 100),
        "kubejs": lm_cfg.get("initial_batch_size_kubejs", 200),
        "md": lm_cfg.get("initial_batch_size_md", 100),
        "patch": lm_cfg.get("initial_batch_size_patchouli", 100),
    }
    max_size = int(max_sizes.get(profile, max_sizes["patch"]) or 1)
    try:
        rpm_cooldown = max(0.0, float(lm_cfg.get("rpm_cooldown_sec", RPM_COOLDOWN_SEC)))
    except (TypeError, ValueError):
        rpm_cooldown = float(RPM_COOLDOWN_SEC)

    models_cfg = get_models_config(load_config())
    model_pool = [
        name for name, config in models_cfg.items() if config.get("enabled", False)
    ]
    if not model_pool:
        log_error(
            "[❌] MODEL_POOL 為空（沒有啟用任何模型），請在設定中啟用至少一個模型"
        )
        return None

    lang_prompt = _prompt_text(
        lm_cfg.get("lang_system_prompt"),
        "你正在翻譯 Minecraft 語言檔案（JSON格式）。",
    )
    patchouli_prompt = _prompt_text(
        lm_cfg.get("patchouli_system_prompt"),
        "你是專業的 Minecraft Patchouli 手冊翻譯員",
    )
    fixed_prompt = lang_prompt if profile in {"lang", "kubejs"} else patchouli_prompt
    return _BatchRuntime(
        batch_items=batch_items,
        total=total,
        lm_cfg=lm_cfg,
        batch_profile=profile,
        batch_size=min(len(batch_items), max_size),
        budget_cfg=BudgetConfig.from_config(lm_cfg),
        budget_tracker=get_tracker(profile),
        key_cycle=ApiKeyCycle(),
        model_pool=model_pool,
        model_temperature=lm_cfg.get("temperature", 0.2),
        lang_prompt=lang_prompt,
        patchouli_prompt=patchouli_prompt,
        fixed_input_tokens=estimate_text_tokens(fixed_prompt),
        original_total=total,
        remaining_items=list(batch_items),
        all_results=[],
        rpm_cooldown_sec=rpm_cooldown,
        key_rotation_buffer_sec=lm_cfg.get("key_rotation_buffer_sec", 5),
        overload_retry_sec=lm_cfg.get("overload_retry_sec", OVERLOAD_RETRY_WAIT_SEC),
        request_interval_sec=lm_cfg.get("request_interval_sec", 4),
    )


def _prepare_batch(runtime: _BatchRuntime) -> _BatchRound:
    """Select a prefix by item and token budget and build the API payload."""
    fit_count = select_batch_size(
        runtime.remaining_items,
        runtime.batch_profile,
        runtime.batch_size,
        runtime.lm_cfg,
        fixed_input_tokens=runtime.fixed_input_tokens,
    )
    current_batch = runtime.remaining_items[:fit_count]
    return _BatchRound(
        current_batch=current_batch,
        fit_count=fit_count,
        estimate=runtime.budget_tracker.estimate(
            current_batch, runtime.budget_cfg, runtime.fixed_input_tokens
        ),
        payload={
            "items": [
                {"id": str(index), "value": item["text"]}
                for index, item in enumerate(current_batch)
            ]
        },
        id_to_item={str(index): item for index, item in enumerate(current_batch)},
    )


def _parse_rate_limit_error(error: Exception) -> tuple[str, int, str]:
    """Extract quota kind, retry delay, and redacted remote message from a 429."""
    error_json = error.response.json().get("error", {})
    remote_message = redact_text(error_json.get("message", "")).upper()
    quota_id = ""
    retry_after = 0
    for detail in error_json.get("details", []):
        detail_type = detail.get("@type")
        if detail_type == "type.googleapis.com/google.rpc.QuotaFailure":
            quota_id = detail.get("violations", [{}])[0].get("quotaId", "").upper()
        if detail_type == "type.googleapis.com/google.rpc.RetryInfo":
            retry_after = int(
                float(str(detail.get("retryDelay", "0s")).replace("s", ""))
            )
    if "PERDAY" in quota_id or "DAILY" in remote_message:
        return "rpd", retry_after, remote_message
    if "PERMINUTE" in quota_id or "RPM" in remote_message:
        return "rpm", retry_after, remote_message
    return "quota", retry_after, remote_message


def _handle_batch_error(
    runtime: _BatchRuntime,
    error: Exception,
    model_index: int,
    *,
    model_output_cap: int | None = None,
    cap_source: str = "global",
) -> BatchAction:
    """Classify one failed model attempt and execute stateful key/wait effects."""
    status = (
        error.response.status_code
        if isinstance(error, requests.HTTPError) and error.response is not None
        else None
    )
    error_kind = _classify_batch_error(error, status)
    if error_kind != "service_unavailable":
        runtime.key_cycle.clear_overload()

    if error_kind == "model_missing":
        log_info(
            f"[⛔] 模型 {runtime.model_pool[model_index]} 不存在或無法使用，跳過此模型"
        )
        return decide_batch_action(error_kind, has_next_model=True).action

    if error_kind == "key_forbidden":
        log_info(
            f"❌ 403 PERMISSION_DENIED：API Key 無權限 "
            f"(index {runtime.key_cycle.current_index})"
        )
        if not runtime.key_cycle.mark_failed(reason="forbidden"):
            raise RuntimeError("❌ 所有 API Key 均無權限")
        return decide_batch_action(error_kind, has_alternative_key=True).action

    if error_kind == "invalid_argument":
        message = redact_text(error).lower()
        if "failed_precondition" in message:
            raise RuntimeError(
                "❌ FAILED_PRECONDITION：此地區未啟用 Gemini API 免費方案，請啟用付費"
            )
        has_cap_error = "maxoutputtokens" in message.replace("_", "").replace(" ", "")
        if has_cap_error and model_index + 1 < len(runtime.model_pool):
            log_warning(
                f"模型 {runtime.model_pool[model_index]} 的 maxOutputTokens 不受支援，改用下一個模型"
            )
            return decide_batch_action(
                error_kind,
                has_next_model=True,
                has_output_token_cap_fallback=True,
            ).action
        if has_cap_error:
            setting = (
                f"lm_translator.models.{runtime.model_pool[model_index]}.max_output_tokens"
                if cap_source == "per_model"
                else "lm_translator.max_output_tokens"
            )
            raise RuntimeError(
                f"❌ maxOutputTokens（{model_output_cap}）超過模型上限；請調低 {setting}"
            )
        log_info("[⚠️] 400 INVALID_ARGUMENT：payload 格式錯誤或過大，縮小 batch")
        return decide_batch_action(error_kind).action

    if error_kind == "rate_limited":
        try:
            quota_kind, retry_after, remote_message = _parse_rate_limit_error(error)
        except Exception as parse_error:  # noqa: BLE001
            remote_message = redact_text(error).upper()
            quota_kind = "quota"
            retry_after = 0
            log_error(f"[⚠️] 無法解析 429 JSON，使用備援：{redact_text(parse_error)}")

        if quota_kind == "rpd":
            log_warning(
                f"[🚫] 每日限額已滿 (RPD)：Key Index {runtime.key_cycle.current_index}"
            )
            if not runtime.key_cycle.mark_failed(reason="rpd"):
                return BatchAction.EXHAUSTED
            return BatchAction.ROTATE_KEY
        if quota_kind == "rpm":
            wait_time = retry_after or 10
            log_info(f"[⏳] 每分鐘頻率限制 (RPM)：等待 {wait_time} 秒")
            interruptible_sleep(wait_time)
            return BatchAction.RETRY_SAME_KEY
        log_warning(
            f"[❓] 偵測到 429 限制 ({remote_message or 'unknown'}), 嘗試切換 Key"
        )
        if not runtime.key_cycle.mark_failed():
            return BatchAction.EXHAUSTED
        return BatchAction.ROTATE_KEY

    if error_kind == "service_unavailable":
        response = error.response if isinstance(error, requests.HTTPError) else None
        try:
            error_json = response.json() if response is not None else {}
            remote_message = redact_text(error_json.get("error", {}).get("message", ""))
            remote_status = error_json.get("error", {}).get("status", "")
        except Exception:  # noqa: BLE001
            remote_message = redact_text(getattr(response, "text", "") or "")
            remote_status = "NON_JSON"
        log_error(f"[Gemini 503] status={remote_status} message={remote_message}")
        overloaded = (
            "overloaded" in remote_message.lower()
            or "too many requests" in remote_message.lower()
        )
        if overloaded:
            overload_count = runtime.key_cycle.record_overload()
            if overload_count >= OVERLOAD_KEY_SWITCH_THRESHOLD:
                if not runtime.key_cycle.mark_failed():
                    return BatchAction.PARTIAL
                runtime.pinned_model_index = None
                interruptible_sleep(runtime.key_rotation_buffer_sec)
                return BatchAction.ROTATE_KEY
            runtime.pinned_model_index = model_index
            interruptible_sleep(runtime.overload_retry_sec)
            return decide_batch_action(
                error_kind,
                quota_kind="overloaded",
                overload_count=overload_count,
                has_alternative_key=True,
            ).action
        runtime.pinned_model_index = None
        interruptible_sleep(runtime.request_interval_sec)
        return decide_batch_action(
            error_kind, has_next_model=model_index + 1 < len(runtime.model_pool)
        ).action

    if error_kind in {"deadline_exceeded", "server_error", "timeout"}:
        log_info(f"[{error_kind}] 請求失敗，縮小 batch")
        return decide_batch_action(error_kind).action

    log_info(f"[!] 未分類錯誤: {redact_text(error)}")
    return BatchAction.FAIL


def _merge_batch_response(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    raw_text: str,
    api_meta: dict,
) -> tuple[list[dict] | None, bool]:
    """Parse, validate, and restore ordered translations for one successful response."""
    parsed = safe_json_loads(raw_text)
    normalized = _normalize_translations(parsed)
    sent_count = len(round_data.id_to_item)
    if len(normalized) < sent_count:
        log_warning(f"[❌ 漏翻] 送出 {sent_count} 條，實收 {len(normalized)} 條")
        runtime.budget_tracker.on_truncated(
            api_meta.get("finish_reason"), kind="missing"
        )
        return None, True

    merged: list[dict] = []
    lazy_count = 0
    for temp_id, original_item in round_data.id_to_item.items():
        translated_text = normalized.get(temp_id, original_item["text"])
        if translated_text == original_item["text"] and any(
            char.isalpha() for char in str(translated_text)
        ):
            lazy_count += 1
        if not translated_text or not str(translated_text).strip():
            log_warning("[⚠️ 空翻譯] path=%s", original_item["path"])
        if len(original_item["text"]) > 0 and (
            len(str(translated_text)) / len(original_item["text"]) > 3
        ):
            log_warning("[⚠️ 異常長度] path=%s", original_item["path"])
        merged.append({**original_item, "text": translated_text})
    if lazy_count:
        log_info(f"[📊 本批次疑似未翻，建議Cache內容查詢 {lazy_count}/{sent_count}]")
    return merged, False


def _attempt_batch(
    runtime: _BatchRuntime, round_data: _BatchRound
) -> _BatchRoundOutcome:
    """Try the model pool and return an explicit action for the outer state machine."""
    model_indices = (
        [runtime.pinned_model_index]
        if runtime.pinned_model_index is not None
        else range(len(runtime.model_pool))
    )
    for model_index in model_indices:
        model_name = runtime.model_pool[model_index]
        prompt = (
            runtime.lang_prompt
            if runtime.batch_profile in {"lang", "kubejs"}
            else runtime.patchouli_prompt
        )
        output_cap = None
        cap_source = "global"
        try:
            model_override = model_output_token_cap(runtime.lm_cfg, model_name)
            cap_source = "per_model" if model_override is not None else "global"
            output_cap = (
                model_override
                if model_override is not None
                else runtime.budget_cfg.max_output_tokens
            )
            api_meta: dict = {}
            log_info(
                f"[→] 嘗試模型 {model_name} | Batch={len(round_data.current_batch)}"
                f"/{runtime.batch_size} | 翻譯總量={runtime.original_total}"
            )
            raw_text = call_gemini_requests(
                model_name=model_name,
                system_prompt=prompt,
                payload=round_data.payload,
                api_key=runtime.key_cycle.claim(),
                temperature=runtime.model_temperature,
                max_output_tokens=output_cap,
                meta_out=api_meta,
            ).strip()
            if not raw_text:
                continue
            finish_reason = api_meta.get("finish_reason")
            if finish_reason == "MAX_TOKENS" or _is_truncated_response(raw_text):
                log_warning(
                    _describe_truncation(
                        finish_reason,
                        api_meta,
                        round_data.estimate,
                        round_data.fit_count,
                    )
                )
            if _is_truncated_response(raw_text):
                runtime.key_cycle.clear_overload()
                runtime.budget_tracker.on_truncated(finish_reason)
                return _BatchRoundOutcome(BatchAction.SHRINK_BATCH, learned_budget=True)

            result, missing = _merge_batch_response(
                runtime, round_data, raw_text, api_meta
            )
            if missing:
                return _BatchRoundOutcome(BatchAction.SHRINK_BATCH, learned_budget=True)

            runtime.completed_calls += 1
            runtime.all_results.extend(result or [])
            actual_output = api_meta.get("candidates_tokens")
            if isinstance(actual_output, int):
                actual_output += api_meta.get("thoughts_tokens") or 0
            runtime.budget_tracker.on_success(
                runtime.budget_cfg,
                value_tokens=round_data.estimate.value_tokens,
                item_count=len(round_data.current_batch),
                actual_output_tokens=actual_output,
            )
            runtime.remaining_items = runtime.remaining_items[
                len(round_data.current_batch) :
            ]
            runtime.batch_size = min(runtime.batch_size, len(runtime.remaining_items))
            runtime.key_cycle.record_success()
            runtime.pinned_model_index = None
            if not runtime.remaining_items and runtime.rpm_cooldown_sec > 0:
                interruptible_sleep(runtime.rpm_cooldown_sec)
            return _BatchRoundOutcome()
        except Exception as error:  # noqa: BLE001
            action = _handle_batch_error(
                runtime,
                error,
                model_index,
                model_output_cap=output_cap,
                cap_source=cap_source,
            )
            if action is BatchAction.NEXT_MODEL:
                continue
            return _BatchRoundOutcome(action)

    # Model pool exhausted: the outer loop must shrink/skip this prefix,
    # rather than treating the exhausted pool as an endless next-model retry.
    return _BatchRoundOutcome(BatchAction.SHRINK_BATCH)


def _run_batch_state_machine(
    batch_items: list[dict],
    total: int | None = None,
    dry_run: bool = False,
):
    """Coordinate preparation, attempts, action execution, and prefix advancement."""
    if dry_run:
        return [], "DRY_RUN"
    runtime = _build_batch_runtime(batch_items, total)
    if runtime is None:
        return [], "FAILED"

    while runtime.remaining_items:
        round_data = _prepare_batch(runtime)
        outcome = _attempt_batch(runtime, round_data)
        if outcome.action in {
            BatchAction.RETRY_SAME_MODEL,
            BatchAction.RETRY_SAME_KEY,
            BatchAction.ROTATE_KEY,
            BatchAction.NEXT_MODEL,
        }:
            continue
        if outcome.action is BatchAction.EXHAUSTED:
            return None, "ALL_KEYS_EXHAUSTED"
        if outcome.action is BatchAction.FAIL:
            return runtime.all_results, "FAILED"
        if outcome.action is BatchAction.PARTIAL:
            return runtime.all_results, "PARTIAL"
        if outcome.action is not BatchAction.SHRINK_BATCH:
            continue

        if outcome.learned_budget:
            next_fit = select_batch_size(
                runtime.remaining_items,
                runtime.batch_profile,
                runtime.batch_size,
                runtime.lm_cfg,
                fixed_input_tokens=runtime.fixed_input_tokens,
            )
            if next_fit < len(round_data.current_batch):
                continue

        shrink_factor = float(runtime.lm_cfg.get("batch_shrink_factor", 0.75) or 0.75)
        basis = min(runtime.batch_size, len(round_data.current_batch))
        new_size = int(basis * shrink_factor)
        min_size = int(runtime.lm_cfg.get("min_batch_size", 50) or 50)
        if runtime.batch_profile == "lang" and new_size < MIN_LANG_BATCH_SIZE:
            new_size = MIN_LANG_BATCH_SIZE if basis > MIN_LANG_BATCH_SIZE else 0
        elif new_size < min_size:
            new_size = min_size if basis > min_size else 0

        if new_size <= 0 or new_size == basis:
            log_warning(f"[⚠️] Batch Size 已縮至極限 ({basis})，回填原文並繼續後續項目")
            runtime.all_results.extend(
                {**item, "_untranslated": True} for item in round_data.current_batch
            )
            runtime.remaining_items = runtime.remaining_items[
                len(round_data.current_batch) :
            ]
            runtime.batch_size = min(
                len(runtime.remaining_items),
                MIN_LANG_BATCH_SIZE if runtime.batch_profile == "lang" else min_size,
            )
            continue
        log_info(f"[↓] 調整 Batch：{basis} → {new_size}")
        runtime.batch_size = new_size

    return runtime.all_results, "AUTO"


# 暫保舊名稱供外部整合程式相容；現役入口與內部呼叫已不再依賴 old 命名。
translate_batch_smart_old = _translate_batch_smart_impl
