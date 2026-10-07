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
        ('{"items":[],"debug":true}', "root contains fields outside items"),
        (
            '{"items":[{"id":"0","value":"鐵錠","debug":true},{"id":"1","value":"鑽石劍"}]}',
            "items[0] does not contain exactly id and value",
        ),
        ('{"items":"not-an-array"}', "items is not an array"),
        ('{"items":[],"other":true}', "root contains fields outside items"),
        ('{"items":[]}', "missing IDs=['0', '1']"),
        (
            '{"items":[{"id":0,"value":"鐵錠"},{"id":"1","value":"鑽石劍"}]}',
            "items[0] id/value is not a string",
        ),
        (
            '{"items":[{"id":"0","value":42},{"id":"1","value":"鑽石劍"}]}',
            "items[0] id/value is not a string",
        ),
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


@pytest.mark.parametrize(
    "response",
    [
        '{"0":"鐵錠","1":"鑽石劍"}',
        '[{"id":"0","value":"鐵錠"},{"id":"1","value":"鑽石劍"}]',
    ],
)
def test_legacy_response_shapes_are_rejected_and_sent_to_retry_flow(
    batch_context, response, monkeypatch
):
    runtime, round_data = batch_context
    warning = Mock()
    monkeypatch.setattr("translation_tool.core.lm_translator_main.log_warning", warning)

    merged, rejected = _merge_batch_response(
        runtime, round_data, response, {"finish_reason": "STOP"}
    )

    assert merged is None
    assert rejected is True
    assert "root must contain exactly the items property" in warning.call_args.args[0]
    runtime.budget_tracker.on_truncated.assert_called_once_with("STOP", kind="missing")


def test_incomplete_response_is_rejected_now_that_schema_has_no_length_limit(
    batch_context, monkeypatch
):
    """Schema 不再要求「剛好 N 筆」，少回的批次必須由程式端擋下，不可合併。"""
    runtime, round_data = batch_context
    warning = Mock()
    monkeypatch.setattr("translation_tool.core.lm_translator_main.log_warning", warning)

    merged, rejected = _merge_batch_response(
        runtime,
        round_data,
        '{"items":[{"id":"1","value":"鑽石劍"}]}',
        {"finish_reason": "STOP"},
    )

    assert merged is None and rejected is True
    assert "missing IDs=['0']" in warning.call_args.args[0]
    runtime.budget_tracker.on_truncated.assert_called_once_with("STOP", kind="missing")


def test_response_with_extra_items_beyond_the_batch_is_rejected(
    batch_context, monkeypatch
):
    runtime, round_data = batch_context
    warning = Mock()
    monkeypatch.setattr("translation_tool.core.lm_translator_main.log_warning", warning)
    response = (
        '{"items":[{"id":"0","value":"鐵錠"},{"id":"1","value":"鑽石劍"},'
        '{"id":"2","value":"多出來的"}]}'
    )

    merged, rejected = _merge_batch_response(
        runtime, round_data, response, {"finish_reason": "STOP"}
    )

    assert merged is None and rejected is True
    assert "unexpected IDs=['2']" in warning.call_args.args[0]


def test_translation_identical_to_source_is_a_normal_result_with_neutral_log(
    batch_context, monkeypatch
):
    """譯文與原文相同（專有名詞等）不是失敗：照常回傳、不標記、不重試；日誌只做中性統計。"""
    from translation_tool.core import lm_translator_main

    runtime, round_data = batch_context
    round_data.id_to_item["0"] = {"path": "mod.name", "text": "Minecraft"}
    logs: list[str] = []
    monkeypatch.setattr(
        lm_translator_main, "log_info", lambda m, *a, **k: logs.append(m)
    )

    merged, rejected = _merge_batch_response(
        runtime,
        round_data,
        '{"items":[{"id":"0","value":"Minecraft"},{"id":"1","value":"鑽石劍"}]}',
        {},
    )

    assert rejected is False  # 不觸發重試／縮小批次
    assert [item["text"] for item in merged] == ["Minecraft", "鑽石劍"]
    assert not any("_untranslated" in item for item in merged)  # 不標失敗
    assert merged[0] == {
        **round_data.id_to_item["0"],
        "text": "Minecraft",
    }  # 不改寫項目
    runtime.budget_tracker.on_truncated.assert_not_called()
    assert any("本批次翻譯與原文相同 1/2" in m for m in logs)
    assert not any("疑似未翻" in m for m in logs)
