"""智慧校對的 provider 呼叫、重試政策與用量計算。

本模組擁有對外部模型的所有呼叫邊界：API key 輪替、ChatGPT 設定、輸入／輸出
token 上限、暫時性錯誤的有限重試，以及 provider usage 的累計。呼叫哪個模型、
送什麼 payload 由 ``smart_review_run`` 決定。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

import requests

from translation_tool.core.codex_oauth import chatgpt_account_status
from translation_tool.core.lm_api_client import call_gemini_requests
from translation_tool.core.lm_batch_budget import BudgetConfig, estimate_text_tokens
from translation_tool.core.lm_config_rules import ApiKeyCycle, get_api_key_count
from translation_tool.core.lm_config_schema import (
    chatgpt_model_input_token_budget,
    chatgpt_model_reasoning_effort,
)
from translation_tool.core.lm_key_health import get_model_quota_registry
from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.translation_db.smart_review_prompt import (
    SMART_REVIEW_SYSTEM_PROMPT,
)
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    interruptible_sleep,
)


def _next_smart_review_batch_size(size: int) -> int:
    """以有界減半方式取得下一個智慧校對批次大小。"""
    return max(1, (int(size) + 1) // 2)


def _smart_review_response_needs_shrink(raw: str, meta: dict[str, Any]) -> bool:
    """判斷空回應或 provider 截斷是否應該縮小下一次請求。"""
    if not isinstance(raw, str) or not raw.strip():
        return True
    finish_reason = str(
        meta.get("finish_reason") or meta.get("finishReason") or ""
    ).lower()
    return any(
        marker in finish_reason
        for marker in ("max_tokens", "length", "truncat", "incomplete")
    )


def _smart_review_output_token_cap(lm_config: dict[str, Any]) -> int:
    """讀取智慧校對輸出上限並限制在 provider 可接受的整數範圍。"""
    try:
        configured = int(lm_config.get("smart_review_max_output_tokens", 16_384))
    except (TypeError, ValueError):
        configured = 16_384
    return max(1, min(configured, 65_536))


@dataclass
class _SmartReviewRetryRuntime:
    """保存智慧校對重試所需的共用 provider 狀態。"""

    provider: str
    batch_interval_sec: float
    chatgpt_retry_count: int = 0
    gemini_transient_retry_count: int = 0


def _smart_review_input_budget(
    lm_config: dict[str, Any], provider: str, model_name: str
) -> int | None:
    """建立本次執行固定的輸入 token 預算快照。"""
    budget = BudgetConfig.from_config(lm_config)
    if not budget.enabled:
        return None
    limit = int(budget.max_input_token_budget)
    if provider == "chatgpt":
        model_limit = chatgpt_model_input_token_budget(lm_config, model_name)
        if model_limit is not None:
            limit = min(limit, int(model_limit))
    return max(1, limit)


def _smart_review_estimated_input_tokens(
    payload: dict[str, Any], response_schema: dict[str, Any]
) -> float:
    """使用翻譯主流程的本地 estimator 估算完整 provider 輸入。"""
    return estimate_text_tokens(
        SMART_REVIEW_SYSTEM_PROMPT
        + json.dumps(payload, ensure_ascii=False)
        + json.dumps(response_schema, ensure_ascii=False)
    )


def _smart_review_token_value(value: object) -> int | None:
    """將 provider usage 欄位轉成可累加的非負整數，未知時保留 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _smart_review_response_item_count(raw: object) -> int | None:
    """只讀回應的 items 數量，不把模型內容寫入診斷事件。"""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    items = parsed.get("items") if isinstance(parsed, dict) else None
    return len(items) if isinstance(items, list) else None


def _smart_review_error_code(error: Exception, status: int | None = None) -> str:
    """建立不含 provider 原文、金鑰或個資的候選錯誤代碼。"""
    from translation_tool.core.lm_translator_main import _classify_batch_error

    if isinstance(error, ChatGPTAPIError):
        code = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(error.code or "unknown"))[:80]
        return f"request_failed:chatgpt:{error.status or 'unknown'}:{code}"
    error_kind = _classify_batch_error(error, status)
    if status is not None:
        return f"request_failed:http_{int(status)}:{error_kind}"
    return f"request_failed:{type(error).__name__}"


