"""provider 錯誤分類決策表：LM、智慧校對與修復請求的診斷旗標必須一致。

``overload`` 只代表供應商服務過載（503 家族）；500／502／504 是 provider 暫時失敗，
不計入 overload。``rate_limit`` 是速率或配額受限。重試決策不由這些旗標決定。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from translation_tool.core import lm_translator_main as main
from translation_tool.core.common_batch_executor import (
    CommonBatchExecutor,
    classify_provider_error,
)
from translation_tool.core.openai_codex_client import ChatGPTAPIError
from translation_tool.translation_db.smart_review_provider import (
    _smart_review_transient_retry,
    _SmartReviewRetryRuntime,
)
from translation_tool.translation_db.smart_review_run_state import (
    SmartReviewRunState,
    new_smart_review_metrics,
)


def _http_error(status: int, headers: dict | None = None) -> requests.HTTPError:
    response = Mock()
    response.status_code = status
    response.headers = headers or {}
    response.json.return_value = {"error": {"message": "x", "status": ""}}
    return requests.HTTPError(str(status), response=response)


# (錯誤物件工廠, overload, rate_limit, timeout, connection, retryable)
DECISION_TABLE = [
    pytest.param(lambda: _http_error(408), False, False, True, False, True, id="408"),
    pytest.param(lambda: _http_error(429), False, True, False, False, True, id="429"),
    pytest.param(lambda: _http_error(500), False, False, False, False, True, id="500"),
    pytest.param(lambda: _http_error(502), False, False, False, False, True, id="502"),
    pytest.param(lambda: _http_error(503), True, False, False, False, True, id="503"),
    pytest.param(lambda: _http_error(504), False, False, True, False, True, id="504"),
    pytest.param(lambda: _http_error(400), False, False, False, False, False, id="400"),
    pytest.param(lambda: _http_error(403), False, False, False, False, False, id="403"),
    pytest.param(
        lambda: ChatGPTAPIError("rate_limit_exceeded", status=429),
        False,
        True,
        False,
        False,
        True,
        id="code-rate_limit_exceeded",
    ),
    pytest.param(
        lambda: ChatGPTAPIError("server_overloaded", status=500),
        True,
        False,
        False,
        False,
        True,
        id="code-server_overloaded",
    ),
    pytest.param(
        lambda: requests.Timeout("t"), False, False, True, False, True, id="timeout"
    ),
    pytest.param(
        lambda: requests.ConnectionError("c"),
        False,
        False,
        False,
        True,
        True,
        id="connection",
    ),
]


@pytest.mark.parametrize(
    ("make", "overload", "rate_limit", "timeout", "connection", "retryable"),
    DECISION_TABLE,
)
def test_decision_table(make, overload, rate_limit, timeout, connection, retryable):
    error = make()
    status = getattr(error, "status", None)
    result = classify_provider_error(error, status)
    assert result["overload"] is overload
    assert result["rate_limit"] is rate_limit
    assert result["timeout"] is timeout
    assert result["connection"] is connection
    assert result["retryable"] is retryable


def test_classification_reads_retry_after_header_of_http_error():
    """requests.HTTPError 沒有 .retry_after 屬性時，也要從回應 header 帶出 retry_after_ms。"""
    result = classify_provider_error(_http_error(429, {"Retry-After": "2.5"}))
    assert result["retry_after_ms"] == 2500
    assert classify_provider_error(_http_error(429))["retry_after_ms"] is None
    attr_error = ChatGPTAPIError("x", status=429, retry_after=3)
    assert classify_provider_error(attr_error, 429)["retry_after_ms"] == 3000


def _state(progress: list) -> SmartReviewRunState:
    executor = CommonBatchExecutor(operation="smart_review", event_sink=lambda _e: None)
    state = SmartReviewRunState(
        db=None,
        run_id="run-1",
        executor=executor,
        selected_count=1,
        planned_batch_count=1,
        progress=progress.append,
        metrics=new_smart_review_metrics(input_token_budget=None),
        started=0.0,
    )
    state.begin_batch(
        number=1,
        size=1,
        ordinal_start=1,
        ordinal_end=1,
        provider="gemini",
        model_name="m",
    )
    return state


def _flags(event: dict) -> tuple[bool, bool, bool]:
    return (
        bool(event.get("overload")),
        bool(event.get("rate_limit")),
        bool(event.get("timeout")),
    )


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_lm_chatgpt_retry_and_smart_review_failure_flags_agree(status):
    error = ChatGPTAPIError("transient", status=status)
    expected = classify_provider_error(error, status)
    expected_flags = (expected["overload"], expected["rate_limit"], expected["timeout"])

    # LM 主流程的 ChatGPT retry_wait（目前輸出舊欄位名稱，經 canonicalize 後比較）。
    from translation_tool.core.batch_events import canonicalize_event

    events: list[dict] = []
    runtime = SimpleNamespace(chatgpt_retry_count=0)
    with patch.object(main, "interruptible_sleep"):
        main._handle_chatgpt_api_error(
            runtime, error, retry_event_callback=events.append
        )
    lm_event = canonicalize_event(next(e for e in events if e["kind"] == "retry_wait"))
    assert _flags(lm_event) == expected_flags

    # 智慧校對 attempt_failure。
    progress: list = []
    state = _state(progress)
    state.emit_attempt_failure(error, batch_number=1, attempt=1, status=status)
    failure = next(
        s["event"]
        for s in progress
        if "event" in s and s["event"]["kind"] == "attempt_failure"
    )
    assert _flags(failure) == expected_flags
    assert failure["retryable"] is expected["retryable"]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_gemini_transient_retry_event_flags_match_classification(status):
    error = _http_error(status)
    expected = classify_provider_error(error, status)
    events: list[dict] = []
    runtime = _SmartReviewRetryRuntime(provider="gemini", batch_interval_sec=0)
    with patch(
        "translation_tool.translation_db.smart_review_provider._smart_review_sleep"
    ):
        assert _smart_review_transient_retry(
            runtime, "m", error, status, cancel_check=None, event_callback=events.append
        )
    event = next(e for e in events if e["kind"] == "retry_wait")
    assert _flags(event) == (
        expected["overload"],
        expected["rate_limit"],
        expected["timeout"],
    )


def test_overload_is_only_the_503_family_not_every_transient_5xx():
    assert [
        classify_provider_error(_http_error(s))["overload"]
        for s in (500, 502, 503, 504)
    ] == [False, False, True, False]


def test_attempt_failure_carries_header_retry_after_without_explicit_argument():
    progress: list = []
    state = _state(progress)
    state.emit_attempt_failure(
        _http_error(429, {"Retry-After": "7"}), batch_number=1, attempt=1, status=429
    )
    failure = next(
        s["event"]
        for s in progress
        if "event" in s and s["event"]["kind"] == "attempt_failure"
    )
    assert failure["retry_after_ms"] == 7000


def test_gemini_503_wait_formula_is_max_of_backoff_and_retry_after():
    delays = main.GEMINI_503_RETRY_DELAYS_SEC
    assert main.gemini_503_wait_sec(0, None) == delays[0]
    assert main.gemini_503_wait_sec(0, delays[0] + 5) == delays[0] + 5
    assert main.gemini_503_wait_sec(1, 0.5) == delays[1]
