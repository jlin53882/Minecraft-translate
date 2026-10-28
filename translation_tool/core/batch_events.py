"""批次事件的 versioned schema、計時 dataclass、舊欄位 adapter 與唯一的中文 formatter。

分層原則：

- core 只產生 canonical 事件：耗時一律是毫秒 ``*_ms``，嘗試序號是 ``attempt``，
  批次序號是 ``batch_index``，計數是 ``attempts``／``retry_count``／``network_attempts``。
- 需要相容舊讀者（既有 JSON 歷史、舊測試與外部腳本）時，只在「輸出邊界」呼叫
  :func:`to_legacy_event` 補舊欄位；core 內部不再讀寫舊欄位。
- 讀取舊事件（歷史資料）時先經過 :func:`canonicalize_event`，formatter 因此只需要認得 canonical 欄位。
- 畫面與 log 的事件文字只由 :func:`format_batch_event` 產生；耗時一律走
  ``batch_timing.format_duration_ms``，不印原始毫秒。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Final

from translation_tool.core.batch_timing import (
    LEGACY_SECONDS_ALIASES,
    format_duration_ms,
    timing_ms,
    with_legacy_seconds,
)

#: 事件 schema 版本。canonical 欄位增減或改名時遞增；讀取端以 ``canonicalize_event`` 相容舊版。
BATCH_EVENT_SCHEMA_VERSION: Final = 2

#: 每個事件一定有的 canonical 欄位（``kind`` 與各事件專屬欄位另外附加）。
EVENT_CORE_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "task_id",
    "run_id",
    "operation",
    "provider",
    "model",
    "phase",
    "batch_index",
    "batch_total",
    "batch_size",
    "selected_count",
    "processed_count",
    "attempt",
    "max_attempts",
    "retry_count",
    "status",
    "elapsed_ms",
)

#: canonical 非計時欄位 -> 舊版欄位名稱（只在輸出邊界補上，讀取舊事件時反向對應）。
#: ``attempt`` 是目前這批的外層嘗試序號；``attempts`` 是已開始的嘗試次數；
#: ``overload``／``rate_limit`` 是 provider 錯誤分類旗標；``suggestion_count`` 是建議修訂筆數。
LEGACY_FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "attempt": ("smart_review_attempt",),
    "attempts": ("smart_review_attempts",),
    "batch_index": ("batch_number",),
    "overload": ("provider_overload",),
    "rate_limit": ("quota_or_rate_limit",),
    "suggestion_count": ("suggest_revision_count",),
}

#: 計時欄位（毫秒）。順序即 ``BatchTiming.to_event()`` 的輸出順序。
TIMING_FIELDS: Final[tuple[str, ...]] = (
    "setup_ms",
    "selection_ms",
    "planning_ms",
    "prompt_build_ms",
    "quota_wait_ms",
    "retry_backoff_ms",
    "network_retry_wait_ms",
    "api_ms",
    "response_parse_ms",
    "validation_ms",
    "commit_ms",
    "history_write_ms",
    "run_record_ms",
    "validation_repair_api_ms",
    "validation_repair_parse_ms",
    "batch_ms",
    "total_ms",
)


@dataclass
class BatchTiming:
    """一組以毫秒累計的批次計時；所有累加都經過 :meth:`add`。"""

    setup_ms: int = 0
    selection_ms: int = 0
    planning_ms: int = 0
    prompt_build_ms: int = 0
    quota_wait_ms: int = 0
    retry_backoff_ms: int = 0
    network_retry_wait_ms: int = 0
    api_ms: int = 0
    response_parse_ms: int = 0
    validation_ms: int = 0
    commit_ms: int = 0
    history_write_ms: int = 0
    run_record_ms: int = 0
    validation_repair_api_ms: int = 0
    validation_repair_parse_ms: int = 0
    batch_ms: int = 0
    total_ms: int = 0

    def add(self, field_name: str, elapsed_ms: float) -> int:
        """把耗時（毫秒，四捨五入、不為負）累加到指定欄位，回傳本次增加值。"""
        if field_name not in TIMING_FIELDS:
            raise AttributeError(field_name)
        value = max(0, round(float(elapsed_ms)))
        setattr(self, field_name, int(getattr(self, field_name)) + value)
        return value

    def add_seconds(self, field_name: str, seconds: float) -> int:
        """與 :meth:`add` 相同，但輸入單位是秒（量測用 ``time.monotonic()`` 時使用）。"""
        return self.add(field_name, float(seconds) * 1000)

    def add_ms(self, field_name: str, elapsed_ms: float) -> int:
        """:meth:`add` 的相容名稱。"""
        return self.add(field_name, elapsed_ms)

    def merge(self, other: BatchTiming) -> None:
        """把另一組計時逐欄位累加進來。"""
        for name in TIMING_FIELDS:
            self.add(name, getattr(other, name))

    def to_event(self) -> dict[str, int]:
        """輸出 canonical 的 ``*_ms`` 欄位，可直接放進事件或 metrics。"""
        return {name: int(getattr(self, name)) for name in TIMING_FIELDS}


_SENSITIVE_FIELD_NAMES: Final = frozenset(
    {
        "api_key",
        "payload",
        "prompt",
        "system_prompt",
        "raw",
        "raw_response",
        "response_text",
        "error_message",
        "exception_message",
    }
)


def safe_fields(event_fields: Mapping[str, Any]) -> dict[str, Any]:
    """限制事件欄位大小，並移除 prompt、response 與 credential 等敏感內容。"""
    safe: dict[str, Any] = {}
    for key, value in event_fields.items():
        if str(key).lower() in _SENSITIVE_FIELD_NAMES:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, Mapping):
            safe[str(key)] = safe_fields(value)
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            safe[str(key)] = [str(item) for item in value[:20]]
        else:
            safe[str(key)] = str(value)[:160]
    return safe


@dataclass(frozen=True)
class BatchEvent:
    """供 app log、UI 任務日誌與最終報告共用的安全結構化事件（canonical 欄位）。"""

    task_id: str
    run_id: str
    operation: str
    provider: str
    model: str
    phase: str
    batch_index: int = 0
    batch_total: int = 0
    batch_size: int = 0
    selected_count: int = 0
    processed_count: int = 0
    attempt: int = 0
    max_attempts: int = 0
    retry_count: int = 0
    status: str = ""
    elapsed_ms: int = 0
    fields: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """轉成可序列化的 canonical 事件，事件專屬欄位會先移除敏感內容並限制大小。"""
        event: dict[str, Any] = {"schema_version": BATCH_EVENT_SCHEMA_VERSION}
        for item in fields(self):
            if item.name != "fields":
                event[item.name] = getattr(self, item.name)
        event.update(safe_fields(self.fields))
        return event


def _present(value: Any) -> bool:
    """欄位是否有值（``None`` 與空字串視為沒有）。"""
    return value is not None and value != ""


def canonicalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """讀取舊版事件：把舊秒數／舊名稱欄位轉成 canonical 欄位（只補缺的，不覆蓋既有值）。"""
    result = dict(event)
    kind = str(result.get("kind") or "")
    if kind.endswith("_resume") and "elapsed_seconds" in result:
        # resume 事件的 elapsed_seconds 是實際等待時間，不是批次經過時間。
        waited = timing_ms(
            {"elapsed_seconds": result.pop("elapsed_seconds")}, "elapsed_ms"
        )
        if waited is not None:
            result.setdefault("waited_ms", waited)
    for canonical in LEGACY_SECONDS_ALIASES:
        if _present(result.get(canonical)):
            continue
        value = timing_ms(result, canonical)
        if value is not None:
            result[canonical] = value
    for canonical, legacy_names in LEGACY_FIELD_ALIASES.items():
        if _present(result.get(canonical)):
            continue
        for legacy in legacy_names:
            if _present(result.get(legacy)):
                result[canonical] = result[legacy]
                break
    return result


def with_legacy_fields(event: Mapping[str, Any]) -> dict[str, Any]:
    """回傳補上舊版非計時欄位（``smart_review_attempt`` 等）的複本；只補缺的欄位。"""
    result = dict(event)
    for canonical, legacy_names in LEGACY_FIELD_ALIASES.items():
        value = result.get(canonical)
        if value is None:
            continue
        for legacy in legacy_names:
            result.setdefault(legacy, value)
    return result


def to_legacy_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """輸出邊界用：補上舊版 ``*_seconds`` 計時別名與舊版欄位名稱。"""
    return with_legacy_fields(with_legacy_seconds(event))


# -- 唯一的中文 formatter -----------------------------------------------------------

OPERATION_LABELS: Final[dict[str, str]] = {
    "smart_review": "智慧校對",
    "translate": "LM 翻譯",
}
_REQUEST_NOUNS: Final[dict[str, str]] = {
    "smart_review": "校對請求",
    "translate": "翻譯請求",
}
_WARNING_KINDS: Final = frozenset(
    {
        "attempt_failure",
        "validation_repair_start",
        "validation_repair_failure",
        "retry_wait",
        "network_failure",
        "network_retry_wait",
        "provider_attempt_failure",
    }
)
_REPAIR_KINDS: Final = (
    "validation_repair_start",
    "validation_repair_attempt_start",
    "validation_repair_attempt_success",
    "validation_repair_failure",
    "validation_repair_complete",
)
_NETWORK_SCOPE_LABELS: Final[dict[str, str]] = {
    "chatgpt_provider_call": "ChatGPT 供應商請求",
}
_UNKNOWN_KIND_FALLBACK: Final = "{prefix} {kind}。"


def _yes_no(value: object) -> str:
    """布林欄位的穩定顯示值。"""
    return "是" if value is True else "否"


def _dur(event: Mapping[str, Any], key: str) -> str:
    """事件中的毫秒欄位轉成給人看的耗時；缺值時顯示 ``--``。"""
    return format_duration_ms(event.get(key))


def _token_text(event: Mapping[str, Any]) -> str:
    """provider 回報的 token 用量；未回報時明確標示。"""
    prompt, output = event.get("prompt_tokens"), event.get("output_tokens")
    if prompt is None and output is None:
        return "token=未提供"
    return f"提示詞 token={prompt}、輸出 token={output}"


def _prefix(event: Mapping[str, Any], default_operation: str) -> str:
    """``[操作][批次 i/n]`` 前綴。"""
    operation = str(event.get("operation") or default_operation or "batch")
    label = OPERATION_LABELS.get(operation, operation)
    return (
        f"[{label}][批次 {event.get('batch_index', '?')}/"
        f"{event.get('batch_total', '?')}]"
    )


def _attempt_prefix(event: Mapping[str, Any], default_operation: str) -> str:
    """前綴加上 ``[第 N 次嘗試]``。"""
    return f"{_prefix(event, default_operation)}[第 {event.get('attempt', '?')} 次嘗試]"


def _expected(event: Mapping[str, Any]) -> Any:
    """這一批（或這次請求）預期的筆數。"""
    return event.get("expected_count", event.get("batch_size", "?"))


def _batch_start(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """批次開始。"""
    where = (
        f"，序號 {event.get('ordinal_start', '?')}～{event.get('ordinal_end', '?')}"
        if "ordinal_start" in event
        else ""
    )
    return (
        f"{prefix} 開始：{_expected(event)} 筆{where}，"
        f"供應商={event.get('provider') or '-'}，模型={event.get('model') or '-'}"
    )


def _attempt_start(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """外層 API 嘗試開始。"""
    noun = _REQUEST_NOUNS.get(operation, "請求")
    return (
        f"{_attempt_prefix(event, operation)} API 請求開始："
        f"{noun}序號={event.get('attempt', '?')}，預期 {_expected(event)} 筆，"
        f"已觀察網路請求={event.get('network_attempts', 0)}。"
    )


def _attempt_success(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """外層 API 嘗試成功。"""
    key = "api_ms" if event.get("api_ms") is not None else "elapsed_ms"
    return (
        f"{_attempt_prefix(event, operation)} API 成功："
        f"耗時={_dur(event, key)}，狀態碼={event.get('http_status', '?')}，"
        f"完成原因={event.get('finish_reason') or '-'}，"
        f"預期/回傳={event.get('expected_count', '?')}/{event.get('actual_count', '?')}，"
        f"{_token_text(event)}，網路請求={event.get('network_attempts', 0)}。"
    )


def _attempt_failure(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """外層 API 嘗試失敗。"""
    kind = event.get("error_kind") or event.get("error_class") or "unknown"
    return (
        f"{_attempt_prefix(event, operation)} API 失敗："
        f"狀態碼={event.get('http_status', '?')}，錯誤分類={kind}，"
        f"例外類型={event.get('exception_type') or 'Exception'}，"
        f"服務過載={_yes_no(event.get('overload'))}，"
        f"配額／速率限制={_yes_no(event.get('rate_limit'))}，"
        f"逾時={_yes_no(event.get('timeout'))}，"
        f"Retry-After={_dur(event, 'retry_after_ms')}，"
        f"API 金鑰輪替={_yes_no(event.get('key_rotated'))}。"
    )


def _provider_attempt(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """provider 層級的嘗試（開始／成功／失敗）。"""
    tag = f"{prefix}[Provider 第 {event.get('provider_attempt', '?')} 次嘗試]"
    kind = event.get("kind")
    if kind == "provider_attempt_start":
        model = event.get("provider_model") or event.get("model") or "-"
        return f"{tag} 請求開始；模型={model}，預期 {_expected(event)} 筆。"
    if kind == "provider_attempt_success":
        return (
            f"{tag} API 成功：狀態碼={event.get('http_status', '?')}，"
            f"finish_reason={event.get('finish_reason') or '-'}，"
            f"預期/回傳={event.get('expected_count', '?')}/"
            f"{event.get('actual_count', '?')}，"
            f"prompt/output tokens={event.get('prompt_tokens', '-')}/"
            f"{event.get('output_tokens', '-')}。"
        )
    return (
        f"{tag} API 失敗：狀態碼={event.get('http_status', '?')}，"
        f"錯誤分類={event.get('error_class', 'unknown')}，"
        f"逾時={_yes_no(event.get('timeout'))}，過載={_yes_no(event.get('overload'))}，"
        f"速率限制={_yes_no(event.get('rate_limit'))}。"
    )


def _validation_repair(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """只針對 validation invalid 條目的受限修復流程。"""
    kind = event.get("kind")
    tag = _attempt_prefix(event, operation)
    if kind == "validation_repair_start":
        errors = event.get("validation_errors") or {}
        error_text = (
            "、".join(f"{key}: {value}" for key, value in list(errors.items())[:3])
            or "未提供"
        )
        return (
            f"{prefix} 驗證修復開始：只重送 {event.get('expected_count', 0)} 筆；"
            f"原驗證錯誤={error_text}。"
        )
    if kind == "validation_repair_attempt_start":
        return (
            f"{tag} 驗證修復 API 請求開始：預期 {event.get('expected_count', 0)} 筆。"
        )
    if kind == "validation_repair_attempt_success":
        return (
            f"{tag} 驗證修復 API 成功："
            f"耗時={_dur(event, 'api_ms')}，狀態碼={event.get('http_status', '?')}，"
            f"預期/回傳={event.get('expected_count', '?')}/"
            f"{event.get('actual_count', '?')}，{_token_text(event)}。"
        )
    if kind == "validation_repair_complete":
        return (
            f"{prefix} 驗證修復完成：有效 +{event.get('repaired_valid_count', 0)}、"
            f"仍無效 +{event.get('repaired_invalid_count', 0)}；"
            "修復結果仍經過完整 strict schema 驗證。"
        )
    return (
        f"{tag} 驗證修復失敗：狀態碼={event.get('http_status', '?')}，"
        f"錯誤分類={event.get('error_kind') or event.get('error_class') or 'unknown'}，"
        f"例外類型={event.get('exception_type') or 'Exception'}；保留原 invalid 結果。"
    )


def _retry(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """provider retry 與配額時鐘的等待、恢復。"""
    kind = event.get("kind")
    tag = _attempt_prefix(event, operation)
    if kind == "retry_wait":
        return (
            f"{tag} 第 {event.get('retry_number', '?')}/"
            f"{event.get('retry_total', '?')} 次重試，等待 {_dur(event, 'wait_ms')}；"
            f"錯誤分類={event.get('error_kind') or event.get('retry_reason') or 'unknown'}，"
            f"狀態碼={event.get('http_status', '?')}，"
            f"Retry-After={_dur(event, 'retry_after_ms')}。"
        )
    if kind == "retry_resume":
        return f"{tag} 重試等待結束，繼續下一次 API 請求。"
    if kind == "rate_limit_wait":
        return f"{tag} 等待配額時鐘，預計 {_dur(event, 'wait_ms')}。"
    return f"{tag} 配額時鐘等待結束，準備送出 API 請求。"


def _network(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """底層 network attempt 與 connection retry。"""
    kind = event.get("kind")
    tag = _attempt_prefix(event, operation)
    attempt = event.get("network_attempt", "?")
    total = event.get("network_total", "?")
    scope = event.get("network_scope")
    scope_label = _NETWORK_SCOPE_LABELS.get(str(scope), scope) if scope else None
    scope_text = f"，範圍={scope_label}" if scope_label else ""
    if kind == "network_attempt_start":
        return f"{tag} 網路請求={attempt}/{total}{scope_text} 開始。"
    if kind == "network_failure":
        error = event.get("error_kind") or event.get("error_class") or "unknown"
        return (
            f"{tag} 網路請求失敗：請求={attempt}/{total}{scope_text}"
            f"，錯誤分類={error}，"
            f"例外類型={event.get('exception_type') or 'Exception'}。"
        )
    if kind == "network_retry_wait":
        return (
            f"{tag} 網路重試，等待 {_dur(event, 'wait_ms')}；"
            f"網路請求={attempt}/{total}。"
        )
    if kind == "network_retry_resume":
        return f"{tag} 網路重試等待結束，繼續後續網路請求（目前 {attempt}/{total}）。"
    return (
        f"{tag} 網路回應：網路請求={attempt}/{total}{scope_text}，"
        f"狀態碼={event.get('http_status', '?')}。"
    )


def _batch_complete(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """批次完成；帶有結果分類（``valid_count``）時列出完整耗時與分類。"""
    if "valid_count" not in event:
        return (
            f"{prefix} 完成：已處理 {event.get('processed_count', 0)} 筆，"
            f"API {format_duration_ms(event.get('api_ms', event.get('elapsed_ms', 0)))}，"
            f"解析 {_dur(event, 'response_parse_ms')}，"
            f"驗證 {_dur(event, 'validation_ms')}，"
            f"寫入 {_dur(event, 'commit_ms')}，"
            f"重試 {event.get('retry_count', 0)} 次，狀態={event.get('status') or '-'}。"
        )
    errors = event.get("validation_errors") or {}
    error_text = (
        "、".join(
            f"{message} × {count:,}" for message, count in list(errors.items())[:5]
        )
        or "無"
    )
    noun = _REQUEST_NOUNS.get(operation, "請求")
    return (
        f"{prefix} 完成：序號 {event.get('ordinal_start', '?')}～"
        f"{event.get('ordinal_end', '?')}，實際 {event.get('actual_count', '?')} 筆；"
        f"{noun} {event.get('attempts', 0)}、"
        f"重試 {event.get('retry_count', 0)}、"
        f"配額等待 {_dur(event, 'quota_wait_ms')}、"
        f"重試等待 {_dur(event, 'retry_backoff_ms')}、"
        f"網路重試等待 {_dur(event, 'network_retry_wait_ms')}、"
        f"API {_dur(event, 'api_ms')}、"
        f"解析 {_dur(event, 'response_parse_ms')}、"
        f"寫入 JSON {_dur(event, 'history_write_ms')}、"
        f"驗證修復 {event.get('validation_repair_attempts', 0)} 次、"
        f"修復有效 +{event.get('repaired_valid_count', 0)}、"
        f"修復後仍無效 +{event.get('repaired_invalid_count', 0)}、"
        f"修復 API {_dur(event, 'validation_repair_api_ms')}、"
        f"本批 {_dur(event, 'elapsed_ms')}；"
        f"有效={event.get('valid_count', 0)}、"
        f"無效={event.get('invalid_count', 0)}、"
        f"需補充上下文={event.get('needs_context_count', 0)}、"
        f"不變更={event.get('no_change_count', 0)}、"
        f"建議修訂={event.get('suggestion_count', 0)}；"
        f"請求錯誤={event.get('request_errors', 0)}；"
        f"驗證錯誤={error_text}。"
    )


def _misc(event: Mapping[str, Any], prefix: str, operation: str) -> str:
    """沒有批次專屬欄位的小事件（計時、key 輪替、run 完成）。"""
    kind = event.get("kind")
    if kind == "timing":
        return (
            f"{prefix} 計時：{event.get('field_name') or '-'} "
            f"{_dur(event, 'elapsed_ms')}。"
        )
    if kind == "key_rotation":
        return f"{prefix} 重試策略切換 API Key：原因={event.get('reason') or '-'}。"
    return f"[{event.get('operation') or operation or 'batch'}] 任務完成：狀態={event.get('status') or '-'}。"


_FORMATTERS: Final[dict[str, Callable[[Mapping[str, Any], str, str], str]]] = {
    "batch_start": _batch_start,
    "attempt_start": _attempt_start,
    "attempt_success": _attempt_success,
    "attempt_failure": _attempt_failure,
    "provider_attempt_start": _provider_attempt,
    "provider_attempt_success": _provider_attempt,
    "provider_attempt_failure": _provider_attempt,
    **dict.fromkeys(_REPAIR_KINDS, _validation_repair),
    **dict.fromkeys(
        ("retry_wait", "retry_resume", "rate_limit_wait", "rate_limit_resume"),
        _retry,
    ),
    **dict.fromkeys(
        (
            "network_attempt_start",
            "network_response",
            "network_failure",
            "network_retry_wait",
            "network_retry_resume",
        ),
        _network,
    ),
    "batch_complete": _batch_complete,
    "timing": _misc,
    "key_rotation": _misc,
    "run_complete": _misc,
}

#: formatter 有專屬文字的事件種類；其餘種類只會得到通用的 ``[操作][批次] kind。``。
FORMATTED_BATCH_EVENT_KINDS: Final = frozenset(_FORMATTERS)


def batch_event_level(event: Mapping[str, Any]) -> str:
    """事件在任務日誌中的等級（``info`` 或 ``warning``）。"""
    event = canonicalize_event(event)
    kind = str(event.get("kind") or "")
    if kind in _WARNING_KINDS:
        return "warning"
    if kind == "validation_repair_complete":
        return "warning" if event.get("repaired_invalid_count", 0) else "info"
    if kind == "batch_complete":
        return "warning" if event.get("invalid_count", 0) else "info"
    return "info"


def format_batch_event_for_log(
    event: Mapping[str, Any], *, default_operation: str = ""
) -> tuple[str, str] | None:
    """任務日誌要顯示的 ``(等級, 文字)``；formatter 不認得的事件種類回傳 ``None``（不顯示）。

    機翻與譯文修復的畫面 log 共用這個規則：只顯示有專屬文字的事件，
    未知種類（例如 ``cancelled``、新增但尚未寫 formatter 的 kind）不產生通用的垃圾行，
    取消等結果由各流程自己的狀態訊息呈現。
    """
    canonical = canonicalize_event(event)
    if str(canonical.get("kind") or "") not in FORMATTED_BATCH_EVENT_KINDS:
        return None
    return (
        batch_event_level(canonical),
        format_batch_event(canonical, default_operation=default_operation),
    )


def format_batch_event(event: Mapping[str, Any], *, default_operation: str = "") -> str:
    """把批次事件轉成有界的中文任務日誌文字。

    canonical 與舊版事件都可以傳入（舊欄位會先轉成 canonical）。``default_operation``
    用於沒有 ``operation`` 欄位的舊歷史事件。
    """
    canonical = canonicalize_event(event)
    kind = str(canonical.get("kind") or "")
    operation = str(canonical.get("operation") or default_operation or "")
    prefix = _prefix(canonical, default_operation)
    formatter = _FORMATTERS.get(kind)
    if formatter is None:
        return _UNKNOWN_KIND_FALLBACK.format(prefix=prefix, kind=kind)
    return formatter(canonical, prefix, operation)


__all__ = [
    "BATCH_EVENT_SCHEMA_VERSION",
    "EVENT_CORE_FIELDS",
    "FORMATTED_BATCH_EVENT_KINDS",
    "LEGACY_FIELD_ALIASES",
    "OPERATION_LABELS",
    "TIMING_FIELDS",
    "BatchEvent",
    "BatchTiming",
    "batch_event_level",
    "canonicalize_event",
    "format_batch_event",
    "format_batch_event_for_log",
    "safe_fields",
    "to_legacy_event",
    "with_legacy_fields",
]
