"""tests/test_key_rotation_and_status.py

整理 PR：
- #66 H-1：rotate_api_key 回傳 bool（False = 已無可用 Key），呼叫端必須檢查回傳值
- #92：MODEL_POOL 為空時提早結束
- _process_output：空結果不可洗掉 FAILED / PARTIAL / ALL_KEYS_EXHAUSTED 等狀態
- #67 M-1：export_cache_only 參數已移除
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from translation_tool.core import lm_translator_main as main

_OK_JSON = '{"items": [{"id": "0", "value": "你好"}]}'


def _http_error(status: int, message: str = "error", api_status: str = "") -> Exception:
    resp = Mock()
    resp.status_code = status
    resp.text = message
    resp.json.return_value = {
        "error": {"message": message, "status": api_status, "details": []}
    }
    return requests.HTTPError(f"{status} {message}", response=resp)


def _config(models: dict[str, bool]) -> dict:
    return {
        "lm_translator": {
            "initial_batch_size_lang": 300,
            "initial_batch_size_patchouli": 100,
            "batch_shrink_factor": 0.75,
            "min_batch_size": 50,
            "models": {name: {"enabled": on} for name, on in models.items()},
            "temperature": 0.2,
            "lang_system_prompt": "test",
            "patchouli_system_prompt": "test",
        }
    }


def _items() -> list[dict]:
    return [{"path": "k", "text": "Hello", "cache_type": "lang"}]


@pytest.fixture
def env():
    """共用 mock：設定 / key / API / 等待 / 換 key。回傳 (config_mock, api_mock, rotate_mock)。"""
    with (
        patch.object(main, "load_config") as cfg,
        patch.object(main, "get_current_api_key", return_value="k"),
        patch.object(main, "get_current_key_index", return_value=0),
        patch.object(main, "call_gemini_requests") as api,
        patch.object(main, "interruptible_sleep"),
        patch.object(main, "rotate_api_key") as rotate,
    ):
        cfg.return_value = _config({"m1": True, "m2": True})
        yield cfg, api, rotate


# ---------------------------------------------------------------------------
# _process_output：保留狀態
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("results", "status"),
    [
        (None, "ALL_KEYS_EXHAUSTED"),
        ([], "FAILED"),
        ([], "PARTIAL"),
        ([], "DRY_RUN"),
        ([], "AUTO"),
    ],
)
def test_process_output_keeps_status_for_empty_results(results, status):
    assert main._process_output(results, status) == ([], status)


def test_process_output_keeps_results_and_status():
    items = [{"path": "a", "text": "x"}]
    assert main._process_output(items, "PARTIAL") == (items, "PARTIAL")


def test_all_keys_exhausted_status_reaches_the_caller(env):
    """429（未知配額類型）且沒有下一把 Key：status 必須是 ALL_KEYS_EXHAUSTED，不是 AUTO。"""
    _cfg, api, rotate = env
    api.side_effect = _http_error(429, "SOMETHING ELSE")
    rotate.return_value = False

    result, status = main.translate_batch_smart(_items(), 1)

    assert result == []
    assert status == "ALL_KEYS_EXHAUSTED"
    rotate.assert_called_once()


# ---------------------------------------------------------------------------
# MODEL_POOL 為空
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("models", [{}, {"m1": False, "m2": False}])
def test_empty_model_pool_fails_fast_without_calling_api(env, models):
    cfg, api, _rotate = env
    cfg.return_value = _config(models)

    result, status = main.translate_batch_smart(_items(), 1)

    assert result == []
    assert status == "FAILED"
    api.assert_not_called()


# ---------------------------------------------------------------------------
# rotate_api_key 回傳值必須被檢查
# ---------------------------------------------------------------------------


def test_403_with_no_more_keys_raises_clear_error(env):
    _cfg, api, rotate = env
    api.side_effect = _http_error(403, "PERMISSION_DENIED")
    rotate.return_value = False

    with pytest.raises(RuntimeError, match="所有 API Key 均無權限"):
        main.translate_batch_smart(_items(), 1)

    rotate.assert_called_once()
    assert api.call_count == 1  # 沒有再用同一把無權限的 Key 重試其他模型


def test_403_rotates_key_and_continues_when_another_key_exists(env):
    _cfg, api, rotate = env
    api.side_effect = [_http_error(403, "PERMISSION_DENIED"), _OK_JSON]
    rotate.return_value = True

    result, status = main.translate_batch_smart(_items(), 1)

    assert status == "AUTO"
    assert [r["text"] for r in result] == ["你好"]
    rotate.assert_called_once()


def test_non_overload_503_without_more_keys_falls_through_to_next_model(env):
    """沒有其他 Key 可換時，不中止：記錄後改用下一個模型重試（行為不變，但不再忽略回傳值）。"""
    _cfg, api, rotate = env
    api.side_effect = [_http_error(503, "backend unavailable", "UNAVAILABLE"), _OK_JSON]
    rotate.return_value = False

    result, status = main.translate_batch_smart(_items(), 1)

    assert status == "AUTO"
    assert [r["text"] for r in result] == ["你好"]
    rotate.assert_called_once()
    assert api.call_count == 2


# ---------------------------------------------------------------------------
# M-1：export_cache_only 已移除
# ---------------------------------------------------------------------------


def test_export_cache_only_parameter_is_removed(env):
    with pytest.raises(TypeError):
        main.translate_batch_smart(_items(), 1, export_cache_only=True)  # type: ignore[call-arg]


def test_dry_run_still_skips_api_and_keeps_status_shape(env):
    _cfg, api, _rotate = env

    result, status = main.translate_batch_smart(_items(), 1, dry_run=True)

    assert result == []
    assert status == "DRY_RUN"
    api.assert_not_called()