def _smart_review_record_usage(
    metrics: dict[str, Any],
    meta: dict[str, Any],
    token_totals: dict[str, int | None],
) -> None:
    """記錄一次 API 嘗試的 provider usage，包含失敗或截斷回應。"""
    metrics["request_attempts"] = int(metrics.get("request_attempts", 0)) + 1
    prompt = _smart_review_token_value(meta.get("prompt_tokens"))
    output = _smart_review_token_value(
        meta.get("output_tokens") or meta.get("candidates_tokens")
    )
    if prompt is None:
        metrics["usage_missing_attempts"] = (
            int(metrics.get("usage_missing_attempts", 0)) + 1
        )
    else:
        token_totals["prompt_tokens"] = (
            token_totals.get("prompt_tokens") or 0
        ) + prompt
    if output is None:
        metrics["usage_missing_output_attempts"] = (
            int(metrics.get("usage_missing_output_attempts", 0)) + 1
        )
    else:
        token_totals["output_tokens"] = (
            token_totals.get("output_tokens") or 0
        ) + output


def _smart_review_sleep(
    seconds: float, cancel_check: Callable[[], bool] | None
) -> None:
    """以既有可取消 sleep 等待 provider 的重試延遲。"""
    if cancel_check is not None and cancel_check():
        raise TaskCancelled()
    context = cancel_scope(cancel_check) if cancel_check is not None else nullcontext()
    with context:
        interruptible_sleep(seconds)
    if cancel_check is not None and cancel_check():
        raise TaskCancelled()


