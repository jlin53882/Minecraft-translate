"""Structured response and translation ID contract regression tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from translation_tool.core.lm_translator_main import _merge_batch_response


@pytest.fixture
def batch_context():
    items = {
        "0": {"path": "items/iron.json", "text": "Iron Ingot"},
        "1": {"path": "items/sword.json", "text": "Diamond Sword"},
    }
    runtime = SimpleNamespace(budget_tracker=Mock())
    round_data = SimpleNamespace(id_to_item=items)
    return runtime, round_data


def test_structured_response_with_matching_ids_merges_translations(batch_context):
    runtime, round_data = batch_context

    merged, rejected = _merge_batch_response(
        runtime,
        round_data,
        '{"items":[{"id":"0","value":"鐵錠"},{"id":"1","value":"鑽石劍"}]}',
        {},
    )

    assert rejected is False
    assert [item["text"] for item in merged] == ["鐵錠", "鑽石劍"]
    runtime.budget_tracker.on_truncated.assert_not_called()


@pytest.mark.parametrize(
    ("response", "expected_log_fragment"),
    [
        ('{"items":[{"id":"0","value":"鐵錠"}]}', "missing IDs=['1']"),
        (
            '{"items":[{"id":"8","value":"鐵錠"},{"id":"9","value":"鑽石劍"}]}',
            "unexpected IDs=['8', '9']",
        ),
        (
            '{"items":[{"id":"0","value":"A"},{"id":"0","value":"B"}]}',
            "duplicate id '0'",
        ),
    ],
)
def test_id_contract_mismatches_are_rejected_and_sent_to_retry_flow(
    batch_context, response, expected_log_fragment, monkeypatch
):
    runtime, round_data = batch_context

    warning = Mock()
    monkeypatch.setattr("translation_tool.core.lm_translator_main.log_warning", warning)
    merged, rejected = _merge_batch_response(
        runtime, round_data, response, {"finish_reason": "STOP"}
    )

    assert merged is None
    assert rejected is True
    assert expected_log_fragment in warning.call_args.args[0]
    runtime.budget_tracker.on_truncated.assert_called_once_with("STOP", kind="missing")
