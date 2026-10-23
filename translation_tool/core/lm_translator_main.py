"""translation_tool/core/lm_translator_main.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import json
import math
import random
import time
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field

import requests

from translation_tool.core.batch_timing import seconds_to_ms
from translation_tool.core.codex_oauth import chatgpt_account_status
from translation_tool.core.common_batch_executor import classify_provider_error
from translation_tool.core.lm_api_client import (
    GeminiResponseFormatError,
    call_gemini_requests,
    worst_case_request_sec,
)
from translation_tool.core.lm_batch_actions import BatchAction, decide_batch_action
from translation_tool.core.lm_batch_budget import (
    PROFILE_BY_CACHE_TYPE,
    BudgetConfig,
    estimate_payload_tokens,
    estimate_prompt_tokens,
    get_tracker,
    profile_for_cache_type,
    select_batch_size,
    uses_lang_prompt,
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
from translation_tool.core.lm_format_policy import build_translation_payload
from translation_tool.core.lm_format_retry import (
    QUALITY_RETRY_INSTRUCTION,
    build_quality_retry_batch,
    quality_issues,
)
from translation_tool.core.lm_key_health import (
    PROBE_LEASE_MARGIN_SEC,
    get_model_quota_registry,
    set_active_model,
)
from translation_tool.core.lm_response_parser import safe_json_loads
from translation_tool.core.lm_same_source_retry import (
    SAME_SOURCE_RETRY_INSTRUCTION,
    build_same_source_retry_batch,
    merge_same_source_retry_results,
)
from translation_tool.core.lm_session import TranslationSessionState
from translation_tool.core.official_locale import (
    build_translation_prompt,
    official_translation_for_item,
    terminology_issues,
    terminology_terms_for_item,
)
from translation_tool.core.retry_after import parse_response_retry_after
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    interruptible_sleep,
    raise_if_cancelled,
)
from translation_tool.utils.config_manager import get_models_config, load_config
from translation_tool.utils.config_schema import (
    get_schema_default,
    validate_batch_shrink_factor,
)
from translation_tool.utils.log_unit import log_error, log_info, log_warning
from translation_tool.utils.redaction import redact_text

# =========================================================
# Time Constants - 時間相關常數
# =========================================================
# 每批完成後的固定等待秒數（預設不等待；遇到 429 會依 API 建議秒數重試）。
# 免費層若常遇到 429，可在設定頁把「每批翻譯後等待秒數」調高。
RPM_COOLDOWN_SEC = get_schema_default("lm_translator.rpm_cooldown_sec")
OVERLOAD_RETRY_WAIT_SEC = 12  # Overload 重試等待秒數
KEY_ROTATION_BUFFER_SEC = 5  # 換 Key 前的緩衝秒數
REQUEST_INTERVAL_SEC = 4  # 非 overload 的 503 後的等待秒數
# Gemini 503 UNAVAILABLE/high-demand：最多重試三次，重試等待逐步增加。
GEMINI_503_RETRY_DELAYS_SEC = (30, 60, 120)
GEMINI_503_MAX_RETRY_AFTER_SEC = 120
# 同一把 API key 累積這麼多次 503 overload 後，才把「這一把」標記為失敗並換下一把。
# 次數是逐把 key 計算：不同 key 的 overload 不互相累加。
OVERLOAD_KEY_SWITCH_THRESHOLD = 3
CHATGPT_MAX_RETRIES = 3
# 429 沒有任何可用的等待提示時的保守等待秒數。
RATE_LIMIT_DEFAULT_WAIT_SEC = 10
# 429 回應無法解析時，連續保守重試同一把 key 的次數；超過才當成該 key 受限並輪替。
UNPARSED_RATE_LIMIT_MAX_RETRIES = 3
_EXPLICIT_QUOTA_MESSAGE_MARKERS = (
    "QUOTA EXCEEDED",
    "QUOTA EXHAUSTED",
    "INSUFFICIENT QUOTA",
    "USAGE LIMIT EXCEEDED",
    "CREDIT BALANCE EXHAUSTED",
)
CHATGPT_MAX_RETRY_AFTER_SEC = 120
CHATGPT_TRANSIENT_USAGE_ERROR_CODES = frozenset(
    {
        "subscription_sharing_usage_unavailable",
        "subscription_sharing_user_unavailable",
    }
)
CHATGPT_PERMANENT_QUOTA_ERROR_CODES = frozenset(
    {
        "credit_balance_exhausted",
        "insufficient_quota",
        "usage_limit",
        "spend_limit",
        "quota_exceeded",
        "subscription_sharing_usage_limit_exceeded",
    }
)

# =========================================================
# Size Constants - 大小相關常數
# =========================================================
MIN_LANG_BATCH_SIZE = 20  # Lang 類型最小批次大小
# 批次 profile -> ``lm_translator.initial_batch_size_*`` 的設定後綴；預設值一律取自 schema。
_BATCH_PROFILE_SIZE_KEYS = {
    "lang": "lang",
    "ftb": "ftb",
    "kubejs": "kubejs",
    "md": "md",
    "patch": "patchouli",
}
_DEFAULT_BATCH_SLEEP = float(
    get_schema_default("lm_translator.rate_limit.sleep_seconds_between_batches")
)


# =========================================================
# 預設參數
# =========================================================
DEFAULT_DRY_RUN = False  # 預設不跳過 API
DEFAULT_EXPORT_CACHE_ONLY = False  # 預設進行完整翻譯
CONVERSATION_TURN_OVERHEAD_TOKENS = 4


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
            if cache_type in PROFILE_BY_CACHE_TYPE:
                return profile_for_cache_type(cache_type)
        for cache_type in PROFILE_BY_CACHE_TYPE:
            if cache_type in cache_types:
                return profile_for_cache_type(cache_type)

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
    """拒絕違反 items、id、value 契約的結構化回應。"""
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
    *,
    event_callback=None,
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
    # 官方三欄資料若隨項目提供，先做 key-aware exact lookup；命中項目不呼叫 API。
    # 沒有 zh_cn 的待翻譯項目仍會走 LLM，並可使用同一份官方術語提示。
    if not dry_run:
        resolved: dict[int, dict] = {}
        pending: list[tuple[int, dict]] = []
        for index, item in enumerate(items):
            official_text = official_translation_for_item(item)
            if official_text is None:
                pending.append((index, item))
            else:
                resolved[index] = {
                    **item,
                    "text": official_text,
                    "_official_exact": True,
                }
        if resolved and not pending:
            return [resolved[index] for index in range(len(items))], "AUTO"
        if get_translation_provider() == "chatgpt":
            validate_translation_credentials()
        elif get_api_key_count() == 0:
            raise RuntimeError("❌ 設定檔中沒有找到任何 API Key，請先設定金鑰。")
        if resolved:
            translated, status = _execute_translation(
                [item for _index, item in pending],
                total,
                dry_run,
                event_callback=event_callback,
            )
            translated = list(translated or [])
            merged: list[dict] = []
            pending_index = 0
            for index in range(len(items)):
                if index in resolved:
                    merged.append(resolved[index])
                else:
                    source_item = pending[pending_index][1]
                    translated_item = (
                        translated[pending_index]
                        if pending_index < len(translated)
                        else {
                            **source_item,
                            "text": source_item.get("source_text", source_item["text"]),
                            "_untranslated": True,
                        }
                    )
                    merged.append(translated_item)
                    pending_index += 1
            return _process_output(merged, status)

    # 批次 profile 與批次大小由 _execute_translation 內部決定（舊版在這裡重複計算後丟棄，已移除）。

    # 2. 執行翻譯
    results, status = _execute_translation(
        items, total, dry_run, event_callback=event_callback
    )

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


def _execute_translation(items, total, dry_run=False, *, event_callback=None):
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
    return _translate_batch_smart_impl(
        items, total, dry_run, event_callback=event_callback
    )


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
    classified = classify_provider_error(error, status)
    return {
        "invalid_request": "invalid_argument",
        "rate_limit": "rate_limited",
        "provider_transient": "server_error",
        "provider_timeout": "deadline_exceeded",
        "context_too_large": "invalid_argument",
    }.get(classified["error_class"], classified["error_class"])


def _emit_runtime_event(callback, kind: str, **fields) -> None:
    """將翻譯執行期間的安全事件交給 runtime callback；沒有 callback 時不做任何事。"""
    if callback is None:
        return
    try:
        callback({"kind": kind, **fields})
    except Exception:  # noqa: BLE001 - telemetry must never break translation
        log_info("[SharedLM] provider telemetry callback failed")


def _sleep_with_runtime_event(
    runtime,
    wait_seconds: float,
    *,
    reason: str,
    callback=None,
    kind: str = "retry_wait",
) -> None:
    """以可取消的分段等待執行 retry 或配額等待，並回報等待進度事件。"""
    wait_seconds = max(0.0, float(wait_seconds or 0))
    if wait_seconds <= 0:
        return
    _emit_runtime_event(
        callback,
        kind,
        wait_ms=seconds_to_ms(wait_seconds),
        retry_reason=reason,
        provider_retry=True,
    )
    started = time.monotonic()
    interruptible_sleep(wait_seconds)
    resume_kind = {
        "rate_limit_wait": "rate_limit_resume",
        "retry_wait": "retry_resume",
    }.get(kind, "retry_resume")
    _emit_runtime_event(
        callback,
        resume_kind,
        wait_ms=seconds_to_ms(wait_seconds),
        waited_ms=seconds_to_ms(time.monotonic() - started),
        retry_reason=reason,
    )


def _payload_error_allows_shrink(error, status: int | None = None) -> bool:
    """只在供應商明確指出 payload/context 過大時允許縮小批次。

    這是翻譯主迴圈與智慧校對共用的保守判斷。400/422 也可能代表
    schema、權限或模型設定錯誤；若訊息沒有明確的大小證據，必須一次失敗，
    不得把同一個永久錯誤重打成一堆單筆請求。
    """
    if status is None and isinstance(error, requests.HTTPError):
        response = error.response
        status = response.status_code if response is not None else None
    if status == 413:
        return True
    if status not in {400, 422}:
        return False

    parts = [str(error)]
    try:
        parts.append(_remote_error_detail(error, limit=500))
    except Exception as detail_error:  # noqa: BLE001 - 判斷本身不能遮蔽原始錯誤
        parts.append(f"detail_error:{type(detail_error).__name__}")
    code = getattr(error, "code", "")
    error_type = getattr(error, "error_type", "")
    param = getattr(error, "param", "")
    parts.extend(str(value) for value in (code, error_type, param))
    text = " ".join(parts).lower()
    size_markers = (
        "request too large",
        "request body too large",
        "request entity too large",
        "payload too large",
        "payload size",
        "payload exceeds",
        "body too large",
        "input too large",
        "context length",
        "context window",
        "input too long",
        "input length",
        "maximum context",
        "token limit",
        "too many tokens",
        "content length",
        "exceeds the limit",
    )
    return (
        any(marker in text for marker in size_markers)
        or (
            any(noun in text for noun in ("payload", "request", "input", "content"))
            and any(
                qualifier in text for qualifier in ("too large", "too long", "exceed")
            )
        )
        or (
            "context" in text
            and any(qualifier in text for qualifier in ("too long", "exceed"))
        )
    )


# =========================================================
# 舊翻譯函數（保留原邏輯）
# =========================================================


def _translate_batch_smart_impl(
    batch_items, total=None, dry_run=False, *, event_callback=None
):
    """執行既有翻譯 contract；詳細 state machine 由命名 helper 承擔。"""
    return _run_batch_state_machine(
        batch_items, total, dry_run, event_callback=event_callback
    )


@dataclass
class _BatchRuntime:
    """供翻譯狀態機各協調 helper 共用的可變執行狀態。"""

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
    effective_prompt: str
    fixed_input_tokens: float
    batch_fit_input_tokens: float
    retry_fixed_input_tokens: float
    quality_retry_fixed_input_tokens: float
    retry_same_as_source: bool
    original_total: int | None
    remaining_items: list[dict]
    all_results: list[dict]
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    session_state: TranslationSessionState | None = None
    completed_calls: int = 0
    pinned_model_index: int | None = None
    # 同專案模式的模型配額：本次呼叫用來持有「探測租約」的身分（見 ModelQuotaRegistry）、
    # 已確定不存在（404）的模型。
    quota_owner: object = field(default_factory=object)
    missing_models: set[int] = field(default_factory=set)
    rpm_cooldown_sec: float = RPM_COOLDOWN_SEC
    key_rotation_buffer_sec: float = KEY_ROTATION_BUFFER_SEC
    overload_retry_sec: float = OVERLOAD_RETRY_WAIT_SEC
    request_interval_sec: float = REQUEST_INTERVAL_SEC
    batch_interval_sec: float = 4
    reasoning_effort: str | None = None
    chatgpt_profile_id: str | None = None
    chatgpt_retry_count: int = 0
    gemini_503_retry_count: int = 0
    unparsed_rate_limit_count: int = 0
    retry_same_key: bool = False
    gemini_503_attempted_models: set[int] = field(default_factory=set)
    contract_errors: list[str] = field(default_factory=list)
    pending_contract_event: dict | None = None
    event_callback: object | None = None
    provider_attempts: int = 0
    remaining_item_ids: list[str] = field(default_factory=list)
    terminology_cache: dict[int, tuple[tuple[str, str], ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class _BatchRound:
    current_batch: list[dict]
    fit_count: int
    estimate: object
    payload: dict
    id_to_item: dict[str, dict]


@dataclass(frozen=True)
class _BatchPromptContext:
    """Prompt and fixed-token values shared by preview and live batching."""

    profile: str
    lang_prompt: str
    patchouli_prompt: str
    effective_prompt: str
    conversation_history: list[dict[str, str]]
    history_input_tokens: float
    fixed_input_tokens: float
    batch_fit_input_tokens: float
    retry_fixed_input_tokens: float
    quality_retry_fixed_input_tokens: float
    retry_same_as_source: bool


@dataclass
class _BatchRetryBudget:
    """Allow at most one application-level translation retry per batch."""

    used: bool = False


@dataclass(frozen=True)
class _BatchRoundOutcome:
    action: BatchAction | None = None
    learned_budget: bool = False
    fallback_reason: str | None = None


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
    """正規化設定提示詞，不把設定處理混入批次迴圈。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("content") or value.get("text") or value)
    return str(value or fallback)


