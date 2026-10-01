"""API Key 健康狀態：走真正的 translate_batch_smart（issue #113）。

只 mock 設定檔的 key 清單、時鐘與 API 回應（依「實際使用的 key」決定回應），
走真正的 KeyIndexTracker + claim_api_key + ApiKeyCycle + 共用的 key 健康狀態。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import Mock, patch

import pytest
import requests

from translation_tool.core import lm_config_rules as rules
from translation_tool.core import lm_translator_main as main
from translation_tool.core.lm_key_health import (
    STATUS_COOLING,
    STATUS_OK,
    get_key_health_registry,
)

OK_JSON = '{"items": [{"id": "0", "value": "你好"}]}'

RPD_BODY = {
    "error": {
        "message": "quota exceeded",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}],
            }
        ],
    }
}
RPM_BODY = {
    "error": {
        "message": "rate limited",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}
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


def rpd():
    return _http_error(429, RPD_BODY, "quota exceeded")


def rpm():
    return _http_error(429, RPM_BODY, "rate limited")


def forbidden():
    return _http_error(403, text="PERMISSION_DENIED")


def overloaded():
    return _http_error(503, text="The model is overloaded. Please try again later.")


def items(n: int) -> list[dict]:
    return [{"path": f"p{i}", "text": "Hello", "cache_type": "lang"} for i in range(n)]


class Clock:
    def __init__(self) -> None:
        self.t = 5_000_000.0

    def __call__(self) -> float:
        return self.t


@dataclass
class Env:
    keys: list[str]
    clock: Clock
    outcomes: dict[str, object] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def fake_api(self, **kwargs):
        key = kwargs["api_key"]
        self.calls.append(key)
        outcome = self.outcomes[key]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def translate(self, n: int, lang_batch: int = 1):
        cfg = {
            "lm_translator": {
                "initial_batch_size_lang": lang_batch,
                "initial_batch_size_patchouli": 100,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {"m1": {"enabled": True}},
                "temperature": 0.2,
                "lang_system_prompt": "test",
                "patchouli_system_prompt": "test",
            }
        }
        with patch.object(main, "load_config", return_value=cfg):
            return main.translate_batch_smart(items(n), n)


@pytest.fixture
def env():
    state = Env(keys=["k0", "k1", "k2"], clock=Clock())
    registry = get_key_health_registry()
    original_clock = registry._clock
    registry._clock = state.clock
    with (
        patch.object(main, "call_gemini_requests", side_effect=state.fake_api),
        patch.object(main, "interruptible_sleep"),
        patch.object(rules, "_get_all_keys", side_effect=lambda: list(state.keys)),
    ):
        rules._key_tracker.reset()
        yield state
        rules._key_tracker.reset()
    registry._clock = original_clock


def _statuses(env: Env) -> list[str]:
    return [h.status for h in rules.get_key_health_snapshot()]


def test_exhausted_key_is_requested_once_across_many_batches(env):
    """驗收：key1 → RPD，key0 / key2 成功；跑多個批次，key1 只被請求一次。"""
    env.outcomes = {"k0": OK_JSON, "k1": rpd(), "k2": OK_JSON}

    result, status = env.translate(6)

    assert status == "AUTO"
    assert len(result) == 6
    assert env.calls.count("k1") == 1
    assert _statuses(env) == [STATUS_OK, STATUS_COOLING, STATUS_OK]


def test_without_failure_memory_every_batch_would_retry_the_exhausted_key(env):
    """對照組：記憶關閉時（舊行為）key1 每輪輪到就再請求一次。"""
    env.outcomes = {"k0": OK_JSON, "k1": rpd(), "k2": OK_JSON}

    with patch.object(rules, "get_key_failure_cooldown_sec", return_value=0):
        env.translate(6)

    assert env.calls.count("k1") >= 2


def test_key_is_given_another_chance_after_the_cooldown(env):
    env.outcomes = {"k0": OK_JSON, "k1": [rpd(), OK_JSON], "k2": OK_JSON}
    env.translate(3)
    assert env.calls.count("k1") == 1
    env.calls.clear()

    env.clock.t += 3601  # 冷卻到期
    _result, status = env.translate(6)

    assert status == "AUTO"
    assert env.calls.count("k1") >= 1  # 又被輪到
    assert _statuses(env)[1] == STATUS_OK  # 成功後紀錄清除


def test_all_keys_exhausted_is_still_reported(env):
    env.outcomes = {"k0": rpd(), "k1": rpd(), "k2": rpd()}

    result, status = env.translate(3)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert sorted(env.calls) == ["k0", "k1", "k2"]  # 第一次呼叫三把都實際嘗試過


def test_all_cooling_probes_once_instead_of_going_silent(env):
    """全部 key 都在冷卻：仍會探測冷卻最快到期的那一把（一次），失敗才回報耗盡。"""
    env.outcomes = {"k0": rpd(), "k1": rpd(), "k2": rpd()}
    env.translate(3)  # 三把都標記耗盡
    env.calls.clear()

    _result, status = env.translate(3)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert len(env.calls) == 1  # 只探測一次，不是每把都再打一次


def _cool_everything(env: Env) -> None:
    env.outcomes = {"k0": rpd(), "k1": rpd(), "k2": rpd()}
    env.translate(3)  # 三把都標記耗盡
    env.calls.clear()


def test_all_cooling_probe_hit_by_rpm_retries_only_the_probe_key(env):
    """全部冷卻 → 試探某把 → 它回 RPM → 同一 cycle 重試：不得改打其他冷卻中的 key。"""
    _cool_everything(env)
    env.outcomes = {
        "k0": [rpm(), OK_JSON],
        "k1": [rpm(), OK_JSON],
        "k2": [rpm(), OK_JSON],
    }

    result, status = env.translate(1)  # 一個批次：RPM 一次、重試成功一次

    assert status == "AUTO"
    assert len(result) == 1
    probe = env.calls[0]
    assert set(env.calls) == {probe}  # 只有試探 key 被請求；沒有第二把冷卻 key
    assert env.calls.count(probe) == 2  # RPM 一次 + 重試成功一次
    assert _statuses(env).count(STATUS_COOLING) == 2  # 另外兩把仍在冷卻


def test_all_cooling_probe_rpm_then_rpd_reports_exhausted_after_one_key_only(env):
    _cool_everything(env)
    env.outcomes = {
        "k0": [rpm(), rpd()],
        "k1": [rpm(), rpd()],
        "k2": [rpm(), rpd()],
    }

    result, status = env.translate(3)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert len(set(env.calls)) == 1  # 其他兩把冷卻中的 key 一次都沒被請求
    assert len(env.calls) == 2


def test_cooling_key_is_skipped_while_a_healthy_key_exists(env):
    """一把冷卻、其餘健康：直接用健康的，不試探冷卻中的那把。"""
    env.outcomes = {"k0": rpd(), "k1": OK_JSON, "k2": OK_JSON}
    env.translate(3)  # k0 進入冷卻
    env.calls.clear()

    _result, status = env.translate(6)

    assert status == "AUTO"
    assert "k0" not in env.calls


def test_all_cooling_recovers_when_the_probe_succeeds(env):
    """使用者換了方案 / 配額重置：探測成功就恢復，不必等冷卻到期。"""
    env.outcomes = {"k0": rpd(), "k1": rpd(), "k2": rpd()}
    env.translate(3)
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.calls.clear()

    result, status = env.translate(3)

    assert status == "AUTO"
    assert len(result) == 3
    assert _statuses(env).count(STATUS_OK) >= 1


def test_forbidden_key_is_remembered_and_skipped(env):
    env.outcomes = {"k0": forbidden(), "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(6)

    assert status == "AUTO"
    assert len(result) == 6
    assert env.calls.count("k0") == 1
    assert _statuses(env)[0] == STATUS_COOLING
    assert get_key_health_registry().reason("k0") == "forbidden"


def test_rpm_limit_does_not_mark_any_key(env):
    env.outcomes = {"k0": [rpm(), OK_JSON], "k1": OK_JSON, "k2": OK_JSON}

    env.translate(4)

    assert _statuses(env) == [STATUS_OK, STATUS_OK, STATUS_OK]


def test_overload_does_not_mark_any_key(env):
    env.outcomes = {"k0": [overloaded(), OK_JSON], "k1": OK_JSON, "k2": OK_JSON}

    env.translate(4)

    assert _statuses(env) == [STATUS_OK, STATUS_OK, STATUS_OK]


def test_single_key_behaviour_is_unchanged(env):
    env.keys = ["k0"]
    env.outcomes = {"k0": rpd()}

    _result, status = env.translate(2)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert env.calls == ["k0"]


def test_config_key_removal_does_not_leave_stale_state(env):
    env.outcomes = {"k0": rpd(), "k1": OK_JSON, "k2": OK_JSON}
    env.translate(2)
    assert _statuses(env)[0] == STATUS_COOLING

    env.keys = ["k1", "k2"]  # k0 從設定檔移除

    assert _statuses(env) == [STATUS_OK, STATUS_OK]