def _smart_review_transient_retry(
    runtime: _SmartReviewRetryRuntime,
    model_name: str,
    error: Exception,
    status: int | None,
    *,
    cancel_check: Callable[[], bool] | None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    """依既有 Gemini 503 policy 對 500/502/503/504 做有限次重試。"""
    from translation_tool.core.lm_translator_main import (
        GEMINI_503_MAX_RETRY_AFTER_SEC,
        GEMINI_503_RETRY_DELAYS_SEC,
        _classify_batch_error,
        _parse_gemini_retry_after_seconds,
    )

    error_kind = _classify_batch_error(error, status)
    if isinstance(error, requests.Timeout):
        error_kind = "timeout"
    elif isinstance(error, requests.ConnectionError):
        error_kind = "connection_error"
    if error_kind not in {
        "server_error",
        "service_unavailable",
        "deadline_exceeded",
        "timeout",
        "connection_error",
    }:
        return False
    if runtime.gemini_transient_retry_count >= len(GEMINI_503_RETRY_DELAYS_SEC):
        return False
    response = getattr(error, "response", None)
    error_json: dict[str, Any] = {}
    try:
        value = response.json() if response is not None else {}
        error_json = value if isinstance(value, dict) else {}
    except (AttributeError, TypeError, ValueError):
        error_json = {}
    retry_after = _parse_gemini_retry_after_seconds(response, error_json)
    if retry_after is not None and retry_after > GEMINI_503_MAX_RETRY_AFTER_SEC:
        return False
    retry_index = runtime.gemini_transient_retry_count
    runtime.gemini_transient_retry_count += 1
    wait_sec = GEMINI_503_RETRY_DELAYS_SEC[retry_index]
    if retry_after is not None:
        wait_sec = max(wait_sec, retry_after)
    if event_callback is not None:
        event_callback(
            {
                "kind": "retry_wait",
                "retry_number": retry_index + 1,
                "retry_total": len(GEMINI_503_RETRY_DELAYS_SEC),
                "wait_seconds": wait_sec,
                "retry_after_seconds": retry_after,
                "error_kind": error_kind,
                "http_status": status,
                "exception_type": type(error).__name__,
                "provider_overload": bool(
                    status == 503 or error_kind == "service_unavailable"
                ),
                "quota_or_rate_limit": bool(status == 429),
                "timeout": bool(error_kind == "timeout"),
            }
        )
    wait_started = time.monotonic()
    quota = get_model_quota_registry()
    quota.defer_request_slot(runtime.provider, model_name, wait_sec)
    _smart_review_sleep(wait_sec, cancel_check)
    if event_callback is not None:
        event_callback(
            {
                "kind": "retry_resume",
                "retry_number": retry_index + 1,
                "retry_total": len(GEMINI_503_RETRY_DELAYS_SEC),
                "wait_seconds": wait_sec,
                "elapsed_seconds": time.monotonic() - wait_started,
            }
        )
    return True


@dataclass
class SmartReviewProviderSession:
    """保存一次智慧校對執行共用的 provider 身分、金鑰輪替與請求上限。"""

    provider: str
    model_name: str
    api_key: str
    lm_config: dict[str, Any]
    cancel_check: Callable[[], bool] | None
    key_cycle: ApiKeyCycle | None
    chatgpt_options: dict[str, Any] = field(default_factory=dict)
    output_token_cap: int = 0
    input_token_budget: int | None = None

    @classmethod
    def open(
        cls,
        *,
        provider: str,
        model_name: str,
        api_key: str,
        lm_config: dict[str, Any],
        cancel_check: Callable[[], bool] | None,
    ) -> SmartReviewProviderSession:
        """驗證 provider 憑證並解析 ChatGPT 專用選項。"""
        # service path 不預先選 key，仍由既有 ApiKeyCycle 負責 Gemini 輪替。
        key_cycle = (
            ApiKeyCycle() if provider == "gemini" and not api_key.strip() else None
        )
        if provider == "gemini" and not api_key.strip() and not get_api_key_count():
            raise ValueError("Gemini smart review 必須指定 API key")
        chatgpt_options: dict[str, Any] = {}
        if provider == "chatgpt":
            chatgpt_options["fast"] = bool(lm_config.get("fast", False))
            chatgpt_options["reasoning_effort"] = chatgpt_model_reasoning_effort(
                lm_config, model_name
            )
            profile_id = str(
                chatgpt_account_status().get("active_profile_id")
                or lm_config.get("chatgpt_model_profile_id")
                or ""
            ).strip()
            if profile_id:
                chatgpt_options["profile_id"] = profile_id
        return cls(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            lm_config=lm_config,
            cancel_check=cancel_check,
            key_cycle=key_cycle,
            chatgpt_options=chatgpt_options,
        )

    def configure_limits(self) -> None:
        """固定本次執行的輸出 token 上限與輸入 token 預算快照。"""
        self.output_token_cap = _smart_review_output_token_cap(self.lm_config)
        self.input_token_budget = _smart_review_input_budget(
            self.lm_config, self.provider, self.model_name
        )

    def claim_api_key(self) -> str:
        """取得本次請求要用的 API key；金鑰輪替耗盡時回傳空字串。"""
        if self.key_cycle is None:
            return self.api_key.strip()
        request_api_key = self.key_cycle.claim()
        if not request_api_key and not self.key_cycle.failed_indexes:
            request_api_key = self.api_key.strip()
        return request_api_key

    def call(
        self,
        *,
        system_prompt: str,
        payload: dict[str, Any],
        api_key: str,
        meta_out: dict[str, Any],
        response_schema: dict[str, Any],
        network_event_callback: Callable[[dict[str, object]], None],
    ) -> str:
        """送出一次 structured-output 請求並回傳原始回應文字。"""
        return call_gemini_requests(
            model_name=self.model_name,
            system_prompt=system_prompt,
            payload=payload,
            api_key=api_key,
            temperature=0.2,
            max_output_tokens=self.output_token_cap,
            meta_out=meta_out,
            provider=self.provider,
            lm_config=self.lm_config,
            response_schema=response_schema,
            network_event_callback=network_event_callback,
            **self.chatgpt_options,
        )
