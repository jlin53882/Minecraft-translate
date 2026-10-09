"""translation_tool/core/lm_translator_main.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import random
from copy import deepcopy
from dataclasses import dataclass, field

import requests

from translation_tool.core.lm_api_client import (
    GeminiResponseFormatError,
    call_gemini_requests,
    worst_case_request_sec,
)
from translation_tool.core.lm_batch_actions import BatchAction, decide_batch_action
from translation_tool.core.lm_batch_budget import (
    BudgetConfig,
    estimate_text_tokens,
    get_tracker,
    select_batch_size,
)
from translation_tool.core.lm_config_rules import (
    ApiKeyCycle,
    get_api_key_count,
    get_translation_provider,
    validate_translation_credentials,
)
from translation_tool.core.lm_config_schema import (
    chatgpt_model_input_token_budget,
    chatgpt_model_reasoning_effort,
    model_output_token_cap,
)
from translation_tool.core.lm_key_health import (
    PROBE_LEASE_MARGIN_SEC,
    get_model_quota_registry,
)
from translation_tool.core.lm_response_parser import safe_json_loads
from translation_tool.core.lm_same_source_retry import (
    SAME_SOURCE_RETRY_INSTRUCTION,
    build_same_source_retry_batch,
    merge_same_source_retry_results,
)
from translation_tool.utils.cancellation import interruptible_sleep, raise_if_cancelled
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
CHATGPT_MAX_RETRIES = 3
CHATGPT_MAX_RETRY_AFTER_SEC = 120

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


def _structured_response_shape_error(parsed) -> str | None:
    """Reject schema-shaped responses that violate the items/id/value contract."""
    if not isinstance(parsed, dict) or "items" not in parsed:
        return "root must contain exactly the items property"
    if set(parsed) != {"items"}:
        return "root contains fields outside items"
    items = parsed["items"]
    if not isinstance(items, list):
        return "items is not an array"

    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"id", "value"}:
            return f"items[{index}] does not contain exactly id and value"
        item_id = item["id"]
        if not isinstance(item_id, str) or not isinstance(item["value"], str):
            return f"items[{index}] id/value is not a string"
        if item_id in seen:
            return f"duplicate id {item_id!r}"
        seen.add(item_id)
    return None


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
    if not dry_run:
        if get_translation_provider() == "chatgpt":
            validate_translation_credentials()
        elif get_api_key_count() == 0:
            raise RuntimeError("❌ 設定檔中沒有找到任何 API Key，請先設定金鑰。")

    # 批次 profile 與批次大小由 _execute_translation 內部決定（舊版在這裡重複計算後丟棄，已移除）。

    # 2. 執行翻譯
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
    provider: str
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
    batch_fit_input_tokens: float
    retry_fixed_input_tokens: float
    retry_same_as_source: bool
    original_total: int | None
    remaining_items: list[dict]
    all_results: list[dict]
    completed_calls: int = 0
    pinned_model_index: int | None = None
    # 同專案模式的模型配額：本次呼叫用來持有「探測租約」的身分（見 ModelQuotaRegistry）、
    # 已確定不存在（404）的模型。
    quota_owner: object = field(default_factory=object)
    missing_models: set[int] = field(default_factory=set)
    rpm_cooldown_sec: float = RPM_COOLDOWN_SEC
    key_rotation_buffer_sec: float = 5
    overload_retry_sec: float = OVERLOAD_RETRY_WAIT_SEC
    request_interval_sec: float = 4
    reasoning_effort: str | None = None
    chatgpt_retry_count: int = 0


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


def _format_remaining(seconds: float) -> str:
    """把剩餘秒數轉成「X 小時 Y 分鐘」給日誌看。"""
    minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小時 {minutes} 分鐘" if hours else f"{minutes} 分鐘"


def _usable_model_indices(runtime: _BatchRuntime, quota) -> list[int]:
    """還能嘗試的模型：不是 404、對本次呼叫而言也沒有被每日配額擋住。"""
    return [
        index
        for index, name in enumerate(runtime.model_pool)
        if index not in runtime.missing_models
        and not quota.is_blocked(name, runtime.quota_owner)
    ]


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
    config_snapshot = deepcopy(load_config())
    lm_cfg = config_snapshot.get("lm_translator", {})
    provider = lm_cfg.get("provider", "gemini")
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

    reasoning_effort = None
    if provider == "chatgpt":
        chatgpt_model = str(lm_cfg.get("chatgpt_model") or "").strip()
        model_pool = [chatgpt_model] if chatgpt_model else []
        if chatgpt_model:
            context_budget = chatgpt_model_input_token_budget(lm_cfg, chatgpt_model)
            if context_budget is not None:
                lm_cfg = {**lm_cfg, "max_input_token_budget": context_budget}
            reasoning_effort = chatgpt_model_reasoning_effort(lm_cfg, chatgpt_model)
    else:
        models_cfg = get_models_config(config_snapshot)
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
    retry_same_as_source = bool(lm_cfg.get("retry_same_as_source", True))
    fixed_input_tokens = estimate_text_tokens(fixed_prompt)
    retry_fixed_input_tokens = estimate_text_tokens(
        f"{fixed_prompt.rstrip()}\n\n{SAME_SOURCE_RETRY_INSTRUCTION}"
    )
    return _BatchRuntime(
        batch_items=batch_items,
        total=total,
        lm_cfg=lm_cfg,
        provider=provider,
        batch_profile=profile,
        batch_size=min(len(batch_items), max_size),
        budget_cfg=BudgetConfig.from_config(lm_cfg),
        budget_tracker=get_tracker(profile),
        key_cycle=ApiKeyCycle(),
        model_pool=model_pool,
        model_temperature=lm_cfg.get("temperature", 0.2),
        lang_prompt=lang_prompt,
        patchouli_prompt=patchouli_prompt,
        fixed_input_tokens=fixed_input_tokens,
        batch_fit_input_tokens=(
            retry_fixed_input_tokens if retry_same_as_source else fixed_input_tokens
        ),
        retry_fixed_input_tokens=retry_fixed_input_tokens,
        retry_same_as_source=retry_same_as_source,
        original_total=total,
        remaining_items=list(batch_items),
        all_results=[],
        rpm_cooldown_sec=rpm_cooldown,
        key_rotation_buffer_sec=lm_cfg.get("key_rotation_buffer_sec", 5),
        overload_retry_sec=lm_cfg.get("overload_retry_sec", OVERLOAD_RETRY_WAIT_SEC),
        request_interval_sec=lm_cfg.get("request_interval_sec", 4),
        reasoning_effort=reasoning_effort,
    )


def _prepare_batch(runtime: _BatchRuntime) -> _BatchRound:
    """Select a prefix by item and token budget and build the API payload."""
    fit_count = select_batch_size(
        runtime.remaining_items,
        runtime.batch_profile,
        runtime.batch_size,
        runtime.lm_cfg,
        fixed_input_tokens=runtime.batch_fit_input_tokens,
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


def _remote_error_detail(error: Exception, limit: int = 300) -> str:
    """HTTP 錯誤回應裡的伺服器訊息（已遮蔽金鑰、限制長度）；取不到時回傳空字串。"""
    response = getattr(error, "response", None)
    if response is None:
        return ""
    try:
        text = str(response.json().get("error", {}).get("message", ""))
    except Exception:  # noqa: BLE001 - 回應不是 JSON 時改用原始文字
        text = str(getattr(response, "text", "") or "")
    return redact_text(text.strip())[:limit]


def _chatgpt_api_error_message(error) -> str:
    """將 Responses API 錯誤轉成可採取行動的翻譯錯誤訊息。"""
    code = error.code.lower()
    param = error.param.lower()
    detail = str(error)

    if code == "refusal":
        return f"ChatGPT 拒絕處理這批翻譯內容，請檢查來源文字後再試。API 詳情：{detail}"
    if code == "subscription_sharing_usage_limit_exceeded":
        return (
            "ChatGPT 方案或此應用的使用限制已達；系統已停止自動重試，"
            "請查看 ChatGPT 使用量設定後再執行。API 詳情："
            f"{detail}"
        )
    if _is_chatgpt_quota_exhaustion(error):
        return (
            "ChatGPT 方案用量或 API 配額已耗盡；系統已停止自動重試，"
            f"請確認方案使用量或帳務限制後再執行。API 詳情：{detail}"
        )
    if error.status == 401:
        return (
            f"ChatGPT OAuth 憑證無效或已過期，請到 API 設定重新登入。API 詳情：{detail}"
        )
    if error.status == 403:
        return (
            "ChatGPT 拒絕此請求（403）。請確認登入帳號已授權 ChatGPT 計畫用 API 存取，"
            "且帳號、方案、模型與所在區域符合使用資格；若資格已更新，可到 API 設定重新登入。"
            f"API 詳情：{detail}"
        )
    if error.status == 429:
        return f"ChatGPT 使用頻率或用量已達限制，請稍後再試並確認方案使用量。API 詳情：{detail}"
    if error.status in {400, 422} and (
        "schema" in code or "schema" in param or "text.format" in param
    ):
        return (
            "ChatGPT 拒絕了結構化輸出 JSON Schema；請確認模型支援 Structured Outputs，"
            f"並檢查 schema 欄位。API 詳情：{detail}"
        )
    if error.status in {400, 422}:
        return f"ChatGPT 無法接受此翻譯請求，請檢查 API 詳情中的欄位與參數。API 詳情：{detail}"
    if error.status == 404:
        return f"ChatGPT 找不到或無權使用所選模型，請在設定選擇可用模型。API 詳情：{detail}"
    if error.status is not None and error.status >= 500:
        return f"ChatGPT 服務暫時發生錯誤，請稍後重試。API 詳情：{detail}"
    if code in {"network_error", "stream_interrupted", "stream_incomplete"}:
        return f"ChatGPT 連線中斷或回應串流未完成，請檢查網路後重試。API 詳情：{detail}"
    return f"ChatGPT 翻譯請求失敗：{detail}"


def _is_chatgpt_quota_exhaustion(error) -> bool:
    """Return whether a machine-readable API code signals a permanent quota limit."""
    permanent_codes = {
        "credit_balance_exhausted",
        "insufficient_quota",
        "usage_limit",
        "spend_limit",
        "quota_exceeded",
        "subscription_sharing_usage_limit_exceeded",
    }
    code = str(error.code or "").strip().lower()
    error_type = str(error.error_type or "").strip().lower()
    return code in permanent_codes or error_type in permanent_codes


def _chatgpt_retryable_error(error) -> bool:
    """Recognize transient Responses errors without retrying billing failures."""
    if _is_chatgpt_quota_exhaustion(error):
        return False
    code = str(error.code or "").lower()
    error_type = str(error.error_type or "").lower()
    if error.status == 429:
        # A 429 is retryable throttling unless its machine-readable code above
        # identified a terminal plan or billing limit.
        return True
    if error.status in {408, 500, 502, 503, 504}:
        return True
    return code in {
        "network_error",
        "server_error",
        "service_unavailable",
        "stream_incomplete",
        "stream_interrupted",
    } or error_type in {"rate_limit_error", "service_unavailable_error"}


def _handle_chatgpt_api_error(runtime: _BatchRuntime, error) -> BatchAction:
    """Apply bounded, cancellable backoff for transient ChatGPT request failures."""
    if error.code == "incomplete":
        log_warning(f"[ChatGPT] 回應未完整完成，縮小批次後重試：{error}")
        return BatchAction.SHRINK_BATCH
    if not _chatgpt_retryable_error(error):
        raise RuntimeError(_chatgpt_api_error_message(error)) from error
    if runtime.chatgpt_retry_count >= CHATGPT_MAX_RETRIES:
        raise RuntimeError(
            f"{_chatgpt_api_error_message(error)} 已達 {CHATGPT_MAX_RETRIES} 次重試上限。"
        ) from error

    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None:
        try:
            wait_sec = max(0.0, float(retry_after))
        except (TypeError, ValueError):
            retry_after = None
    if retry_after is None:
        base_delay = min(2**runtime.chatgpt_retry_count, 30)
        wait_sec = base_delay + random.uniform(0, min(1.0, base_delay * 0.25))
    if wait_sec > CHATGPT_MAX_RETRY_AFTER_SEC:
        raise RuntimeError(
            f"{_chatgpt_api_error_message(error)} 伺服器要求等待 {wait_sec:g} 秒，"
            f"超過本程式的 {CHATGPT_MAX_RETRY_AFTER_SEC} 秒上限；請稍後重新執行。"
        ) from error

    runtime.chatgpt_retry_count += 1
    log_warning(
        f"[ChatGPT] 暫時性錯誤，{wait_sec:g} 秒後重試 "
        f"({runtime.chatgpt_retry_count}/{CHATGPT_MAX_RETRIES})：{error}"
    )
    interruptible_sleep(wait_sec)
    return BatchAction.RETRY_SAME_MODEL


def _handle_batch_error(
    runtime: _BatchRuntime,
    error: Exception,
    model_index: int,
    *,
    model_output_cap: int | None = None,
    cap_source: str = "global",
) -> BatchAction:
    """Classify one failed model attempt and execute stateful key/wait effects."""
    from translation_tool.core.codex_oauth import ChatGPTOAuthError
    from translation_tool.core.openai_codex_client import ChatGPTAPIError

    if isinstance(error, ChatGPTOAuthError):
        raise ChatGPTOAuthError(f"ChatGPT OAuth 登入需要處理：{error}") from error
    if isinstance(error, ChatGPTAPIError):
        return _handle_chatgpt_api_error(runtime, error)

    status = (
        error.response.status_code
        if isinstance(error, requests.HTTPError) and error.response is not None
        else None
    )
    error_kind = _classify_batch_error(error, status)
    if error_kind != "service_unavailable":
        runtime.key_cycle.clear_overload()

    if error_kind == "model_missing":
        runtime.missing_models.add(model_index)  # 之後的輪次不再重打這個模型
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
        detail = _remote_error_detail(error)
        if detail:
            log_info(f"[⚠️] 伺服器回應：{detail}")
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
            # 同專案模式：RPD 算在「專案 × 模型」，所有 key 共用同一份額度。
            # 記在模型上（到太平洋時間午夜才恢復）→ 換下一個模型；全部模型都耗盡才結束。
            model_name = runtime.model_pool[model_index]
            quota = get_model_quota_registry()
            quota.mark_exhausted(model_name)
            log_warning(
                f"[🚫] 每日限額已滿 (RPD)：模型 {model_name}（所有 Key 共用同一專案額度），"
                f"約 {_format_remaining(quota.seconds_remaining(model_name))} 後重置"
            )
            usable = _usable_model_indices(runtime, quota)
            return decide_batch_action(
                error_kind, quota_kind="rpd", has_next_model=bool(usable)
            ).action
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
    *,
    optional_retry: bool = False,
) -> tuple[list[dict] | None, bool]:
    """Parse, validate, and restore ordered translations for one successful response."""
    parsed = safe_json_loads(raw_text)
    expected_ids = set(round_data.id_to_item)
    contract_error = _structured_response_shape_error(parsed)
    normalized = (
        {item["id"]: item["value"] for item in parsed["items"]}
        if contract_error is None
        else {}
    )
    returned_ids = set(normalized)
    if contract_error is None and returned_ids != expected_ids:
        missing_ids = sorted(expected_ids - returned_ids)
        unexpected_ids = sorted(returned_ids - expected_ids)
        contract_error = f"missing IDs={missing_ids}, unexpected IDs={unexpected_ids}"
    if contract_error is None and any(
        not isinstance(value, str) for value in normalized.values()
    ):
        contract_error = "one or more translation values are not strings"
    if contract_error is not None:
        if not optional_retry:
            log_warning(f"[❌ 回應契約不符] {contract_error}；本批次將重試/縮小")
            runtime.budget_tracker.on_truncated(
                api_meta.get("finish_reason"), kind="missing"
            )
        return None, True

    merged: list[dict] = []
    for temp_id, original_item in round_data.id_to_item.items():
        translated_text = normalized.get(temp_id, original_item["text"])
        if not translated_text or not str(translated_text).strip():
            log_warning("[⚠️ 空翻譯] path=%s", original_item["path"])
        if len(original_item["text"]) > 0 and (
            len(str(translated_text)) / len(original_item["text"]) > 3
        ):
            log_warning("[⚠️ 異常長度] path=%s", original_item["path"])
        merged.append({**original_item, "text": translated_text})
    return merged, False


def _retry_same_source_translations(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    result: list[dict],
    *,
    prompt: str,
    model_name: str,
    api_key: str,
    output_cap: int | None,
) -> list[dict]:
    """Reconfirm only same-as-source candidates; keep the first valid result on failure."""
    candidates = build_same_source_retry_batch(
        round_data.current_batch, result, list(round_data.id_to_item)
    )
    if not candidates.positions:
        return result
    if not runtime.retry_same_as_source:
        log_info(
            f"[📊 本批次翻譯與原文相同 "
            f"{len(candidates.positions)}/{len(round_data.current_batch)}]"
        )
        return result

    retry_prompt = f"{prompt.rstrip()}\n\n{SAME_SOURCE_RETRY_INSTRUCTION}"
    retry_estimate = runtime.budget_tracker.estimate(
        candidates.items, runtime.budget_cfg, runtime.retry_fixed_input_tokens
    )
    retry_round = _BatchRound(
        current_batch=list(candidates.items),
        fit_count=len(candidates.items),
        estimate=retry_estimate,
        payload=candidates.payload,
        id_to_item=candidates.id_to_item,
    )
    candidate_count = len(candidates.positions)
    log_info(
        f"[🔁 本批次翻譯與原文相同 {candidate_count}/{len(round_data.current_batch)}，"
        f"重新確認一次；預估輸入≈{round(retry_estimate.input_tokens)} tokens]"
    )
    try:
        raise_if_cancelled()
        retry_meta: dict = {}
        request_kwargs = _provider_request_kwargs(
            runtime,
            model_name,
            retry_prompt,
            retry_round.payload,
            output_cap,
            retry_meta,
            api_key,
        )
        retry_text = call_gemini_requests(**request_kwargs).strip()
        raise_if_cancelled()
        if (
            not retry_text
            or retry_meta.get("finish_reason") == "MAX_TOKENS"
            or _is_truncated_response(retry_text)
        ):
            log_warning(
                f"[⚠️ 相同譯文重新確認失敗，保留第一次翻譯結果：{candidate_count} 筆]"
            )
            return result
        retried, invalid = _merge_batch_response(
            runtime, retry_round, retry_text, retry_meta, optional_retry=True
        )
        if invalid or retried is None:
            log_warning(
                f"[⚠️ 相同譯文重新確認失敗，保留第一次翻譯結果：{candidate_count} 筆]"
            )
            return result
    except Exception:  # noqa: BLE001 - optional quality retry must not fail the valid batch
        log_warning(
            f"[⚠️ 相同譯文重新確認失敗，保留第一次翻譯結果：{candidate_count} 筆]"
        )
        return result

    accepted_positions: list[int] = []
    accepted_results: list[dict] = []
    blank_count = 0
    for position, retry_item in zip(candidates.positions, retried, strict=True):
        retry_text = retry_item.get("text")
        if not isinstance(retry_text, str) or not retry_text.strip():
            blank_count += 1
            continue
        accepted_positions.append(position)
        accepted_results.append(retry_item)
    if blank_count:
        log_warning(
            f"[⚠️ 相同譯文重新確認回傳空白，保留第一次翻譯結果：{blank_count} 筆]"
        )

    merged = merge_same_source_retry_results(
        result, tuple(accepted_positions), accepted_results
    )
    updated = sum(
        retry_item["text"] != result[position]["text"]
        for position, retry_item in zip(
            accepted_positions, accepted_results, strict=True
        )
    )
    log_info(
        f"[✅ 相同譯文重新確認完成：{candidate_count} 筆，其中 {updated} 筆更新、"
        f"{candidate_count - updated} 筆維持原文]"
    )
    return merged


def _clear_quota_on_http_success(
    quota, error: Exception, model_name: str, started: float
) -> None:
    """HTTP 200 但回應格式異常：已證明沒有被 RPD 拒絕，配額紀錄一樣清除（翻譯失敗另外處理）。"""
    if isinstance(error, GeminiResponseFormatError):
        quota.mark_ok(model_name, started_at=started)


def _abandon_model(
    runtime: _BatchRuntime,
    quota,
    model_index: int,
    model_indices: list[int],
    pinned_round: bool,
) -> None:
    """放棄目前的模型、改試其他模型的單一入口。

    - 收回它的探測租約並重新計時（只有同一模型的 RPM／503 重試才保留租約）。
    - 這一輪是「被釘住」的（503 overload 重試只走那一個模型）：解除釘選，並把整個模型池加進這一輪
      的候選，否則迴圈只有被釘住的模型，放棄後其他模型根本不會被試到。
    """
    quota.release(runtime.model_pool[model_index], runtime.quota_owner)
    if pinned_round:
        runtime.pinned_model_index = None
        model_indices.extend(
            [i for i in range(len(runtime.model_pool)) if i not in model_indices]
        )


def _probe_lease_sec(runtime: _BatchRuntime) -> float:
    """探測租約長度：涵蓋用戶端整段最壞耗時（連線重試 × 逾時 + 退避）+ 餘裕。"""
    try:
        timeout = float((runtime.lm_cfg.get("rate_limit") or {}).get("timeout", 600))
    except (TypeError, ValueError):
        timeout = 600.0
    return worst_case_request_sec(timeout) + PROBE_LEASE_MARGIN_SEC


def _plan_model_indices(runtime: _BatchRuntime, quota) -> list[int] | None:
    """這一輪要走的模型索引；所有可用模型都被每日配額擋住時回傳 None（= 耗盡，不送請求）。"""
    owner = runtime.quota_owner
    live = [
        i for i in range(len(runtime.model_pool)) if i not in runtime.missing_models
    ]
    if live and all(quota.is_blocked(runtime.model_pool[i], owner) for i in live):
        log_warning(
            "[🚫] 所有啟用的模型今日配額（RPD）都已用盡，"
            f"最快約 {_format_remaining(quota.soonest_reset_in(runtime.model_pool) or 0)} 後重置"
        )
        return None
    pinned = runtime.pinned_model_index
    if pinned is not None and quota.is_blocked(runtime.model_pool[pinned], owner):
        runtime.pinned_model_index = None  # 被釘住的模型已耗盡：改走完整模型池
    indices = (
        [runtime.pinned_model_index]
        if runtime.pinned_model_index is not None
        else list(range(len(runtime.model_pool)))
    )
    return indices


def _dead_end_outcome(
    runtime: _BatchRuntime,
    quota,
    model_indices: list[int],
    skipped_by_quota: set[int],
) -> _BatchRoundOutcome | None:
    """這一輪沒有任何模型能送請求（都是 404 或被每日配額擋住）時的終止結果；否則回傳 None。

    看的是模型「現在」的狀態，而不是它怎麼變成被擋住的：claim 時就被擋下、這一輪送出請求後才
    收到 RPD、探測名額被其他 worker 領走，結果都一樣。搶不到探測名額的模型若已被對方恢復，則重試
    這一輪。縮小 batch 對沒有任何模型可用的情況沒有用，而且會把原文回填成「已翻譯」。
    """
    blocked_now = {
        index
        for index in model_indices
        if quota.is_blocked(runtime.model_pool[index], runtime.quota_owner)
    }
    if skipped_by_quota - blocked_now - runtime.missing_models:
        # 搶不到探測名額的模型，在我們收尾前已被贏得探測的 worker 恢復了：重新走這一輪，不能誤報耗盡。
        return _BatchRoundOutcome(BatchAction.RETRY_SAME_MODEL)
    if not all(
        index in runtime.missing_models or index in blocked_now
        for index in model_indices
    ):
        return None
    if blocked_now:
        log_warning(
            "[🚫] 沒有可用的模型：其餘模型今日配額已用盡或正由其他 worker 探測中"
        )
        return _BatchRoundOutcome(BatchAction.EXHAUSTED)
    log_error("[❌] 所有啟用的模型都不存在或無法使用，請檢查模型名稱設定")
    return _BatchRoundOutcome(BatchAction.FAIL)


def _finish_successful_batch(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    result: list[dict],
    api_meta: dict,
    *,
    prompt: str,
    model_name: str,
    api_key: str,
    output_cap: int | None,
) -> _BatchRoundOutcome:
    """Reconfirm optional candidates, then advance the validated batch exactly once."""
    finalized = _retry_same_source_translations(
        runtime,
        round_data,
        result,
        prompt=prompt,
        model_name=model_name,
        api_key=api_key,
        output_cap=output_cap,
    )
    runtime.completed_calls += 1
    runtime.all_results.extend(finalized)
    actual_output = api_meta.get("candidates_tokens")
    if isinstance(actual_output, int):
        actual_output += api_meta.get("thoughts_tokens") or 0
    runtime.budget_tracker.on_success(
        runtime.budget_cfg,
        value_tokens=round_data.estimate.value_tokens,
        item_count=len(round_data.current_batch),
        actual_output_tokens=actual_output,
    )
    runtime.remaining_items = runtime.remaining_items[len(round_data.current_batch) :]
    runtime.batch_size = min(runtime.batch_size, len(runtime.remaining_items))
    runtime.key_cycle.record_success()
    runtime.pinned_model_index = None
    if not runtime.remaining_items and runtime.rpm_cooldown_sec > 0:
        interruptible_sleep(runtime.rpm_cooldown_sec)
    return _BatchRoundOutcome()


def _provider_request_kwargs(
    runtime, model_name, prompt, payload, output_cap, meta_out, api_key
):
    """Build shared and ChatGPT-specific arguments for one provider request."""
    kwargs = {
        "model_name": model_name,
        "system_prompt": prompt,
        "payload": payload,
        "api_key": api_key,
        "temperature": runtime.model_temperature,
        "max_output_tokens": output_cap,
        "meta_out": meta_out,
        "provider": getattr(
            runtime, "provider", runtime.lm_cfg.get("provider", "gemini")
        ),
        "lm_config": runtime.lm_cfg,
    }
    if kwargs["provider"] == "chatgpt":
        kwargs["reasoning_effort"] = runtime.reasoning_effort
    return kwargs


def _attempt_batch(
    runtime: _BatchRuntime, round_data: _BatchRound
) -> _BatchRoundOutcome:
    """Try the model pool and return an explicit action for the outer state machine."""
    quota = get_model_quota_registry()
    owner = runtime.quota_owner
    model_indices = _plan_model_indices(runtime, quota)
    if model_indices is None:
        return _BatchRoundOutcome(BatchAction.EXHAUSTED)
    pinned_round = runtime.pinned_model_index is not None
    skipped_by_quota: set[int] = set()
    for model_index in model_indices:
        raise_if_cancelled()
        if model_index in runtime.missing_models:
            continue
        model_name = runtime.model_pool[model_index]
        lease_sec = _probe_lease_sec(runtime)
        if not quota.claim(model_name, owner, lease_sec):
            # 今日配額已用盡且還沒輪到探測（或探測名額被其他 worker 領走）：不白打請求
            skipped_by_quota.add(model_index)
            continue
        prompt = (
            runtime.lang_prompt
            if runtime.batch_profile in {"lang", "kubejs"}
            else runtime.patchouli_prompt
        )
        output_cap = None
        cap_source = "global"
        started = quota.now()  # 只有「耗盡紀錄之後才開始」的成功能清除紀錄
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
            with quota.hold_probe_lease(model_name, owner, lease_sec):
                api_key = (
                    runtime.key_cycle.claim() if runtime.provider == "gemini" else ""
                )
                request_kwargs = _provider_request_kwargs(
                    runtime,
                    model_name,
                    prompt,
                    round_data.payload,
                    output_cap,
                    api_meta,
                    api_key,
                )
                raw_text = call_gemini_requests(**request_kwargs).strip()
            if runtime.provider == "chatgpt":
                runtime.chatgpt_retry_count = 0
            # A synchronous provider request cannot be interrupted in flight.
            # If cancellation arrived while it was blocked, discard its result
            # before quota state, retries, or downstream writes can observe it.
            raise_if_cancelled()
            # HTTP 200 已證明沒有被 RPD 拒絕：配額紀錄在這裡就清除；回應內容的問題（空、截斷、
            # 格式不符）由 batch 流程自己處理，不影響配額狀態。
            quota.mark_ok(model_name, started_at=started)
            if not raw_text:
                _abandon_model(runtime, quota, model_index, model_indices, pinned_round)
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
            return _finish_successful_batch(
                runtime,
                round_data,
                result or [],
                api_meta,
                prompt=prompt,
                model_name=model_name,
                api_key=api_key,
                output_cap=output_cap,
            )
        except Exception as error:  # noqa: BLE001
            _clear_quota_on_http_success(quota, error, model_name, started)
            action = _handle_batch_error(
                runtime,
                error,
                model_index,
                model_output_cap=output_cap,
                cap_source=cap_source,
            )
            if action is BatchAction.NEXT_MODEL:
                _abandon_model(runtime, quota, model_index, model_indices, pinned_round)
                continue
            return _BatchRoundOutcome(action)

    dead_end = _dead_end_outcome(runtime, quota, model_indices, skipped_by_quota)
    if dead_end is not None:
        return dead_end

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
    try:
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
                    fixed_input_tokens=runtime.batch_fit_input_tokens,
                )
                if next_fit < len(round_data.current_batch):
                    continue

            shrink_factor = float(
                runtime.lm_cfg.get("batch_shrink_factor", 0.75) or 0.75
            )
            basis = min(runtime.batch_size, len(round_data.current_batch))
            new_size = int(basis * shrink_factor)
            min_size = int(runtime.lm_cfg.get("min_batch_size", 50) or 50)
            if runtime.batch_profile == "lang" and new_size < MIN_LANG_BATCH_SIZE:
                new_size = MIN_LANG_BATCH_SIZE if basis > MIN_LANG_BATCH_SIZE else 0
            elif new_size < min_size:
                new_size = min_size if basis > min_size else 0

            if new_size <= 0 or new_size == basis:
                log_warning(
                    f"[⚠️] Batch Size 已縮至極限 ({basis})，回填原文並繼續後續項目"
                )
                runtime.all_results.extend(
                    {**item, "_untranslated": True} for item in round_data.current_batch
                )
                runtime.remaining_items = runtime.remaining_items[
                    len(round_data.current_batch) :
                ]
                runtime.batch_size = min(
                    len(runtime.remaining_items),
                    MIN_LANG_BATCH_SIZE
                    if runtime.batch_profile == "lang"
                    else min_size,
                )
                continue
            log_info(f"[↓] 調整 Batch：{basis} → {new_size}")
            runtime.batch_size = new_size

        return runtime.all_results, "AUTO"
    finally:
        # 不論怎麼結束，都收回這次呼叫持有的探測租約（成功時紀錄已被清除，這裡不會有東西）。
        get_model_quota_registry().release_owner(runtime.quota_owner)


# 暫保舊名稱供外部整合程式相容；現役入口與內部呼叫已不再依賴 old 命名。
translate_batch_smart_old = _translate_batch_smart_impl
