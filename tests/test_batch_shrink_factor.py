"""縮批比例與隱藏等待設定：缺漏用 schema 預設，非法值要明確報錯或退回預設。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from translation_tool.core import lm_translator_main as main
from translation_tool.utils.config_schema import (
    get_schema_default,
    validate_batch_shrink_factor,
)


def test_missing_factor_uses_schema_default_not_a_private_fallback():
    assert get_schema_default("lm_translator.batch_shrink_factor") == 0.5
    assert main._batch_shrink_factor({}) == 0.5
    assert main._batch_shrink_factor({"batch_shrink_factor": None}) == 0.5


@pytest.mark.parametrize("value", [0.1, 0.5, 0.75, 0.999, "0.6", 1e-6])
def test_valid_factor_is_returned_unchanged(value):
    assert main._batch_shrink_factor({"batch_shrink_factor": value}) == float(value)
    assert validate_batch_shrink_factor(value) == float(value)


@pytest.mark.parametrize(
    "value",
    [0, 0.0, -0.5, 1, 1.0, 1.5, 2, float("nan"), float("inf"), "abc", "", True, [0.5]],
)
def test_invalid_factor_is_an_error_not_a_silent_default(value):
    with pytest.raises(ValueError, match="batch_shrink_factor"):
        validate_batch_shrink_factor(value)
    with pytest.raises(RuntimeError, match="batch_shrink_factor"):
        main._batch_shrink_factor({"batch_shrink_factor": value})


def _runtime(lm_cfg: dict):
    items = [{"path": str(i), "text": "x"} for i in range(100)]
    runtime = SimpleNamespace(
        lm_cfg=lm_cfg,
        batch_size=100,
        batch_profile="patch",
        remaining_items=list(items),
        remaining_item_ids=[str(i) for i in range(100)],
        all_results=[],
        contract_errors=[],
        pending_contract_event=None,
    )
    return runtime, items


def test_shrink_uses_factor_and_never_enlarges_or_fills_source_on_bad_config():
    runtime, items = _runtime({"batch_shrink_factor": 0.5, "min_batch_size": 10})
    round_data = SimpleNamespace(current_batch=items, id_to_item={})
    main._apply_batch_shrink(runtime, round_data, main._BatchRoundOutcome())
    assert runtime.batch_size == 50
    assert runtime.all_results == []

    for bad in (0, 1.5):
        runtime, items = _runtime({"batch_shrink_factor": bad})
        round_data = SimpleNamespace(current_batch=items, id_to_item={})
        with pytest.raises(RuntimeError, match="batch_shrink_factor"):
            main._apply_batch_shrink(runtime, round_data, main._BatchRoundOutcome())
        assert runtime.batch_size == 100  # 沒有被擴大
        assert runtime.all_results == []  # 沒有把原文回填成已處理


def test_runtime_build_rejects_invalid_factor_before_any_request(monkeypatch):
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {"lm_translator": {"batch_shrink_factor": 0}},
    )
    with pytest.raises(RuntimeError, match="batch_shrink_factor"):
        main._build_batch_runtime([{"path": "a", "text": "x"}], 1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 5.0),
        (0, 0.0),
        (2.5, 2.5),
        ("7", 7.0),
        (main.HIDDEN_WAIT_SEC_MAX, main.HIDDEN_WAIT_SEC_MAX),
        (-1, 5.0),
        (main.HIDDEN_WAIT_SEC_MAX + 1, 5.0),
        (float("nan"), 5.0),
        (float("inf"), 5.0),
        ("abc", 5.0),
        (True, 5.0),
    ],
)
def test_hidden_wait_settings_have_explicit_bounds(raw, expected):
    cfg = {} if raw is None else {"key_rotation_buffer_sec": raw}
    assert main._hidden_wait_sec(cfg, "key_rotation_buffer_sec", 5.0) == expected
