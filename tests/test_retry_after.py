"""Retry-After 共用解析：數值規則、來源優先序，以及各呼叫端使用同一規則。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import Mock, patch

import pytest
import requests

from translation_tool.core import lm_config_rules as rules
from translation_tool.core import lm_translator_main as main
from translation_tool.core.openai_codex_client import _parse_retry_after
from translation_tool.core.retry_after import (
    parse_response_retry_after,
    parse_retry_after,
    parse_retry_info_seconds,
    parse_retry_seconds,
)


def _body(delay: object) -> dict:
    return {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": delay,
                }
            ]
        }
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("34.9", 34.9),
        ("0.5", 0.5),
        (" 45 ", 45.0),
        (0, 0.0),
        (2.5, 2.5),
        ("-1", None),
        (-3, None),
        ("nan", None),
        ("inf", None),
        ("Infinity", None),
        (float("nan"), None),
        (float("inf"), None),
        ("abc", None),
        ("", None),
        (None, None),
        (True, None),
        (Mock(), None),
    ],
)
def test_parse_retry_seconds_rejects_non_finite_or_negative(value, expected):
    assert parse_retry_seconds(value) == expected


def test_retry_info_accepts_s_suffix_and_keeps_fraction():
    assert parse_retry_info_seconds(_body("34.9s")) == 34.9
    assert parse_retry_info_seconds(_body("0.5s")) == 0.5
    assert parse_retry_info_seconds(_body("12")) == 12.0
    assert parse_retry_info_seconds(_body("nans")) is None
    assert parse_retry_info_seconds(_body("-2s")) is None
    assert parse_retry_info_seconds({"error": "text"}) is None
    assert parse_retry_info_seconds(None) is None


def test_header_only_body_only_and_header_wins_over_body():
    assert parse_retry_after({"Retry-After": "45"}, {}) == 45.0
    assert parse_retry_after({}, _body("7.5s")) == 7.5
    assert parse_retry_after({"Retry-After": "3"}, _body("99s")) == 3.0
    # header 無法解析時退回 body，而不是整個忽略。
    assert parse_retry_after({"Retry-After": "soon"}, _body("7s")) == 7.0
    assert parse_retry_after({"retry-after": "4"}, None) == 4.0
    assert parse_retry_after(None, None) is None


def test_http_date_header_future_and_past():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    future = format_datetime(now + timedelta(seconds=30), usegmt=True)
    past = format_datetime(now - timedelta(seconds=30), usegmt=True)
    assert parse_retry_after({"Retry-After": future}, None, now=now) == pytest.approx(
        30.0
    )
    assert parse_retry_after({"Retry-After": past}, None, now=now) == 0.0


def test_response_without_json_body_uses_header():
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "2.5"
    assert parse_response_retry_after(response) == 2.5
    assert parse_response_retry_after(None) is None


def test_chatgpt_parser_shares_the_same_numeric_rules():
    assert _parse_retry_after("0.5") == 0.5
    assert _parse_retry_after("inf") is None
    assert _parse_retry_after("nan") is None
    assert _parse_retry_after("-1") is None


def _http_429(body: dict | None, headers: dict | None = None):
    response = Mock()
    response.status_code = 429
    response.headers = headers or {}
    response.text = "429"
    if body is None:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = body
    return requests.HTTPError("429", response=response)


def _rpm_body(delay: object | None) -> dict:
    body = {
        "error": {
            "message": "rate limited",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "RequestsPerMinutePerProject"}],
                }
            ],
        }
    }
    if delay is not None:
        body["error"]["details"].append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
        )
    return body


@pytest.mark.parametrize(
    ("delay", "headers", "expected"),
    [("34.9s", None, 34.9), ("0.5s", None, 0.5), (None, {"Retry-After": "45"}, 45.0)],
)
def test_parse_rate_limit_error_keeps_fractional_and_header_only_waits(
    delay, headers, expected
):
    kind, wait, _message = main._parse_rate_limit_error(
        _http_429(_rpm_body(delay), headers)
    )
    assert kind == "rpm"
    assert wait == expected


def test_parse_rate_limit_error_unparsed_body_is_not_a_quota_exhaustion():
    kind, wait, _message = main._parse_rate_limit_error(
        _http_429(None, {"Retry-After": "8"})
    )
    assert kind == "unparsed"
    assert wait == 8.0


def test_parse_rate_limit_error_unknown_valid_json_is_unparsed():
    error = _http_429(
        {
            "error": {
                "code": 429,
                "message": "Rate limit exceeded",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
        {"Retry-After": "8"},
    )

    kind, wait, _message = main._parse_rate_limit_error(error)

    assert kind == "unparsed"
    assert wait == 8.0


def test_unknown_valid_json_429_retries_same_key_before_rotation(monkeypatch):
    from types import SimpleNamespace

    waits = []
    monkeypatch.setattr(
        main,
        "_sleep_with_runtime_event",
        lambda _runtime, seconds, **_kwargs: waits.append(seconds),
    )
    runtime = SimpleNamespace(unparsed_rate_limit_count=0, key_cycle=Mock())
    error = _http_429(
        {
            "error": {
                "code": 429,
                "message": "Rate limit exceeded",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
        {"Retry-After": "8"},
    )

    action = main._handle_batch_error(runtime, error, 0)

    assert action is main.BatchAction.RETRY_SAME_KEY
    assert waits == [8.0]
    runtime.key_cycle.mark_failed.assert_not_called()


def test_rate_limit_waits_reject_unbounded_provider_delays():
    error = _http_429(_rpm_body(None), {"Retry-After": "121"})

    assert main._parse_rate_limit_error(error)[1] == 0.0
    from translation_tool.translation_db.smart_review_request import _http_retry_after

    assert _http_retry_after(error) is None


def test_parse_rate_limit_error_rpd_keeps_model_quota_classification():
    body = {
        "error": {
            "message": "quota exceeded",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerModel"}],
                }
            ],
        }
    }
    assert main._parse_rate_limit_error(_http_429(body))[0] == "rpd"


def test_parse_rate_limit_error_explicit_quota_message_keeps_quota_classification():
    body = {"error": {"message": "Quota exceeded"}}

    assert main._parse_rate_limit_error(_http_429(body))[0] == "quota"


def test_smart_review_and_lm_read_the_same_retry_after():
    from translation_tool.translation_db.smart_review_request import _http_retry_after

    error = _http_429(_rpm_body("0.5s"), {"Retry-After": "2.5"})
    assert _http_retry_after(error) == 2.5
    assert parse_response_retry_after(error.response) == 2.5
    assert main._parse_rate_limit_error(error)[1] == 2.5


def test_gemini_rpm_wait_uses_fractional_retry_after_without_real_sleep():
    runtime = Mock()
    runtime.key_cycle = Mock()
    with patch.object(main, "_sleep_with_runtime_event") as sleep:
        action = main._handle_batch_error(
            runtime, _http_429(_rpm_body("0.5s")), 0, retry_event_callback=None
        )
    assert action is main.BatchAction.RETRY_SAME_KEY
    assert sleep.call_args.args[1] == 0.5


@pytest.mark.parametrize(
    "key",
    [
        "AIza" + "a" * 35,
        "AQ." + "b" * 40,
        "AIza-too-short",
        "XXXX" + "a" * 40,
        "AIza" + "a" * 35 + " !",
        "AIza" + "a" * 35 + "中",
    ],
)
def test_key_format_rule_is_shared_by_runtime_and_ui_validation(key):
    problem = rules._gemini_key_format_error(key)
    if problem is None:
        rules.validate_api_keys_from_ui([key])
        return
    with pytest.raises(RuntimeError) as ui_error:
        rules.validate_api_keys_from_ui([key])
    assert str(ui_error.value) == problem[1]
    with (
        patch.object(rules, "get_translation_provider", return_value="gemini"),
        patch.object(rules, "_get_all_keys", return_value=[key]),
        pytest.raises(RuntimeError) as runtime_error,
    ):
        rules.validate_api_keys()
    assert str(runtime_error.value) == problem[1]


def test_redaction_pattern_is_not_the_credential_validator():
    """遮蔽規則 AIza 需 20+ 字元；格式驗證要求總長 35。兩者是各自獨立的規則。"""
    from translation_tool.utils import redaction

    short_but_redacted = "AIza" + "a" * 20
    assert redaction._GOOGLE_KEY_RE.search(short_but_redacted)
    assert rules._gemini_key_format_error(short_but_redacted) is not None