def _estimate_conversation_history_tokens(
    conversation_history: list[dict[str, str]],
    *,
    estimator=None,
) -> float:
    """估算重試請求已攜帶的對話歷史輸入 Token。"""
    return sum(
        estimate_prompt_tokens(
            turn.get("text", ""), estimator=estimator, include_request_bias=False
        )
        + CONVERSATION_TURN_OVERHEAD_TOKENS
        for turn in conversation_history
    )


def _estimate_retry_input_tokens(
    prompt: str,
    payload: dict,
    conversation_history: list[dict[str, str]],
    *,
    estimator=None,
) -> float:
    """估算品質重試請求實際送出的完整輸入 Token。"""
    return (
        estimate_prompt_tokens(prompt, estimator=estimator)
        + _estimate_conversation_history_tokens(
            conversation_history, estimator=estimator
        )
        + estimate_payload_tokens(payload, estimator=estimator)
    )


def _refresh_runtime_prompt_budget(
    runtime: _BatchRuntime,
    *,
    prompt_items: list[dict] | None = None,
    prompt_ids: list[str] | None = None,
) -> None:
    """依目前實際負載重建提示詞並重新計算固定輸入預算。"""
    if prompt_items is not None:
        base_prompt = (
            runtime.lang_prompt
            if uses_lang_prompt(runtime.batch_profile)
            else runtime.patchouli_prompt
        )
        runtime.effective_prompt = build_translation_prompt(
            base_prompt,
            prompt_items,
            profile=runtime.batch_profile,
            include_official=bool(runtime.lm_cfg.get("official_glossary", True)),
            include_moddb_reviewed=bool(
                runtime.lm_cfg.get("moddb_reviewed_glossary", True)
            ),
            item_ids=prompt_ids,
        )
    estimator = runtime.budget_cfg.token_estimator
    history_input_tokens = _estimate_conversation_history_tokens(
        runtime.conversation_history, estimator=estimator
    )
    prompt_tokens = estimate_prompt_tokens(
        runtime.effective_prompt, estimator=estimator
    )
    retry_prompt_tokens = estimate_prompt_tokens(
        f"{runtime.effective_prompt.rstrip()}\n\n{SAME_SOURCE_RETRY_INSTRUCTION}",
        estimator=estimator,
    )
    quality_retry_prompt_tokens = estimate_prompt_tokens(
        f"{runtime.effective_prompt.rstrip()}\n\n{QUALITY_RETRY_INSTRUCTION}",
        estimator=estimator,
    )
    runtime.fixed_input_tokens = prompt_tokens + history_input_tokens
    runtime.retry_fixed_input_tokens = retry_prompt_tokens
    runtime.quality_retry_fixed_input_tokens = quality_retry_prompt_tokens
    batch_prompt_fit_tokens = (
        retry_prompt_tokens if runtime.retry_same_as_source else prompt_tokens
    )
    runtime.batch_fit_input_tokens = (
        max(batch_prompt_fit_tokens, quality_retry_prompt_tokens) + history_input_tokens
    )


def _batch_interval_seconds(lm_cfg: dict) -> float:
    rate_cfg = lm_cfg.get("rate_limit", {})
    if not isinstance(rate_cfg, dict):
        rate_cfg = {}
    try:
        return max(
            0.0,
            float(rate_cfg.get("sleep_seconds_between_batches", _DEFAULT_BATCH_SLEEP)),
        )
    except (TypeError, ValueError):
        return _DEFAULT_BATCH_SLEEP


def _wait_for_request_slot(
    runtime: _BatchRuntime,
    model_name: str,
    *,
    cancel_check=None,
    wait_event_callback=None,
) -> None:
    """透過共用模型時鐘節流每一次 provider 請求。"""
    quota = get_model_quota_registry()
    provider = getattr(runtime, "provider", None)
    if not provider:
        provider = runtime.lm_cfg.get("provider", "gemini")
    _wait_sec, reservation = quota.reserve_request_slot_with_token(
        provider,
        model_name,
        runtime.batch_interval_sec,
    )
    try:
        while True:
            if cancel_check is not None and cancel_check():
                raise TaskCancelled()
            wait_sec = quota.request_slot_wait(reservation)
            waited_target = reservation.target_at if wait_sec > 0 else None
            if wait_sec > 0:
                if wait_event_callback is not None:
                    wait_event_callback(
                        {
                            "kind": "rate_limit_wait",
                            "wait_ms": seconds_to_ms(wait_sec),
                        }
                    )
                wait_started = quota.now()
                sleep_context = (
                    cancel_scope(cancel_check)
                    if cancel_check is not None
                    else nullcontext()
                )
                with sleep_context:
                    interruptible_sleep(wait_sec)
                if wait_event_callback is not None:
                    wait_event_callback(
                        {
                            "kind": "rate_limit_resume",
                            "wait_ms": seconds_to_ms(wait_sec),
                            "waited_ms": seconds_to_ms(quota.now() - wait_started),
                        }
                    )
            raise_if_cancelled()
            if cancel_check is not None and cancel_check():
                raise TaskCancelled()
            if quota.commit_request_slot(reservation, waited_target=waited_target):
                return
    except BaseException:
        quota.cancel_request_slot(reservation)
        raise


