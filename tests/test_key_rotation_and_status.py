"""tests/test_key_rotation_and_status.py

整理 PR（#112）：
- API Key rotation contract：走**真正的** KeyIndexTracker + claim_api_key + ApiKeyCycle，
  只 mock 設定檔的 key 清單與 API 回應（依實際使用的 key 決定回應）。
  不 mock get_current_api_key / rotate_api_key，才能抓到「tracker 是下一把、
  卻被當成剛才使用的那一把」這類互相衝突的 index 語意。
- MODEL_POOL 為空時提早結束
- _process_output：空結果不可洗掉 FAILED / PARTIAL / ALL_KEYS_EXHAUSTED 等狀態
- export_cache_only 參數已移除
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from unittest.mock import Mock, patch

import pytest
import requests

from translation_tool.core import lm_config_rules as rules
from translation_tool.core import lm_translator_main as main

_OK_JSON = '{"items": [{"id": "0", "value": "你好"}]}'

_RPD_BODY = {
    "error": {
        "message": "quota exceeded",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                ],
            }
        ],
    }
}


def _http_error(status: int, body: dict | None = None, text: str = "error"):
    resp = Mock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = body or {
        "error": {"message": text, "status": "", "details": []}
    }
    return requests.HTTPError(f"{status} {text}", response=resp)


def _forbidden():
    return _http_error(403, text="PERMISSION_DENIED")


def _rpd():
    return _http_error(429, _RPD_BODY, "quota exceeded")


def _unknown_quota():
    return _http_error(429, text="SOMETHING ELSE")


def _unparseable_429():
    """429 但回應不是 JSON：走「無法解析 429 JSON」的備援分支。"""
    resp = Mock()
    resp.status_code = 429
    resp.text = "quota exceeded"
    resp.json.side_effect = ValueError("not json")
    return requests.HTTPError("429 quota exceeded", response=resp)


def _overloaded():
    return _http_error(503, text="The model is overloaded. Please try again later.")


def _backend_503():
    return _http_error(503, text="backend unavailable")


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


@dataclass
class Env:
    cfg: Mock
    keys: list[str]
    outcomes: dict[str, object] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def fake_api(self, **kwargs):
        """依「實際使用的 key」決定回應；Exception 會被 raise，字串會被回傳。"""
        key = kwargs["api_key"]
        self.calls.append(key)
        outcome = self.outcomes[key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def use_models(self, *names: str) -> None:
        self.cfg.return_value = _config({name: True for name in names})


@pytest.fixture
def env() -> Iterator[Env]:
    """真正的 tracker / claim / cycle；只 mock 設定檔 key 清單與 API。"""
    state = Env(cfg=Mock(), keys=["key0", "key1"])
    state.cfg.return_value = _config({"m1": True})
    with (
        patch.object(main, "load_config", state.cfg),
        patch.object(main, "call_gemini_requests", side_effect=state.fake_api),
        patch.object(main, "interruptible_sleep"),
        patch.object(rules, "_get_all_keys", side_effect=lambda: list(state.keys)),
    ):
        rules._key_tracker.reset()
        yield state
        rules._key_tracker.reset()


def _texts(result: list[dict]) -> list[str]:
    return [r["text"] for r in result]


# ---------------------------------------------------------------------------
# 403 PERMISSION_DENIED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("models", [("m1",), ("m1", "m2")], ids=["1model", "2models"])
def test_403_first_key_denied_second_key_is_actually_used(env, models):
    """key0 → 403、key1 → 成功：必須真的用 key1，且不可宣告「所有 API Key 均無權限」。"""
    env.use_models(*models)
    env.outcomes = {"key0": _forbidden(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


@pytest.mark.parametrize("models", [("m1",), ("m1", "m2")], ids=["1model", "2models"])
def test_403_all_keys_denied_only_then_terminal_failure(env, models):
    """兩把都 403：兩把都真的被使用後才終止；沒有第三次重試、沒有回頭重複用同一把。"""
    env.use_models(*models)
    env.outcomes = {"key0": _forbidden(), "key1": _forbidden()}

    with pytest.raises(RuntimeError, match="所有 API Key 均無權限"):
        main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]


def test_403_three_keys_none_is_skipped(env):
    """舊行為：領取 key0 後 tracker 已指向 key1，rotate 再前進一次 → 下一次用 key2，key1 被跳過。"""
    env.keys = ["key0", "key1", "key2"]
    env.outcomes = {"key0": _forbidden(), "key1": _forbidden(), "key2": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key2"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_403_single_key_is_terminal_after_one_attempt(env):
    env.keys = ["only"]
    env.outcomes = {"only": _forbidden()}

    with pytest.raises(RuntimeError, match="所有 API Key 均無權限"):
        main.translate_batch_smart(_items(), 1)

    assert env.calls == ["only"]


# ---------------------------------------------------------------------------
# 429
# ---------------------------------------------------------------------------


def test_429_rpd_first_key_exhausted_second_key_succeeds(env):
    env.outcomes = {"key0": _rpd(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"  # 不可提前 ALL_KEYS_EXHAUSTED
    assert _texts(result) == ["你好"]


def test_429_rpd_all_keys_exhausted_only_after_every_key_was_tried(env):
    env.outcomes = {"key0": _rpd(), "key1": _rpd()}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert result == []
    assert status == "ALL_KEYS_EXHAUSTED"


def test_429_unknown_quota_tries_every_key_before_exhausted(env):
    env.outcomes = {"key0": _unknown_quota(), "key1": _unknown_quota()}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert result == []
    assert status == "ALL_KEYS_EXHAUSTED"


def test_429_unknown_quota_second_key_succeeds(env):
    env.outcomes = {"key0": _unknown_quota(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_429_unparseable_body_fallback_tries_every_key(env):
    env.outcomes = {"key0": _unparseable_429(), "key1": _unparseable_429()}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert result == []
    assert status == "ALL_KEYS_EXHAUSTED"


def test_429_unparseable_body_fallback_second_key_succeeds(env):
    env.outcomes = {"key0": _unparseable_429(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_429_three_keys_all_rpd_every_key_tried_in_order(env):
    env.keys = ["key0", "key1", "key2"]
    env.outcomes = {"key0": _rpd(), "key1": _rpd(), "key2": _rpd()}

    _result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key2"]
    assert status == "ALL_KEYS_EXHAUSTED"


# ---------------------------------------------------------------------------
# 503
# ---------------------------------------------------------------------------


def test_503_non_overload_single_key_falls_through_to_next_model(env):
    """只有一把 key：換 key 沒有意義，改用下一個模型（既有行為不變）。"""
    env.keys = ["only"]
    env.use_models("m1", "m2")
    outcomes = iter([_backend_503(), _OK_JSON])
    env.outcomes = {"only": None}

    def fake(**kwargs):
        env.calls.append(kwargs["api_key"])
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with patch.object(main, "call_gemini_requests", side_effect=fake):
        result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["only", "only"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_non_overload_two_keys_next_request_uses_the_other_key(env):
    env.use_models("m1", "m2")
    env.outcomes = {"key0": _backend_503(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_recovers_on_another_key(env):
    env.outcomes = {"key0": _overloaded(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_all_keys_overloaded_returns_partial_without_endless_retry(env):
    """連續 overload 達門檻才換 key；所有 key 都實際撐不住後回 PARTIAL，不會無限重試。"""
    env.outcomes = {"key0": _overloaded(), "key1": _overloaded()}

    result, status = main.translate_batch_smart(_items(), 1)

    assert result == []
    assert status == "PARTIAL"
    assert set(env.calls) == {"key0", "key1"}
    assert len(env.calls) <= 6  # 兩把 key 各達 3 次門檻就收斂


def test_503_overload_does_not_declare_exhaustion_before_trying_other_key(env):
    """只有一把 key 以外的情況下，達門檻後必須先換到沒試過的 key，而不是直接 PARTIAL。"""
    env.keys = ["key0", "key1", "key2"]
    env.outcomes = {"key0": _overloaded(), "key1": _overloaded(), "key2": _OK_JSON}

    _result, status = main.translate_batch_smart(_items(), 1)

    assert status == "AUTO"
    assert "key2" in env.calls


# ---------------------------------------------------------------------------
# 成功後重新開始新的 cycle
# ---------------------------------------------------------------------------


def test_successful_request_starts_a_new_key_cycle(env):
    """同一次翻譯呼叫內，成功會重置「已失敗的 key」；之後的批次不會因舊失敗而提前耗盡。"""
    # 第一批：key0 → RPD、key1 → 成功；第二批開始時 key0 再度可用（輪到它且成功）
    env.keys = ["key0", "key1"]
    env.outcomes = {"key0": _rpd(), "key1": _OK_JSON}
    result, status = main.translate_batch_smart(_items(), 1)
    assert status == "AUTO"

    env.calls.clear()
    env.outcomes = {"key0": _OK_JSON, "key1": _OK_JSON}
    result, status = main.translate_batch_smart(_items(), 1)

    assert status == "AUTO"
    assert _texts(result) == ["你好"]
    assert len(env.calls) == 1


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


# ---------------------------------------------------------------------------
# MODEL_POOL 為空
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("models", [{}, {"m1": False, "m2": False}])
def test_empty_model_pool_fails_fast_without_calling_api(env, models):
    env.cfg.return_value = _config(models)

    result, status = main.translate_batch_smart(_items(), 1)

    assert result == []
    assert status == "FAILED"
    assert env.calls == []  # 沒有呼叫 API、沒有縮批次、沒有把原文當成功


# ---------------------------------------------------------------------------
# M-1：export_cache_only 已移除
# ---------------------------------------------------------------------------


def test_export_cache_only_parameter_is_removed(env):
    with pytest.raises(TypeError):
        main.translate_batch_smart(_items(), 1, export_cache_only=True)  # type: ignore[call-arg]


def test_dry_run_still_skips_api_and_keeps_status_shape(env):
    result, status = main.translate_batch_smart(_items(), 1, dry_run=True)

    assert result == []
    assert status == "DRY_RUN"
    assert env.calls == []
