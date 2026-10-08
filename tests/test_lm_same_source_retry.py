"""Regression tests for the optional one-pass same-as-source confirmation."""

from __future__ import annotations

import json
import threading

import pytest
import requests

from translation_tool.core import lm_translator_main as main
from translation_tool.core.lm_batch_budget import get_tracker
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(str(status), response=response)


@pytest.fixture
def configure_batch(monkeypatch):
    def configure(*, retry=True):
        config = {
            "lm_translator": {
                "models": {"same-source-test-model": {"enabled": True}},
                "temperature": 0.25,
                "retry_same_as_source": retry,
                "token_budget_enabled": False,
                "max_output_tokens": 2048,
                "initial_batch_size_patchouli": 100,
                "initial_batch_size_lang": 100,
                "initial_batch_size_ftb": 100,
                "initial_batch_size_kubejs": 100,
                "initial_batch_size_md": 100,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 1,
                "lang_system_prompt": "LANG PROFILE PROMPT",
                "patchouli_system_prompt": "PATCHOULI PROFILE PROMPT",
            }
        }
        monkeypatch.setattr(main, "load_config", lambda: config)
        monkeypatch.setattr(
            "translation_tool.core.lm_config_rules._get_all_keys",
            lambda: ["test-key"],
        )
        monkeypatch.setattr(main, "interruptible_sleep", lambda _seconds: None)
        return config

    return configure


def _reply(call, by_value):
    return json.dumps(
        {
            "items": [
                {
                    "id": item["id"],
                    "value": by_value.get(item["value"], item["value"]),
                }
                for item in call["payload"]["items"]
            ]
        },
        ensure_ascii=False,
    )


def _item(text, index, **extra):
    return {
        "path": f"sample.key.{index}",
        "text": text,
        "source_text": text,
        "cache_type": "lang",
        "_entry_id": index + 100,
        "_kind": "lang",
        "_mod_id": "samplemod",
        "file": "samplemod/lang/en_us.json",
        **extra,
    }


def _run_with_api(monkeypatch, items, replies):
    calls = []

    def call_api(**kwargs):
        calls.append(kwargs)
        response = replies[len(calls) - 1]
        if isinstance(response, BaseException):
            raise response
        return response(kwargs) if callable(response) else response

    monkeypatch.setattr(main, "call_gemini_requests", call_api)
    result, status = main.translate_batch_smart(items, len(items))
    return result, status, calls


def test_setting_off_keeps_one_request_and_accepts_same_text(
    monkeypatch, configure_batch
):
    configure_batch(retry=False)
    item = _item("Minecraft", 0)

    result, status, calls = _run_with_api(
        monkeypatch, [item], [lambda call: _reply(call, {})]
    )

    assert status == "AUTO"
    assert len(calls) == 1
    assert result == [{**item, "text": "Minecraft"}]
    assert "_untranslated" not in result[0]


def test_cancel_before_provider_request_does_not_send_api_call(
    monkeypatch, configure_batch
):
    configure_batch(retry=False)
    calls = []
    monkeypatch.setattr(
        main, "call_gemini_requests", lambda **kwargs: calls.append(kwargs)
    )

    with cancel_scope(lambda: True), pytest.raises(TaskCancelled):
        main.translate_batch_smart([_item("Pressure Chamber", 0)], 1)

    assert calls == []


def test_cancel_during_provider_request_discards_successful_response(
    monkeypatch, configure_batch
):
    configure_batch(retry=False)
    cancel = threading.Event()
    calls = []

    def complete_then_cancel(**kwargs):
        calls.append(kwargs)
        response = _reply(kwargs, {kwargs["payload"]["items"][0]["value"]: "壓力室"})
        cancel.set()
        return response

    monkeypatch.setattr(main, "call_gemini_requests", complete_then_cancel)

    with cancel_scope(cancel.is_set), pytest.raises(TaskCancelled):
        main.translate_batch_smart([_item("Pressure Chamber", 0)], 1)

    assert len(calls) == 1