def _build_batch_prompt_context(
    batch_items: list[dict],
    lm_cfg: dict,
    *,
    item_ids: list[str] | None = None,
) -> _BatchPromptContext:
    """建立所有批次規劃器共用的提示詞與 Token 預算契約。"""
    profile = _detect_batch_profile(batch_items)
    lang_prompt = _prompt_text(
        lm_cfg.get("lang_system_prompt"),
        "你正在翻譯 Minecraft 語言檔案（JSON格式）。",
    )
    patchouli_prompt = _prompt_text(
        lm_cfg.get("patchouli_system_prompt"),
        "你是專業的 Minecraft Patchouli 手冊翻譯員",
    )
    fixed_prompt = lang_prompt if uses_lang_prompt(profile) else patchouli_prompt
    session_state = next(
        (
            item.get("_translation_session")
            for item in batch_items
            if isinstance(item.get("_translation_session"), TranslationSessionState)
        ),
        None,
    )
    conversation_history = session_state.snapshot() if session_state is not None else []
    effective_prompt = build_translation_prompt(
        fixed_prompt,
        batch_items,
        profile=profile,
        include_official=bool(lm_cfg.get("official_glossary", True)),
        include_moddb_reviewed=bool(lm_cfg.get("moddb_reviewed_glossary", True)),
        item_ids=item_ids,
    )
    estimator = BudgetConfig.from_config(lm_cfg).token_estimator
    retry_same_as_source = bool(lm_cfg.get("retry_same_as_source", True))
    history_input_tokens = _estimate_conversation_history_tokens(
        conversation_history, estimator=estimator
    )
    fixed_input_tokens = estimate_prompt_tokens(
        effective_prompt, estimator=estimator
    ) + history_input_tokens
    retry_fixed_input_tokens = estimate_prompt_tokens(
        f"{effective_prompt.rstrip()}\n\n{SAME_SOURCE_RETRY_INSTRUCTION}",
        estimator=estimator,
    )
    quality_retry_fixed_input_tokens = estimate_prompt_tokens(
        f"{effective_prompt.rstrip()}\n\n{QUALITY_RETRY_INSTRUCTION}",
        estimator=estimator,
    )
    batch_prompt_fit_tokens = (
        retry_fixed_input_tokens
        if retry_same_as_source
        else estimate_prompt_tokens(effective_prompt, estimator=estimator)
    )
    return _BatchPromptContext(
        profile=profile,
        lang_prompt=lang_prompt,
        patchouli_prompt=patchouli_prompt,
        effective_prompt=effective_prompt,
        conversation_history=conversation_history,
        history_input_tokens=history_input_tokens,
        fixed_input_tokens=fixed_input_tokens,
        batch_fit_input_tokens=max(
            batch_prompt_fit_tokens, quality_retry_fixed_input_tokens
        )
        + history_input_tokens,
        retry_fixed_input_tokens=retry_fixed_input_tokens,
        quality_retry_fixed_input_tokens=quality_retry_fixed_input_tokens,
        retry_same_as_source=retry_same_as_source,
    )


def estimate_batch_fixed_input_tokens(
    batch_items: list[dict], *, lm_cfg: dict | None = None
) -> float:
    """依即時批次執行路徑精確估算固定輸入 Token。"""
    if lm_cfg is None:
        config_snapshot = load_config()
        lm_cfg = config_snapshot.get("lm_translator", {})
    if not isinstance(lm_cfg, dict):
        lm_cfg = {}
    return _build_batch_prompt_context(batch_items, lm_cfg).batch_fit_input_tokens


def _profile_max_batch_sizes(lm_cfg: dict) -> dict[str, int | str | None]:
    """各批次 profile 的初始批次上限；設定缺漏時使用 schema 預設值。"""
    return {
        profile_name: lm_cfg.get(
            f"initial_batch_size_{key}",
            get_schema_default(f"lm_translator.initial_batch_size_{key}"),
        )
        for profile_name, key in _BATCH_PROFILE_SIZE_KEYS.items()
    }


# 內部隱藏設定（不在設定頁，需手動編輯 config.json）的等待秒數上限；超出或非有限值時退回預設。
HIDDEN_WAIT_SEC_MAX = 3600.0


def _hidden_wait_sec(lm_cfg: dict, key: str, default: float) -> float:
    """讀取隱藏的等待秒數設定：必須是 ``0 <= 有限數值 <= HIDDEN_WAIT_SEC_MAX``，否則用預設並記警告。"""
    raw = lm_cfg.get(key)
    if raw is None:
        return default
    try:
        value = float(raw) if not isinstance(raw, bool) else math.nan
    except (TypeError, ValueError):
        value = math.nan
    if math.isfinite(value) and 0 <= value <= HIDDEN_WAIT_SEC_MAX:
        return value
    log_warning(
        f"[⚠️] lm_translator.{key} 必須介於 0 到 {HIDDEN_WAIT_SEC_MAX:g} 秒，"
        f"收到 {raw!r}，改用預設 {default:g} 秒"
    )
    return default


def _build_batch_runtime(
    batch_items: list[dict],
    total: int | None,
    *,
    event_callback=None,
) -> _BatchRuntime | None:
    """建立單次批次翻譯共用的設定與可變執行狀態。"""
    config_snapshot = deepcopy(load_config())
    lm_cfg = config_snapshot.get("lm_translator", {})
    _batch_shrink_factor(lm_cfg)  # 設定非法時在送出任何請求前就報錯
    provider = lm_cfg.get("provider", "gemini")
    profile = _detect_batch_profile(batch_items)
    max_sizes = _profile_max_batch_sizes(lm_cfg)
    max_size = int(max_sizes.get(profile, max_sizes["patch"]) or 1)
    try:
        rpm_cooldown = max(0.0, float(lm_cfg.get("rpm_cooldown_sec", RPM_COOLDOWN_SEC)))
    except (TypeError, ValueError):
        rpm_cooldown = float(RPM_COOLDOWN_SEC)

    reasoning_effort = None
    chatgpt_profile_id = None
    if provider == "chatgpt":
        chatgpt_model = str(lm_cfg.get("chatgpt_model") or "").strip()
        model_pool = [chatgpt_model] if chatgpt_model else []
        if chatgpt_model:
            context_budget = chatgpt_model_input_token_budget(lm_cfg, chatgpt_model)
            if context_budget is not None:
                try:
                    configured_budget = int(
                        lm_cfg.get("max_input_token_budget")
                        or get_schema_default("lm_translator.max_input_token_budget")
                    )
                except (TypeError, ValueError):
                    configured_budget = int(
                        get_schema_default("lm_translator.max_input_token_budget")
                    )
                lm_cfg = {
                    **lm_cfg,
                    "max_input_token_budget": min(configured_budget, context_budget),
                }
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
    if provider == "chatgpt":
        chatgpt_profile_id = str(
            chatgpt_account_status().get("active_profile_id") or ""
        )
        if not chatgpt_profile_id:
            log_error("[❌] 找不到作用中的 ChatGPT 帳號，請先在設定頁連結並選擇帳號")
            return None
        configured_profile_id = str(lm_cfg.get("chatgpt_model_profile_id") or "")
        if configured_profile_id != chatgpt_profile_id:
            log_error(
                "[❌] ChatGPT 模型尚未確認適用於目前帳號；請在設定頁更新模型清單並儲存設定"
            )
            return None

    prompt_started = time.monotonic()
    prompt_context = _build_batch_prompt_context(batch_items, lm_cfg)
    _emit_runtime_event(
        event_callback,
        "timing",
        field_name="prompt_build_ms",
        elapsed_ms=round((time.monotonic() - prompt_started) * 1000),
    )
    return _BatchRuntime(
        batch_items=batch_items,
        total=total,
        lm_cfg=lm_cfg,
        provider=provider,
        batch_profile=prompt_context.profile,
        batch_size=min(len(batch_items), max_size),
        budget_cfg=BudgetConfig.from_config(lm_cfg),
        budget_tracker=get_tracker(profile),
        key_cycle=ApiKeyCycle(),
        model_pool=model_pool,
        model_temperature=lm_cfg.get(
            "temperature", get_schema_default("lm_translator.temperature")
        ),
        lang_prompt=prompt_context.lang_prompt,
        patchouli_prompt=prompt_context.patchouli_prompt,
        effective_prompt=prompt_context.effective_prompt,
        fixed_input_tokens=prompt_context.fixed_input_tokens,
        batch_fit_input_tokens=prompt_context.batch_fit_input_tokens,
        retry_fixed_input_tokens=prompt_context.retry_fixed_input_tokens,
        quality_retry_fixed_input_tokens=prompt_context.quality_retry_fixed_input_tokens,
        retry_same_as_source=prompt_context.retry_same_as_source,
        original_total=total,
        remaining_items=list(batch_items),
        all_results=[],
        event_callback=event_callback,
        conversation_history=prompt_context.conversation_history,
        session_state=next(
            (
                item.get("_translation_session")
                for item in batch_items
                if isinstance(item.get("_translation_session"), TranslationSessionState)
            ),
            None,
        ),
        rpm_cooldown_sec=rpm_cooldown,
        key_rotation_buffer_sec=_hidden_wait_sec(
            lm_cfg, "key_rotation_buffer_sec", KEY_ROTATION_BUFFER_SEC
        ),
        overload_retry_sec=_hidden_wait_sec(
            lm_cfg, "overload_retry_sec", OVERLOAD_RETRY_WAIT_SEC
        ),
        request_interval_sec=_hidden_wait_sec(
            lm_cfg, "request_interval_sec", REQUEST_INTERVAL_SEC
        ),
        batch_interval_sec=_batch_interval_seconds(lm_cfg),
        reasoning_effort=reasoning_effort,
        chatgpt_profile_id=chatgpt_profile_id,
        remaining_item_ids=[str(index) for index in range(len(batch_items))],
        terminology_cache={
            id(item): tuple(
                terminology_terms_for_item(
                    item,
                    include_official=bool(lm_cfg.get("official_glossary", True)),
                    include_moddb_reviewed=bool(
                        lm_cfg.get("moddb_reviewed_glossary", True)
                    ),
                )
            )
            for item in batch_items
        },
    )


def _prepare_batch(runtime: _BatchRuntime) -> _BatchRound:
    """依實際批次術語提示與 Token 預算選取穩定前綴。"""
    if not runtime.remaining_items:
        raise ValueError("沒有可準備的翻譯項目")
    scope_count = min(max(1, runtime.batch_size), len(runtime.remaining_items))
    fit_count = scope_count
    # 提示詞本身會隨批次術語數量變化；每輪都用同一批實際項目與穩定 id
    # 重建提示詞，最多收斂數輪，避免沿用全輸入批次造成 Token 高估或錯配。
    for _ in range(6):
        scope_items = runtime.remaining_items[:scope_count]
        scope_ids = runtime.remaining_item_ids[:scope_count]
        _refresh_runtime_prompt_budget(
            runtime, prompt_items=scope_items, prompt_ids=scope_ids
        )
        fit_count = max(
            1,
            min(
                scope_count,
                select_batch_size(
                    runtime.remaining_items,
                    runtime.batch_profile,
                    scope_count,
                    runtime.lm_cfg,
                    fixed_input_tokens=runtime.batch_fit_input_tokens,
                ),
            ),
        )
        if fit_count == scope_count:
            break
        scope_count = fit_count
    current_batch = runtime.remaining_items[:fit_count]
    current_ids = runtime.remaining_item_ids[:fit_count]
    _refresh_runtime_prompt_budget(
        runtime, prompt_items=current_batch, prompt_ids=current_ids
    )
    return _BatchRound(
        current_batch=current_batch,
        fit_count=fit_count,
        estimate=runtime.budget_tracker.estimate(
            current_batch, runtime.budget_cfg, runtime.fixed_input_tokens
        ),
        payload=build_translation_payload(
            current_batch,
            item_ids=current_ids,
        ),
        id_to_item=dict(zip(current_ids, current_batch, strict=True)),
    )


def _is_explicit_quota_exhaustion(quota_id: str, remote_message: str) -> bool:
    return bool(quota_id) or any(
        marker in remote_message for marker in _EXPLICIT_QUOTA_MESSAGE_MARKERS
    )


def _parse_rate_limit_error(error: Exception) -> tuple[str, float, str]:
    """從 429 錯誤解析配額種類、重試延遲秒數（可含小數，無則 0）與已遮罩訊息。

    配額種類為 ``rpd``／``rpm``／``quota``；只有明確辨識為配額耗盡時才回傳 ``quota``。
    合法 JSON 但沒有 RPD／RPM／明確配額耗盡訊號，或回應 body 不是可解析的 Google error JSON
    時回傳 ``unparsed``：它仍是 HTTP 429，只能保守視為速率限制，不能當成需輪替 key 的配額耗盡。
    延遲來自 ``Retry-After`` header 或 ``RetryInfo``（見 ``retry_after.parse_retry_after``）。
    """
    response = getattr(error, "response", None)
    parsed_retry_after = parse_response_retry_after(response)
    # A provider-controlled delay must not be allowed to suspend the app for
    # days.  503 has a separate policy below; this bound is for 429 waits.
    retry_after = (
        parsed_retry_after
        if parsed_retry_after is not None
        and math.isfinite(parsed_retry_after)
        and 0 <= parsed_retry_after <= GEMINI_503_MAX_RETRY_AFTER_SEC
        else 0.0
    )
    try:
        error_json = response.json().get("error", {})
        if not isinstance(error_json, dict):
            raise TypeError("429 error body must be an object")
    except (AttributeError, TypeError, ValueError):
        return "unparsed", retry_after, redact_text(error).upper()
    remote_message = redact_text(error_json.get("message", "")).upper()
    quota_id = ""
    details = error_json.get("details", [])
    if not isinstance(details, list):
        details = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
            continue
        violations = detail.get("violations", [])
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if isinstance(violation, dict):
                quota_id = str(violation.get("quotaId", "")).upper()
                if quota_id:
                    break
        if quota_id:
            break
    if "PERDAY" in quota_id or "DAILY" in remote_message:
        return "rpd", retry_after, remote_message
    if "PERMINUTE" in quota_id or "RPM" in remote_message:
        return "rpm", retry_after, remote_message
    if _is_explicit_quota_exhaustion(quota_id, remote_message):
        return "quota", retry_after, remote_message
    return "unparsed", retry_after, remote_message


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


def gemini_503_wait_sec(retry_index: int, retry_after: float | None) -> float:
    """Gemini 503 暫時不可用的等待秒數：固定退避表與供應商 Retry-After 取較大者。

    LM 主流程與智慧校對共用；是否超過 ``GEMINI_503_MAX_RETRY_AFTER_SEC``、
    是否換模型或改回報 PARTIAL，仍由各流程自己決定。
    """
    base_delay = GEMINI_503_RETRY_DELAYS_SEC[retry_index]
    return base_delay if retry_after is None else max(base_delay, retry_after)


def _reset_gemini_503_retry_state(runtime: _BatchRuntime) -> None:
    runtime.gemini_503_retry_count = 0
    runtime.unparsed_rate_limit_count = 0
    runtime.gemini_503_attempted_models.clear()


def _reset_provider_retry_state_after_success(runtime: _BatchRuntime) -> None:
    if runtime.provider == "chatgpt":
        runtime.chatgpt_retry_count = 0
    elif runtime.provider == "gemini":
        _reset_gemini_503_retry_state(runtime)


def _defer_gemini_503_remaining_items(
    runtime: _BatchRuntime, reason: str
) -> BatchAction:
    """在 503 重試耗盡後保留本批次與後續項目為未翻譯。"""
    log_warning(reason)
    runtime.all_results.extend(
        {**item, "_untranslated": True} for item in runtime.remaining_items
    )
    runtime.remaining_items.clear()
    runtime.pinned_model_index = None
    return BatchAction.PARTIAL


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
    """判斷機器可讀的 API 代碼是否代表永久配額耗盡。"""
    code = str(error.code or "").strip().lower()
    error_type = str(error.error_type or "").strip().lower()
    return (
        code in CHATGPT_PERMANENT_QUOTA_ERROR_CODES
        or error_type in CHATGPT_PERMANENT_QUOTA_ERROR_CODES
    )


def _chatgpt_retryable_error(error) -> bool:
    """辨識暫時性 Responses 錯誤，且不重試計費失敗。"""
    if _is_chatgpt_quota_exhaustion(error):
        return False
    code = str(error.code or "").strip().lower()
    error_type = str(error.error_type or "").lower()
    if code in CHATGPT_TRANSIENT_USAGE_ERROR_CODES:
        return True
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


def _handle_chatgpt_api_error(
    runtime: _BatchRuntime,
    error,
    *,
    retry_event_callback=None,
) -> BatchAction:
    """對暫時性 ChatGPT 請求錯誤執行有界且可取消的退避。"""
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
            parsed_retry_after = float(retry_after)
            if not math.isfinite(parsed_retry_after) or parsed_retry_after < 0:
                raise ValueError("retry_after must be finite and non-negative")
            wait_sec = parsed_retry_after
        except (TypeError, ValueError, OverflowError):
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
    if retry_event_callback is not None:
        classification = classify_provider_error(error, error.status)
        retry_event_callback(
            {
                "kind": "retry_wait",
                "retry_number": runtime.chatgpt_retry_count,
                "retry_total": CHATGPT_MAX_RETRIES,
                "wait_ms": seconds_to_ms(wait_sec),
                "retry_after_ms": None
                if retry_after is None
                else seconds_to_ms(retry_after),
                "error_kind": "chatgpt_transient",
                "http_status": error.status,
                "overload": classification["overload"],
                "rate_limit": classification["rate_limit"],
                "timeout": classification["timeout"],
            }
        )
    wait_started = time.monotonic()
    log_warning(
        f"[ChatGPT] 暫時性錯誤，{wait_sec:g} 秒後重試 "
        f"({runtime.chatgpt_retry_count}/{CHATGPT_MAX_RETRIES})：{error}"
    )
    interruptible_sleep(wait_sec)
    if retry_event_callback is not None:
        retry_event_callback(
            {
                "kind": "retry_resume",
                "retry_number": runtime.chatgpt_retry_count,
                "retry_total": CHATGPT_MAX_RETRIES,
                "wait_ms": seconds_to_ms(wait_sec),
                "waited_ms": seconds_to_ms(time.monotonic() - wait_started),
            }
        )
    return BatchAction.RETRY_SAME_MODEL