def test_same_as_source_is_reconfirmed_and_second_translation_is_used(
    monkeypatch, configure_batch
):
    configure_batch()
    item = _item("Pressure Chamber", 0)
    result, status, calls = _run_with_api(
        monkeypatch,
        [item],
        [
            lambda call: _reply(call, {}),
            lambda call: _reply(call, {"Pressure Chamber": "壓力室"}),
        ],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert [entry["value"] for entry in calls[1]["payload"]["items"]] == [
        "Pressure Chamber"
    ]
    assert calls[1]["model_name"] == calls[0]["model_name"]
    assert calls[1]["api_key"] == calls[0]["api_key"]
    assert calls[1]["temperature"] == calls[0]["temperature"]
    retry_prompt = calls[1]["system_prompt"]
    assert retry_prompt.startswith("LANG PROFILE PROMPT")
    assert "再次確認" in retry_prompt and "繁體中文（台灣用語）" in retry_prompt
    assert result == [{**item, "text": "壓力室"}]


def test_still_same_after_retry_is_normal_final_result_with_no_third_request(
    monkeypatch, configure_batch
):
    configure_batch()
    item = _item("Minecraft", 0)

    result, status, calls = _run_with_api(
        monkeypatch,
        [item],
        [lambda call: _reply(call, {}), lambda call: _reply(call, {})],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert result == [{**item, "text": "Minecraft"}]
    assert "_untranslated" not in result[0]


def test_mixed_batch_retries_candidates_only_and_restores_order_and_metadata(
    monkeypatch, configure_batch
):
    configure_batch()
    items = [_item("Alpha", 0), _item("Beta", 1), _item("Gamma", 2), _item("Delta", 3)]

    result, status, calls = _run_with_api(
        monkeypatch,
        items,
        [
            lambda call: _reply(call, {"Alpha": "阿爾法", "Gamma": "伽瑪"}),
            lambda call: _reply(call, {"Beta": "貝塔", "Delta": "德爾塔"}),
        ],
    )

    assert status == "AUTO"
    assert [entry["value"] for entry in calls[1]["payload"]["items"]] == [
        "Beta",
        "Delta",
    ]
    assert [entry["id"] for entry in calls[1]["payload"]["items"]] == ["1", "3"]
    assert [entry["text"] for entry in result] == ["阿爾法", "貝塔", "伽瑪", "德爾塔"]
    for original, translated in zip(items, result, strict=True):
        for field in ("_entry_id", "_kind", "_mod_id", "file", "path", "source_text"):
            assert translated[field] == original[field]


@pytest.mark.parametrize(
    ("cache_type", "expected_prompt"),
    [
        ("lang", "LANG PROFILE PROMPT"),
        ("patchouli", "PATCHOULI PROFILE PROMPT"),
        ("ftbquests", "PATCHOULI PROFILE PROMPT"),
        ("kubejs", "LANG PROFILE PROMPT"),
        ("md", "PATCHOULI PROFILE PROMPT"),
    ],
)
def test_all_batch_profiles_use_their_existing_prompt_route(
    monkeypatch, configure_batch, cache_type, expected_prompt
):
    configure_batch()
    item = {**_item("Minecraft", 0), "cache_type": cache_type}
    _result, status, calls = _run_with_api(
        monkeypatch,
        [item],
        [lambda call: _reply(call, {}), lambda call: _reply(call, {})],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert calls[0]["system_prompt"] == expected_prompt
    assert calls[1]["system_prompt"].startswith(expected_prompt)


def test_candidate_filter_keeps_short_and_uppercase_words_but_skips_symbols(
    monkeypatch, configure_batch
):
    configure_batch()
    values = [
        "123",
        "---",
        "§",
        "%%",
        "RF",
        "FE",
        "JEI",
        "API",
        "Ore",
        "Axe",
        "Log",
        "Create",
        "Minecraft",
    ]
    items = [_item(value, index) for index, value in enumerate(values)]
    items.append(_item("Marked fallback", len(items), _untranslated=True))
    result, status, calls = _run_with_api(
        monkeypatch,
        items,
        [lambda call: _reply(call, {}), lambda call: _reply(call, {})],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert [entry["value"] for entry in calls[1]["payload"]["items"]] == values[4:]
    assert calls[1]["payload"]["items"][-1]["value"] == "Minecraft"
    assert len(result) == len(items)


@pytest.mark.parametrize(
    "failure",
    [requests.Timeout("timeout"), _http_error(429), _http_error(503)],
    ids=["timeout", "http-429", "http-503"],
)
def test_retry_technical_failure_falls_back_to_first_valid_result(
    monkeypatch, configure_batch, failure
):
    configure_batch()
    item = _item("Minecraft", 0)
    result, status, calls = _run_with_api(
        monkeypatch,
        [item],
        [lambda call: _reply(call, {}), failure],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert result == [{**item, "text": "Minecraft"}]


def test_retry_structured_response_failure_does_not_shrink_or_fail_batch(
    monkeypatch, configure_batch
):
    configure_batch()
    item = _item("Minecraft", 0)
    result, status, calls = _run_with_api(
        monkeypatch,
        [item],
        [lambda call: _reply(call, {}), '{"items":[]}'],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert result == [{**item, "text": "Minecraft"}]


@pytest.mark.parametrize(
    "blank_value", ["", " ", "\n"], ids=["empty", "space", "newline"]
)
def test_blank_retry_value_keeps_first_valid_result(
    monkeypatch, configure_batch, blank_value
):
    configure_batch()
    item = _item("Minecraft", 0)
    result, status, calls = _run_with_api(
        monkeypatch,
        [item],
        [
            lambda call: _reply(call, {}),
            lambda call: _reply(call, {"Minecraft": blank_value}),
        ],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert result == [{**item, "text": "Minecraft"}]


def test_partial_blank_retry_keeps_first_value_only_for_blank_candidate(
    monkeypatch, configure_batch
):
    configure_batch()
    items = [_item("Alpha", 0), _item("Beta", 1), _item("Gamma", 2)]
    result, status, calls = _run_with_api(
        monkeypatch,
        items,
        [
            lambda call: _reply(call, {"Alpha": "阿爾法"}),
            lambda call: _reply(call, {"Beta": "貝塔", "Gamma": " \n "}),
        ],
    )

    assert status == "AUTO"
    assert len(calls) == 2
    assert [entry["value"] for entry in calls[1]["payload"]["items"]] == [
        "Beta",
        "Gamma",
    ]
    assert [entry["text"] for entry in result] == ["阿爾法", "貝塔", "Gamma"]


def test_retry_cancellation_propagates_instead_of_falling_back(
    monkeypatch, configure_batch
):
    configure_batch()
    item = _item("Minecraft", 0)

    with pytest.raises(TaskCancelled):
        _run_with_api(
            monkeypatch,
            [item],
            [lambda call: _reply(call, {}), TaskCancelled()],
        )


def test_retry_token_estimate_uses_only_candidates_and_extended_prompt(
    monkeypatch, configure_batch
):
    config = configure_batch()
    config["lm_translator"]["retry_same_as_source"] = True
    items = [_item("Translated", 0), _item("Minecraft", 1), _item("JEI", 2)]
    tracker = get_tracker("lang")
    estimate = tracker.estimate
    observations = []

    def record_estimate(batch, budget, fixed_input_tokens=0):
        observations.append((len(batch), fixed_input_tokens))
        return estimate(batch, budget, fixed_input_tokens)

    monkeypatch.setattr(tracker, "estimate", record_estimate)
    _result, _status, calls = _run_with_api(
        monkeypatch,
        items,
        [
            lambda call: _reply(call, {"Translated": "已翻譯"}),
            lambda call: _reply(call, {}),
        ],
    )

    assert len(calls) == 2
    assert observations[0][0] == 3
    assert observations[1][0] == 2
    assert observations[1][1] > observations[0][1]


def test_batch_input_fit_reserves_retry_prompt_only_when_enabled(
    monkeypatch, configure_batch
):
    item = _item("Minecraft", 0)
    config = configure_batch(retry=True)
    enabled_runtime = main._build_batch_runtime([item], 1)
    assert (
        enabled_runtime.batch_fit_input_tokens
        == enabled_runtime.retry_fixed_input_tokens
    )
    assert enabled_runtime.batch_fit_input_tokens > enabled_runtime.fixed_input_tokens

    config["lm_translator"]["retry_same_as_source"] = False
    disabled_runtime = main._build_batch_runtime([item], 1)
    assert (
        disabled_runtime.batch_fit_input_tokens == disabled_runtime.fixed_input_tokens
    )


@pytest.mark.parametrize(
    ("second_value", "expected"),
    [("壓力室", "壓力室"), ("Pressure Chamber", "Pressure Chamber")],
    ids=["updated", "still-same"],
)
def test_cache_receives_only_the_final_retry_translation(
    monkeypatch, configure_batch, second_value, expected
):
    from translation_tool.core import lm_translator_shared_loop as shared_loop

    config = configure_batch()
    monkeypatch.setattr(shared_loop, "_lm_config", lambda: config["lm_translator"])
    calls = []

    def call_api(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _reply(kwargs, {})
        return _reply(kwargs, {"Pressure Chamber": second_value})

    monkeypatch.setattr(main, "call_gemini_requests", call_api)
    writes = []
    saves = []
    item = _item("Pressure Chamber", 0)
    result = shared_loop.translate_items_with_cache_loop(
        [item],
        total_for_smart=1,
        translate_batch_smart=main.translate_batch_smart,
        batch_size_by_type={"lang": 1},
        reload_cache=False,
        sleep_seconds_between_batches=0,
        cache_add=lambda *args, **kwargs: writes.append(args) or True,
        cache_save=lambda cache_type, **kwargs: saves.append(cache_type) or True,
    )

    assert result.status == "DONE"
    assert result.processed == 1
    assert writes == [("lang", item["path"], "Pressure Chamber", expected)]
    assert saves == ["lang"]