def _handle_service_unavailable_error(
    runtime: _BatchRuntime,
    error: Exception,
    model_index: int,
    status: int | None,
    *,
    retry_event_callback=None,
) -> BatchAction:
    """處理 provider 暫時不可用錯誤，記錄分類、等待與是否繼續重試。"""
    response = error.response if isinstance(error, requests.HTTPError) else None
    error_json = {}
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
            _sleep_with_runtime_event(
                runtime,
                runtime.key_rotation_buffer_sec,
                reason="key_rotation",
                callback=retry_event_callback,
            )
            return BatchAction.ROTATE_KEY
        runtime.pinned_model_index = model_index
        _sleep_with_runtime_event(
            runtime,
            runtime.overload_retry_sec,
            reason="provider_overload",
            callback=retry_event_callback,
        )
        return decide_batch_action(
            "service_unavailable",
            quota_kind="overloaded",
            overload_count=overload_count,
            has_alternative_key=True,
        ).action

    high_demand_unavailable = (
        runtime.provider == "gemini"
        and status == 503
        and (
            str(remote_status).upper() == "UNAVAILABLE"
            or "high demand" in remote_message.lower()
        )
    )
    if high_demand_unavailable:
        runtime.key_cycle.clear_overload()
        retry_after = parse_response_retry_after(response)
        if retry_after is not None and retry_after > GEMINI_503_MAX_RETRY_AFTER_SEC:
            return _defer_gemini_503_remaining_items(
                runtime,
                "[Gemini 503] Retry-After 超過 120 秒上限；保留未翻譯項目並回報 PARTIAL",
            )
        if runtime.gemini_503_retry_count >= len(GEMINI_503_RETRY_DELAYS_SEC):
            return _defer_gemini_503_remaining_items(
                runtime,
                "[Gemini 503] 已完成 3 次重試仍不可用；保留未翻譯項目並回報 PARTIAL",
            )

        retry_index = runtime.gemini_503_retry_count
        runtime.gemini_503_retry_count += 1
        wait_sec = gemini_503_wait_sec(retry_index, retry_after)
        runtime.gemini_503_attempted_models.add(model_index)

        alternate_models = []
        if retry_index > 0:
            quota = get_model_quota_registry()
            alternate_models = [
                candidate
                for candidate in _usable_model_indices(runtime, quota)
                if candidate != model_index
                and candidate not in runtime.gemini_503_attempted_models
            ]
        if alternate_models:
            action = BatchAction.NEXT_MODEL
        else:
            runtime.pinned_model_index = model_index
            action = BatchAction.RETRY_SAME_MODEL

        log_warning(
            f"[Gemini 503] UNAVAILABLE 暫時高需求，{wait_sec:g} 秒後重試 "
            f"({runtime.gemini_503_retry_count}/{len(GEMINI_503_RETRY_DELAYS_SEC)})；"
            f"保留 batch={runtime.batch_size}"
        )
        _sleep_with_runtime_event(
            runtime,
            wait_sec,
            reason="provider_unavailable",
            callback=retry_event_callback,
        )
        return action

    if runtime.provider == "gemini":
        _reset_gemini_503_retry_state(runtime)
    runtime.pinned_model_index = None
    _sleep_with_runtime_event(
        runtime,
        runtime.request_interval_sec,
        reason="provider_unavailable",
        callback=retry_event_callback,
    )
    return decide_batch_action(
        "service_unavailable", has_next_model=model_index + 1 < len(runtime.model_pool)
    ).action


def _handle_unparsed_rate_limit(
    runtime: _BatchRuntime,
    retry_after: float,
    remote_message: str,
    retry_event_callback,
) -> BatchAction:
    """429 回應無法解析時的保守處理：先等待並重試同一把 key，連續失敗才輪替。"""
    if runtime.unparsed_rate_limit_count >= UNPARSED_RATE_LIMIT_MAX_RETRIES:
        runtime.unparsed_rate_limit_count = 0
        runtime.retry_same_key = False
        log_warning(
            f"[❓] 429 回應無法解析且重試 {UNPARSED_RATE_LIMIT_MAX_RETRIES} 次仍受限"
            f" ({remote_message or 'unknown'})，嘗試切換 Key"
        )
        if not runtime.key_cycle.mark_failed():
            return BatchAction.EXHAUSTED
        return BatchAction.ROTATE_KEY
    runtime.unparsed_rate_limit_count += 1
    runtime.retry_same_key = True
    wait_time = retry_after or RATE_LIMIT_DEFAULT_WAIT_SEC
    log_info(f"[⏳] 429 回應無法解析，視為頻率限制：等待 {wait_time:g} 秒")
    _sleep_with_runtime_event(
        runtime,
        wait_time,
        reason="rate_limit_unparsed",
        callback=retry_event_callback,
        kind="rate_limit_wait",
    )
    return BatchAction.RETRY_SAME_KEY


def _handle_batch_error(
    runtime: _BatchRuntime,
    error: Exception,
    model_index: int,
    *,
    model_output_cap: int | None = None,
    cap_source: str = "global",
    retry_event_callback=None,
) -> BatchAction:
    """分類單次模型失敗並執行狀態化的金鑰與等待處理。"""
    from translation_tool.core.codex_oauth import ChatGPTOAuthError
    from translation_tool.core.openai_codex_client import ChatGPTAPIError

    if isinstance(error, ChatGPTOAuthError):
        raise ChatGPTOAuthError(f"ChatGPT OAuth 登入需要處理：{error}") from error
    if isinstance(error, ChatGPTAPIError):
        return _handle_chatgpt_api_error(
            runtime, error, retry_event_callback=retry_event_callback
        )

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
        if not _payload_error_allows_shrink(error, status):
            detail = _remote_error_detail(error)
            if detail:
                log_info(f"[⚠️] 伺服器回應：{detail}")
            raise RuntimeError(
                "❌ provider 拒絕翻譯請求；回應沒有指出 payload/context 過大，"
                "不會盲目縮小批次。請檢查模型、schema 與請求設定。"
            ) from error
        log_info("[⚠️] 400 INVALID_ARGUMENT：payload 過大，縮小 batch")
        detail = _remote_error_detail(error)
        if detail:
            log_info(f"[⚠️] 伺服器回應：{detail}")
        action = decide_batch_action(error_kind).action
        if runtime.provider == "gemini" and action is BatchAction.SHRINK_BATCH:
            _reset_gemini_503_retry_state(runtime)
        return action

    if error_kind == "rate_limited":
        try:
            quota_kind, retry_after, remote_message = _parse_rate_limit_error(error)
        except Exception as parse_error:  # noqa: BLE001
            remote_message = redact_text(error).upper()
            quota_kind = "unparsed"
            retry_after = 0.0
            log_error(f"[⚠️] 無法解析 429 回應，使用備援：{redact_text(parse_error)}")
        if quota_kind == "unparsed":
            return _handle_unparsed_rate_limit(
                runtime, retry_after, remote_message, retry_event_callback
            )

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
            wait_time = retry_after or RATE_LIMIT_DEFAULT_WAIT_SEC
            log_info(f"[⏳] 每分鐘頻率限制 (RPM)：等待 {wait_time:g} 秒")
            _sleep_with_runtime_event(
                runtime,
                wait_time,
                reason=f"rate_limit_{quota_kind}",
                callback=retry_event_callback,
                kind="rate_limit_wait",
            )
            return BatchAction.RETRY_SAME_KEY
        log_warning(
            f"[❓] 偵測到 429 限制 ({remote_message or 'unknown'}), 嘗試切換 Key"
        )
        if not runtime.key_cycle.mark_failed():
            return BatchAction.EXHAUSTED
        return BatchAction.ROTATE_KEY

    if error_kind == "service_unavailable":
        return _handle_service_unavailable_error(
            runtime,
            error,
            model_index,
            status,
            retry_event_callback=retry_event_callback,
        )

    if error_kind in {"deadline_exceeded", "server_error", "timeout"}:
        if runtime.provider == "gemini":
            _reset_gemini_503_retry_state(runtime)
        log_info(f"[{error_kind}] 請求失敗，縮小 batch")
        return decide_batch_action(error_kind).action

    log_info(f"[!] 未分類錯誤: {redact_text(error)}")
    return BatchAction.FAIL


def _is_moddb_batch(items: list[dict]) -> bool:
    """判斷批次項目是否帶有 ModDB 批次群組標記。"""
    return any("_batch_group" in item for item in items)


_CONTRACT_TYPE_LABELS = {
    "duplicate_id": "重複 id：模型對同一個 id 回傳了兩次",
    "id_mismatch": "id 缺漏或多出：回傳的 id 與送出的不一致",
    "invalid_shape": "回傳結構無效：不是約定的 items/id/value 格式",
}


def _contract_error_type(detail: str) -> str:
    if detail.startswith("duplicate id"):
        return "duplicate_id"
    if detail.startswith("missing IDs"):
        return "id_mismatch"
    return "invalid_shape"


def _note_contract_error(
    runtime: _BatchRuntime, round_data: _BatchRound, detail: str
) -> None:
    """記錄 ModDB 回應契約違反的後端結構化資訊。"""
    if not _is_moddb_batch(list(round_data.id_to_item.values())):
        return
    size = len(round_data.id_to_item)
    runtime.pending_contract_event = {"batch_size": size}
    log_warning(
        f"[ResponseContractError 回應格式錯誤] 批次 {size} 筆｜類型："
        f"{_CONTRACT_TYPE_LABELS[_contract_error_type(detail)]}｜細節：{detail}｜"
        "處理：縮小批次後重送（不算品質重試）",
        extra={"ui_mirrored": True},
    )


def _truncation_fallback_reason(
    runtime: _BatchRuntime, round_data: _BatchRound, finish_reason: str | None
) -> str:
    """JSON 不完整時的回填原因：只有供應商回報 MAX_TOKENS 才算 token 不足。

    模型正常結束（STOP）或沒有 finish_reason，JSON 卻不完整，屬於回應格式問題，
    記為契約錯誤，後台統計才不會把它誤報成 token 不足。
    """
    if finish_reason == "MAX_TOKENS":
        return "max_tokens"
    if _is_moddb_batch(list(round_data.id_to_item.values())):
        detail = f"incomplete JSON (finish_reason={finish_reason})"
        contract_errors = getattr(runtime, "contract_errors", None)
        if isinstance(contract_errors, list):
            contract_errors.append(detail)
        _note_contract_error(runtime, round_data, detail)
    return "response_contract"


def _note_contract_progress(runtime: _BatchRuntime, basis: int, new_size: int) -> None:
    """記錄契約失敗如何被處理：縮小批次或保留來源。"""
    if getattr(runtime, "pending_contract_event", None) is None:
        return
    if new_size <= 0 or new_size == basis:
        runtime.pending_contract_event = None
        log_warning(
            f"[ResponseContractError 回應格式錯誤] 批次 {basis} 筆已縮到極限仍失敗｜"
            "結果：回填原文、不寫入快取與資料庫",
            extra={"ui_mirrored": True},
        )
        return
    log_warning(
        f"[ResponseContractError 回應格式錯誤] 縮小批次重送：{basis} 筆 → {new_size} 筆",
        extra={"ui_mirrored": True},
    )


def _note_contract_resolved(runtime: _BatchRuntime, round_data: _BatchRound) -> None:
    """記錄同一 ModDB 批次後續回應已通過驗證。"""
    event = getattr(runtime, "pending_contract_event", None)
    if event is None:
        return
    runtime.pending_contract_event = None
    log_info(
        f"[ResponseContractError 回應格式錯誤] 已排除｜原批次 {event['batch_size']} 筆，"
        f"成功批次 {len(round_data.id_to_item)} 筆",
        extra={"ui_mirrored": True},
    )


def _merge_batch_response(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    raw_text: str,
    api_meta: dict,
    *,
    optional_retry: bool = False,
) -> tuple[list[dict] | None, bool]:
    """解析、驗證並恢復一次成功回應中的翻譯順序。"""
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
            contract_errors = getattr(runtime, "contract_errors", None)
            if isinstance(contract_errors, list):
                contract_errors.append(contract_error)
            _note_contract_error(runtime, round_data, contract_error)
            log_warning(
                f"[❌ 回應契約不符] {contract_error}；本批次將重試/縮小",
                extra=(
                    {"ui_mirrored": True}
                    if _is_moddb_batch(list(round_data.id_to_item.values()))
                    else None
                ),
            )
            runtime.budget_tracker.on_truncated(
                api_meta.get("finish_reason"), kind="missing"
            )
        return None, True

    _note_contract_resolved(runtime, round_data)
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


def _retry_error_detail(exc: Exception) -> str:
    """重試失敗的例外摘要（類型與經遮蔽的訊息），寫入後台 log 方便追查原因。"""
    return f"{type(exc).__name__}: {redact_text(exc)[:300]}"


def _record_quality_retry_failure(
    runtime: _BatchRuntime,
    error: Exception,
    model_index: int,
    model_name: str,
) -> None:
    """更新品質訊號，但不啟動一般重試狀態機。"""
    from translation_tool.core.openai_codex_client import ChatGPTAPIError

    quota = get_model_quota_registry()
    try:
        if isinstance(error, ChatGPTAPIError):
            code = str(error.code or "").lower()
            error_type = str(error.error_type or "").lower()
            is_rate_limited = (
                error.status == 429
                or code in {"rate_limit_exceeded", "rate_limit_error", "slow_down"}
                or error_type == "rate_limit_error"
            )
            if is_rate_limited and not _is_chatgpt_quota_exhaustion(error):
                try:
                    retry_after = float(error.retry_after or 0.0)
                    if not math.isfinite(retry_after) or retry_after < 0:
                        raise ValueError("retry_after must be finite and non-negative")
                except (TypeError, ValueError, OverflowError):
                    retry_after = 0.0
                retry_after = min(retry_after, CHATGPT_MAX_RETRY_AFTER_SEC)
                if retry_after <= 0:
                    retry_after = max(1.0, runtime.batch_interval_sec)
                quota.defer_request_slot(runtime.provider, model_name, retry_after)
                log_warning(
                    f"[品質重試] ChatGPT RPM 限制，已將共用 scheduler 延後 {retry_after:g} 秒"
                )
            return
        status = (
            error.response.status_code
            if isinstance(error, requests.HTTPError) and error.response is not None
            else None
        )
        error_kind = _classify_batch_error(error, status)
        if error_kind == "model_missing":
            runtime.missing_models.add(model_index)
            return
        if error_kind == "key_forbidden":
            runtime.key_cycle.mark_failed(reason="forbidden")
            return
        if error_kind == "rate_limited":
            try:
                quota_kind, retry_after, remote_message = _parse_rate_limit_error(error)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                quota_kind, retry_after = "unparsed", 0.0
                remote_message = redact_text(error).upper()
            if quota_kind == "rpd":
                quota.mark_exhausted(model_name)
            elif quota_kind in {"rpm", "unparsed"}:
                retry_after_value = retry_after
                if retry_after_value <= 0:
                    retry_after_value = max(1.0, runtime.batch_interval_sec)
                quota.defer_request_slot(
                    runtime.provider, model_name, retry_after_value
                )
                log_warning(
                    f"[品質重試] RPM 限制（{remote_message or 'unknown'}），"
                    f"共用 scheduler 延後 {retry_after_value:g} 秒"
                )
            else:
                runtime.key_cycle.mark_failed()
            return
        if error_kind == "service_unavailable" and runtime.provider == "gemini":
            runtime.key_cycle.record_overload()
    finally:
        # A failed optional retry must not leave a probe lease alive for a request
        # that will never be retried in this path.
        quota.release(model_name, runtime.quota_owner)


def _retry_same_source_translations(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    result: list[dict],
    *,
    prompt: str,
    model_name: str,
    api_key: str,
    output_cap: int | None,
    conversation_history: list[dict[str, str]],
    retry_budget: _BatchRetryBudget,
) -> list[dict]:
    """只重新確認與來源相同的候選，失敗時保留第一個有效結果。"""
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
    if retry_budget.used:
        log_info(
            "[翻譯重試] 本批次已用完重試額度（每批最多 1 次），略過相同譯文的重新確認"
        )
        return result
    retry_budget.used = True

    retry_prompt = f"{prompt.rstrip()}\n\n{SAME_SOURCE_RETRY_INSTRUCTION}"
    retry_estimate = runtime.budget_tracker.estimate(
        candidates.items,
        runtime.budget_cfg,
        runtime.retry_fixed_input_tokens
        + _estimate_conversation_history_tokens(conversation_history),
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
            conversation_history=conversation_history,
        )
        _wait_for_request_slot(runtime, model_name)
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
        conversation_history.extend(
            [
                {
                    "role": "user",
                    "text": json.dumps(retry_round.payload, ensure_ascii=False),
                },
                {"role": "model", "text": retry_text},
            ]
        )
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


def _mark_quality_retry_result(
    result: list[dict],
    candidate_positions: tuple[int, ...],
    *,
    fixed_positions: tuple[int, ...] = (),
    unresolved_positions: tuple[int, ...] | None = None,
    quota_failed: bool = False,
) -> list[dict]:
    """附加單批次品質重試統計，不改變輸出文字。"""
    candidates = set(candidate_positions)
    fixed = set(fixed_positions)
    unresolved = (
        set(unresolved_positions)
        if unresolved_positions is not None
        else candidates - fixed
    )
    marked: list[dict] = []
    for position, item in enumerate(result):
        if not isinstance(item, dict) or position not in candidates:
            marked.append(item)
            continue
        # Keep the public generic translator result unchanged. Mod DB items
        # opt in through their explicit batch-group metadata.
        if "_batch_group" not in item:
            marked.append(item)
            continue
        updated = {**item, "_quality_retry_candidate": True}
        if position in fixed:
            updated["_quality_retry_fixed"] = True
        if position in unresolved:
            updated["_quality_retry_unresolved"] = True
        if quota_failed:
            updated["_quality_retry_quota_failed"] = True
        marked.append(updated)
    return marked


def _is_quality_retry_quota_failure(error: Exception) -> bool:
    """辨識配額或速率限制失敗，供結構化重試統計使用。"""
    status = getattr(error, "status", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )
    if status == 429:
        return True
    text = str(error).lower()
    return any(token in text for token in ("quota", "rate limit", "rate_limit"))


def _retry_quality_translations(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    result: list[dict],
    *,
    prompt: str,
    model_name: str,
    api_key: str,
    output_cap: int | None,
    conversation_history: list[dict[str, str]],
) -> list[dict]:
    """以單批次請求重試所有品質問題翻譯並保留最終結果。"""
    candidates = build_quality_retry_batch(
        round_data.current_batch,
        result,
        list(round_data.id_to_item),
        retry_same_as_source=runtime.retry_same_as_source,
        include_official=bool(runtime.lm_cfg.get("official_glossary", True)),
        include_moddb_reviewed=bool(
            runtime.lm_cfg.get("moddb_reviewed_glossary", True)
        ),
        terminology_cache=runtime.terminology_cache,
    )
    if not candidates.positions:
        return result
    candidate_positions = tuple(candidates.positions)

    retry_prompt = f"{prompt.rstrip()}\n\n{QUALITY_RETRY_INSTRUCTION}"
    retry_estimate = runtime.budget_tracker.estimate(
        candidates.items,
        runtime.budget_cfg,
        runtime.quality_retry_fixed_input_tokens
        + _estimate_conversation_history_tokens(
            conversation_history, estimator=runtime.budget_cfg.token_estimator
        ),
    )
    retry_input_tokens = _estimate_retry_input_tokens(
        retry_prompt,
        candidates.payload,
        conversation_history,
        estimator=runtime.budget_cfg.token_estimator,
    )
    if runtime.budget_cfg.enabled and (
        retry_input_tokens > runtime.budget_cfg.max_input_token_budget
    ):
        log_warning(
            "[品質重試] 重試輸入超過 token 預算，略過重試，"
            f"{len(candidates.positions)} 筆保留第一次譯文"
            f"（估計 {round(retry_input_tokens)} tokens，預算 "
            f"{runtime.budget_cfg.max_input_token_budget} tokens）"
        )
        return _mark_quality_retry_result(
            result, candidate_positions, unresolved_positions=candidate_positions
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
        f"[品質重試] 送出 {candidate_count}/{len(round_data.current_batch)} 筆需要重新確認的譯文"
        "（與原文相同、格式或術語不一致），"
        f"估計輸入 {round(retry_input_tokens)} tokens"
    )

    quota = get_model_quota_registry()
    try:
        model_index = runtime.model_pool.index(model_name)
    except ValueError:
        log_warning(f"[品質重試] 找不到模型 {model_name} 的配額索引，保留第一次譯文")
        return _mark_quality_retry_result(
            result, candidate_positions, unresolved_positions=candidate_positions
        )
    lease_sec = _probe_lease_sec(runtime)
    if not quota.claim(model_name, runtime.quota_owner, lease_sec):
        log_warning(
            f"[品質重試] 模型 {model_name} 目前沒有可用的配額探測租約，"
            f"{candidate_count} 筆保留第一次譯文"
        )
        return _mark_quality_retry_result(
            result,
            candidate_positions,
            unresolved_positions=candidate_positions,
            quota_failed=True,
        )
    started = quota.now()

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
            conversation_history=conversation_history,
        )
        with quota.hold_probe_lease(model_name, runtime.quota_owner, lease_sec):
            _wait_for_request_slot(runtime, model_name)
            retry_text = call_gemini_requests(**request_kwargs).strip()
        raise_if_cancelled()
        quota.mark_ok(model_name, started_at=started)
        if (
            not retry_text
            or retry_meta.get("finish_reason") == "MAX_TOKENS"
            or _is_truncated_response(retry_text)
        ):
            log_warning(
                "[品質重試] 重試回應為空或被截斷"
                f"（finish_reason={retry_meta.get('finish_reason')}），"
                f"{candidate_count} 筆保留第一次譯文"
            )
            return _mark_quality_retry_result(
                result, candidate_positions, unresolved_positions=candidate_positions
            )
        retried, invalid = _merge_batch_response(
            runtime, retry_round, retry_text, retry_meta, optional_retry=True
        )
        if invalid or retried is None:
            log_warning(
                "[品質重試] 重試回應不符合結構化格式（缺少項目、ID 不一致，或譯文不是字串），"
                f"{candidate_count} 筆保留第一次譯文"
            )
            return _mark_quality_retry_result(
                result, candidate_positions, unresolved_positions=candidate_positions
            )
        conversation_history.extend(
            [
                {
                    "role": "user",
                    "text": json.dumps(retry_round.payload, ensure_ascii=False),
                },
                {"role": "model", "text": retry_text},
            ]
        )
    except Exception as exc:  # noqa: BLE001 - optional quality retry must not fail a valid batch
        _clear_quota_on_http_success(quota, exc, model_name, started)
        try:
            _record_quality_retry_failure(runtime, exc, model_index, model_name)
        except Exception as health_error:  # noqa: BLE001 - health is best effort
            log_warning(
                f"[品質重試] 更新模型健康狀態失敗，仍保留第一次譯文：{_retry_error_detail(health_error)}"
            )
        log_warning(
            f"[品質重試] 重試請求失敗，{candidate_count} 筆保留第一次譯文"
            f"（{_retry_error_detail(exc)}）"
        )
        return _mark_quality_retry_result(
            result,
            candidate_positions,
            unresolved_positions=candidate_positions,
            quota_failed=_is_quality_retry_quota_failure(exc),
        )

    accepted_positions: list[int] = []
    accepted_results: list[dict] = []
    unresolved = 0
    initially_same_source_ids = {
        str(item.get("id"))
        for item in candidates.payload.get("items", [])
        if "翻譯結果與原文相同" in (item.get("retry_reasons") or [])
    }
    initially_same_source_positions = {
        position
        for position, item_id in zip(
            candidates.positions, candidates.item_ids, strict=True
        )
        if str(item_id) in initially_same_source_ids
    }
    for position, retry_item in zip(candidates.positions, retried, strict=True):
        source_text = round_data.current_batch[position].get("text")
        translated_text = retry_item.get("text")
        still_suspect_same_source = (
            position not in initially_same_source_positions
            and isinstance(source_text, str)
            and translated_text == source_text
            and any(char.isalpha() for char in source_text)
        )
        if (
            not isinstance(translated_text, str)
            or not translated_text.strip()
            or not isinstance(source_text, str)
            or still_suspect_same_source
            or quality_issues(round_data.current_batch[position], translated_text)
            or terminology_issues(
                round_data.current_batch[position],
                translated_text,
                include_official=bool(runtime.lm_cfg.get("official_glossary", True)),
                include_moddb_reviewed=bool(
                    runtime.lm_cfg.get("moddb_reviewed_glossary", True)
                ),
                terms=runtime.terminology_cache.get(
                    id(round_data.current_batch[position])
                ),
            )
        ):
            unresolved += 1
            continue
        accepted_positions.append(position)
        accepted_results.append(retry_item)

    if unresolved:
        log_warning(
            f"[品質重試] 重試後仍有 {unresolved}/{candidate_count} 筆品質問題"
            "（格式、術語不一致或譯文為空），這些筆保留第一次譯文"
        )

    merged = merge_same_source_retry_results(
        result, tuple(accepted_positions), accepted_results
    )
    log_info(
        f"[品質重試] 已修正 {len(accepted_positions)}/{candidate_count} 筆需要重新確認的譯文"
    )
    unresolved_positions = tuple(
        position
        for position in candidate_positions
        if position not in accepted_positions
    )
    return _mark_quality_retry_result(
        merged,
        candidate_positions,
        fixed_positions=tuple(accepted_positions),
        unresolved_positions=unresolved_positions,
    )


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
    default_timeout = float(get_schema_default("lm_translator.rate_limit.timeout"))
    try:
        timeout = float(
            (runtime.lm_cfg.get("rate_limit") or {}).get("timeout", default_timeout)
        )
    except (TypeError, ValueError):
        timeout = default_timeout
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
    initial_response_text: str,
) -> _BatchRoundOutcome:
    """確認可選品質候選後，只前進一次已驗證的批次。"""
    conversation_history = [
        *runtime.conversation_history,
        {
            "role": "user",
            "text": json.dumps(round_data.payload, ensure_ascii=False),
        },
        {"role": "model", "text": initial_response_text},
    ]
    finalized = _retry_quality_translations(
        runtime,
        round_data,
        result,
        prompt=prompt,
        model_name=model_name,
        api_key=api_key,
        output_cap=output_cap,
        conversation_history=conversation_history,
    )
    if runtime.session_state is not None:
        runtime.session_state.replace(conversation_history)
        runtime.conversation_history = runtime.session_state.snapshot()
    else:
        runtime.conversation_history = conversation_history
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
    runtime.remaining_item_ids = runtime.remaining_item_ids[
        len(round_data.current_batch) :
    ]
    runtime.batch_size = min(runtime.batch_size, len(runtime.remaining_items))
    runtime.key_cycle.record_success()
    runtime.pinned_model_index = None
    if not runtime.remaining_items and runtime.rpm_cooldown_sec > 0:
        interruptible_sleep(runtime.rpm_cooldown_sec)
    return _BatchRoundOutcome()


def _provider_request_kwargs(
    runtime,
    model_name,
    prompt,
    payload,
    output_cap,
    meta_out,
    api_key,
    *,
    conversation_history=None,
    network_event_callback=None,
):
    """建立單次 provider 請求共用與 ChatGPT 專用的參數。"""
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
        "fast": bool(runtime.lm_cfg.get("fast", False)),
    }
    if conversation_history:
        kwargs["conversation_history"] = list(conversation_history)
    if network_event_callback is not None:
        kwargs["network_event_callback"] = network_event_callback
    if kwargs["provider"] == "chatgpt":
        kwargs["reasoning_effort"] = runtime.reasoning_effort
        profile_id = getattr(runtime, "chatgpt_profile_id", None)
        if profile_id:
            kwargs["profile_id"] = profile_id
    return kwargs


def _perform_provider_request(
    runtime: _BatchRuntime,
    round_data: _BatchRound,
    model_name: str,
    quota,
    owner,
    lease_sec: float,
):
    """執行一次 provider 請求，並發布底層 network／provider attempt telemetry。"""
    prompt = runtime.effective_prompt
    model_override = model_output_token_cap(runtime.lm_cfg, model_name)
    cap_source = "per_model" if model_override is not None else "global"
    output_cap = (
        model_override
        if model_override is not None
        else runtime.budget_cfg.max_output_tokens
    )
    api_meta: dict = {}
    started = quota.now()
    try:
        log_info(
            f"[→] 送出至模型 {model_name}｜"
            f"本批 {len(round_data.current_batch)} 個（每批上限 {runtime.batch_size}）｜"
            f"本次唯一 AI 翻譯項目共 {runtime.original_total} 個"
        )
        with quota.hold_probe_lease(model_name, owner, lease_sec):
            runtime.provider_attempts += 1
            _emit_runtime_event(
                runtime.event_callback,
                "provider_attempt_start",
                provider_attempt=runtime.provider_attempts,
                provider_model=model_name,
                expected_count=len(round_data.current_batch),
            )
            if runtime.provider == "gemini":
                reuse_current_key = runtime.retry_same_key
                runtime.retry_same_key = False
                api_key = (
                    runtime.key_cycle.reuse_current()
                    if reuse_current_key
                    else runtime.key_cycle.claim()
                )
            else:
                api_key = ""
            request_kwargs = _provider_request_kwargs(
                runtime,
                model_name,
                prompt,
                round_data.payload,
                output_cap,
                api_meta,
                api_key,
                conversation_history=runtime.conversation_history,
                network_event_callback=runtime.event_callback,
            )
            _wait_for_request_slot(
                runtime,
                model_name,
                wait_event_callback=runtime.event_callback,
            )
            raw_text = call_gemini_requests(**request_kwargs).strip()
        raise_if_cancelled()
        _reset_provider_retry_state_after_success(runtime)
        quota.mark_ok(model_name, started_at=started)
        return raw_text, api_meta, api_key, output_cap, cap_source
    except Exception as error:
        _clear_quota_on_http_success(quota, error, model_name, started)
        status = (
            error.response.status_code
            if isinstance(error, requests.HTTPError) and error.response is not None
            else None
        )
        _emit_runtime_event(
            runtime.event_callback,
            "provider_attempt_failure",
            provider_attempt=runtime.provider_attempts,
            provider_model=model_name,
            **classify_provider_error(error, status),
        )
        raise


def _attempt_batch(
    runtime: _BatchRuntime, round_data: _BatchRound
) -> _BatchRoundOutcome:
    """嘗試模型池並回傳外層狀態機要執行的明確動作。"""
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
        prompt = runtime.effective_prompt
        model_override = model_output_token_cap(runtime.lm_cfg, model_name)
        cap_source = "per_model" if model_override is not None else "global"
        output_cap = (
            model_override
            if model_override is not None
            else runtime.budget_cfg.max_output_tokens
        )
        set_active_model(model_name)  # UI 狀態列顯示實際使用中的模型
        try:
            raw_text, api_meta, api_key, output_cap, cap_source = (
                _perform_provider_request(
                    runtime, round_data, model_name, quota, owner, lease_sec
                )
            )
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
                return _BatchRoundOutcome(
                    BatchAction.SHRINK_BATCH,
                    learned_budget=True,
                    fallback_reason=_truncation_fallback_reason(
                        runtime, round_data, finish_reason
                    ),
                )

            result, missing = _merge_batch_response(
                runtime, round_data, raw_text, api_meta
            )
            _emit_runtime_event(
                runtime.event_callback,
                "provider_attempt_success",
                provider_attempt=runtime.provider_attempts,
                provider_model=model_name,
                http_status=api_meta.get("http_status", 200),
                finish_reason=api_meta.get("finish_reason"),
                expected_count=len(round_data.current_batch),
                actual_count=len(result or []),
                prompt_tokens=api_meta.get("prompt_tokens"),
                output_tokens=api_meta.get("candidates_tokens"),
            )
            if missing:
                return _BatchRoundOutcome(
                    BatchAction.SHRINK_BATCH,
                    learned_budget=True,
                    fallback_reason="response_contract",
                )
            return _finish_successful_batch(
                runtime,
                round_data,
                result or [],
                api_meta,
                prompt=prompt,
                model_name=model_name,
                api_key=api_key,
                output_cap=output_cap,
                initial_response_text=raw_text,
            )
        except Exception as error:  # noqa: BLE001
            action = _handle_batch_error(
                runtime,
                error,
                model_index,
                model_output_cap=output_cap,
                cap_source=cap_source,
                retry_event_callback=runtime.event_callback,
            )
            if action is BatchAction.ROTATE_KEY:
                _emit_runtime_event(
                    runtime.event_callback,
                    "key_rotation",
                    provider_attempt=runtime.provider_attempts,
                    reason="retry_policy",
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


def _batch_shrink_factor(lm_cfg: dict) -> float:
    """讀取縮批比例：缺漏用 schema 預設；明確設定但不在 ``0 < factor < 1`` 時丟 RuntimeError。

    不使用 ``get(key, default) or default`` 這類會把顯式 0 吞成預設值的寫法，
    也不讓非法值默默擴大批次或把整批回填原文。
    """
    raw = lm_cfg.get("batch_shrink_factor")
    if raw is None:
        return float(get_schema_default("lm_translator.batch_shrink_factor"))
    try:
        return validate_batch_shrink_factor(raw)
    except ValueError as error:
        raise RuntimeError(f"❌ {error}") from error


def _apply_batch_shrink(
    runtime: _BatchRuntime, round_data: _BatchRound, outcome: _BatchRoundOutcome
) -> None:
    """套用學習到的批次縮小值並保持項目與識別碼狀態一致。"""
    if outcome.learned_budget:
        next_fit = select_batch_size(
            runtime.remaining_items,
            runtime.batch_profile,
            runtime.batch_size,
            runtime.lm_cfg,
            fixed_input_tokens=runtime.batch_fit_input_tokens,
        )
        if next_fit < len(round_data.current_batch):
            _note_contract_progress(runtime, len(round_data.current_batch), next_fit)
            return

    shrink_factor = _batch_shrink_factor(runtime.lm_cfg)
    basis = min(runtime.batch_size, len(round_data.current_batch))
    new_size = int(basis * shrink_factor)
    min_size = int(
        runtime.lm_cfg.get("min_batch_size")
        or get_schema_default("lm_translator.min_batch_size")
    )
    if runtime.batch_profile == "lang" and new_size < MIN_LANG_BATCH_SIZE:
        new_size = MIN_LANG_BATCH_SIZE if basis > MIN_LANG_BATCH_SIZE else 0
    elif new_size < min_size:
        new_size = min_size if basis > min_size else 0

    _note_contract_progress(runtime, basis, new_size)
    if new_size <= 0 or new_size == basis:
        log_warning(f"[⚠️] Batch Size 已縮至極限 ({basis})，回填原文並繼續後續項目")
        fallback_reason = outcome.fallback_reason or "batch_shrink"
        contract_error = (
            runtime.contract_errors[-1]
            if fallback_reason == "response_contract" and runtime.contract_errors
            else None
        )
        runtime.all_results.extend(
            {
                **item,
                "_untranslated": True,
                "_fallback_reason": fallback_reason,
                **({"_contract_error": contract_error} if contract_error else {}),
            }
            for item in round_data.current_batch
        )
        item_count = len(round_data.current_batch)
        runtime.remaining_items = runtime.remaining_items[item_count:]
        runtime.remaining_item_ids = runtime.remaining_item_ids[item_count:]
        runtime.batch_size = min(
            len(runtime.remaining_items),
            MIN_LANG_BATCH_SIZE if runtime.batch_profile == "lang" else min_size,
        )
        return
    log_info(f"[↓] 調整 Batch：{basis} → {new_size}")
    runtime.batch_size = new_size


def _run_batch_state_machine(
    batch_items: list[dict],
    total: int | None = None,
    dry_run: bool = False,
    *,
    event_callback=None,
):
    """協調批次準備、請求、錯誤處理與前綴前進。"""
    if dry_run:
        return [], "DRY_RUN"
    runtime = _build_batch_runtime(batch_items, total, event_callback=event_callback)
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
            _apply_batch_shrink(runtime, round_data, outcome)

        return runtime.all_results, "AUTO"
    finally:
        # 不論怎麼結束，都收回這次呼叫持有的探測租約（成功時紀錄已被清除，這裡不會有東西）。
        get_model_quota_registry().release_owner(runtime.quota_owner)


# 暫保舊名稱供外部整合程式相容；現役入口與內部呼叫已不再依賴 old 命名。
translate_batch_smart_old = _translate_batch_smart_impl
