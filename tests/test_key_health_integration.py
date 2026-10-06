"""API Key / 模型健康狀態：走真正的 translate_batch_smart（issue #113）。

只 mock 設定檔的 key 清單、時鐘與 API 回應（依「實際使用的 key / 模型」決定回應），
走真正的 KeyIndexTracker + claim_api_key + ApiKeyCycle + 共用的健康狀態。

同專案模式：
- 403 無權限記在「key」上（KeyHealthRegistry），冷卻中的 key 會被跳過。
- 429 RPD 記在「模型」上（ModelQuotaRegistry）：額度算在專案 × 模型，換 key 沒有用，
  所以改試下一個模型，全部模型都耗盡才結束；到太平洋時間午夜才恢復。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import Mock, patch

import pytest
import requests

from translation_tool.core import lm_config_rules as rules
from translation_tool.core import lm_translator_main as main
from translation_tool.core.lm_key_health import (
    DEFAULT_PROBE_INTERVAL_SEC,
    STATUS_COOLING,
    STATUS_OK,
    get_key_health_registry,
    get_model_quota_registry,
    next_quota_reset,
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
    model_outcomes: dict[str, object] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    model_calls: list[str] = field(default_factory=list)

    @staticmethod
    def _next(outcome):
        if isinstance(outcome, list):
            return outcome.pop(0) if len(outcome) > 1 else outcome[0]
        return outcome

    def fake_api(self, **kwargs):
        """model_outcomes[model] 優先；否則依「實際使用的 key」決定回應。"""
        key, model = kwargs["api_key"], kwargs["model_name"]
        self.calls.append(key)
        self.model_calls.append(model)
        outcome = self._next(
            self.model_outcomes[model]
            if model in self.model_outcomes
            else self.outcomes[key]
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def translate(self, n: int, lang_batch: int = 1, models: tuple = ("m1",)):
        cfg = {
            "lm_translator": {
                "initial_batch_size_lang": lang_batch,
                "initial_batch_size_patchouli": 100,
                "batch_shrink_factor": 0.75,
                "min_batch_size": 50,
                "models": {name: {"enabled": True} for name in models},
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
    quota = get_model_quota_registry()
    original_clocks = (registry._clock, quota._clock)
    registry._clock = quota._clock = state.clock
    with (
        patch.object(main, "call_gemini_requests", side_effect=state.fake_api),
        patch.object(main, "interruptible_sleep"),
        patch.object(rules, "_get_all_keys", side_effect=lambda: list(state.keys)),
    ):
        rules._key_tracker.reset()
        yield state
        rules._key_tracker.reset()
    registry._clock, quota._clock = original_clocks


def _statuses(env: Env) -> list[str]:
    return [h.status for h in rules.get_key_health_snapshot()]


NO_PERMISSION = "所有 API Key 均無權限"


# ---------------------------------------------------------------------------
# 403：記在 key 上
# ---------------------------------------------------------------------------


def test_forbidden_key_is_requested_once_across_many_batches(env):
    """key1 → 403，key0 / key2 成功；跑多個批次，key1 只被請求一次。"""
    env.outcomes = {"k0": OK_JSON, "k1": forbidden(), "k2": OK_JSON}

    result, status = env.translate(6)

    assert status == "AUTO"
    assert len(result) == 6
    assert env.calls.count("k1") == 1
    assert _statuses(env) == [STATUS_OK, STATUS_COOLING, STATUS_OK]


def test_without_failure_memory_every_batch_would_retry_the_forbidden_key(env):
    """對照組：記憶關閉時（舊行為）k1 每輪輪到就再請求一次。"""
    env.outcomes = {"k0": OK_JSON, "k1": forbidden(), "k2": OK_JSON}

    with patch.object(rules, "get_key_failure_cooldown_sec", return_value=0):
        env.translate(6)

    assert env.calls.count("k1") >= 2


def test_forbidden_key_is_given_another_chance_after_the_cooldown(env):
    env.outcomes = {"k0": OK_JSON, "k1": [forbidden(), OK_JSON], "k2": OK_JSON}
    env.translate(3)
    assert env.calls.count("k1") == 1
    env.calls.clear()

    env.clock.t += 3601  # 冷卻到期
    _result, status = env.translate(6)

    assert status == "AUTO"
    assert env.calls.count("k1") >= 1  # 又被輪到
    assert _statuses(env)[1] == STATUS_OK  # 成功後紀錄清除


def test_all_keys_forbidden_is_terminal(env):
    env.outcomes = {"k0": forbidden(), "k1": forbidden(), "k2": forbidden()}

    with pytest.raises(RuntimeError, match=NO_PERMISSION):
        env.translate(3)

    assert sorted(env.calls) == ["k0", "k1", "k2"]  # 三把都實際嘗試過


def _cool_everything(env: Env) -> None:
    env.outcomes = {"k0": forbidden(), "k1": forbidden(), "k2": forbidden()}
    with pytest.raises(RuntimeError, match=NO_PERMISSION):
        env.translate(3)  # 三把都標記無權限
    env.calls.clear()


def test_all_cooling_probes_once_instead_of_going_silent(env):
    """全部 key 都在冷卻：仍會探測冷卻最快到期的那一把（一次），失敗才終止。"""
    _cool_everything(env)

    with pytest.raises(RuntimeError, match=NO_PERMISSION):
        env.translate(3)

    assert len(env.calls) == 1  # 只探測一次，不是每把都再打一次


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


def test_all_cooling_probe_rpm_then_forbidden_stops_after_one_key_only(env):
    _cool_everything(env)
    env.outcomes = {
        "k0": [rpm(), forbidden()],
        "k1": [rpm(), forbidden()],
        "k2": [rpm(), forbidden()],
    }

    with pytest.raises(RuntimeError, match=NO_PERMISSION):
        env.translate(3)

    assert len(set(env.calls)) == 1  # 其他兩把冷卻中的 key 一次都沒被請求
    assert len(env.calls) == 2


def test_cooling_key_is_skipped_while_a_healthy_key_exists(env):
    """一把冷卻、其餘健康：直接用健康的，不試探冷卻中的那把。"""
    env.outcomes = {"k0": forbidden(), "k1": OK_JSON, "k2": OK_JSON}
    env.translate(3)  # k0 進入冷卻
    env.calls.clear()

    _result, status = env.translate(6)

    assert status == "AUTO"
    assert "k0" not in env.calls


def test_all_cooling_recovers_when_the_probe_succeeds(env):
    """使用者修好權限：探測成功就恢復，不必等冷卻到期。"""
    _cool_everything(env)
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

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


def test_config_key_removal_does_not_leave_stale_state(env):
    env.outcomes = {"k0": forbidden(), "k1": OK_JSON, "k2": OK_JSON}
    env.translate(2)
    assert _statuses(env)[0] == STATUS_COOLING

    env.keys = ["k1", "k2"]  # k0 從設定檔移除

    assert _statuses(env) == [STATUS_OK, STATUS_OK]


# ---------------------------------------------------------------------------
# 429 RPD：同專案模式，記在「模型」上，不換 key
# ---------------------------------------------------------------------------


def test_rpd_marks_the_model_not_the_key_and_never_rotates_keys(env):
    """單一模型 RPD：額度是專案共用的，其他 key 打了也一樣 429，所以只打一次就結束。"""
    env.outcomes = {"k0": rpd(), "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(2)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert env.calls == ["k0"]  # k1 / k2 一次都沒被請求
    assert _statuses(env) == [STATUS_OK, STATUS_OK, STATUS_OK]  # key 沒有被冷卻
    assert get_model_quota_registry().is_exhausted("m1")


def test_rpd_falls_through_to_next_model(env):
    env.model_outcomes = {"m1": rpd(), "m2": OK_JSON}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(1, models=("m1", "m2"))

    assert status == "AUTO"
    assert len(result) == 1
    assert env.model_calls == ["m1", "m2"]


def test_rpd_on_every_model_is_exhausted_after_each_model_tried_once(env):
    """2 個 model 都 429 RPD：每個 model 只試一次，不因換 key 而重複嘗試 → 結束。"""
    env.model_outcomes = {"m1": rpd(), "m2": rpd()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(2, models=("m1", "m2"))

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert env.model_calls == ["m1", "m2"]
    assert len(env.calls) == 2


def test_exhausted_model_is_skipped_by_following_batches(env):
    """m1 已確定今日耗盡：後續批次直接用 m2，不再白打 m1。"""
    env.model_outcomes = {"m1": rpd(), "m2": OK_JSON}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(6, models=("m1", "m2"))

    assert status == "AUTO"
    assert len(result) == 6
    assert env.model_calls.count("m1") == 1
    assert env.model_calls.count("m2") == 6


def test_no_request_is_sent_while_every_model_is_exhausted(env):
    env.model_outcomes = {"m1": rpd(), "m2": rpd()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.translate(2, models=("m1", "m2"))
    env.calls.clear()
    env.model_calls.clear()

    _result, status = env.translate(2, models=("m1", "m2"))

    assert status == "ALL_KEYS_EXHAUSTED"
    assert env.calls == []  # 重置前完全不送請求


def test_models_recover_at_the_next_pacific_midnight(env):
    env.model_outcomes = {"m1": rpd()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.translate(1)
    quota = get_model_quota_registry()
    reset_at = next_quota_reset(env.clock.t)

    env.clock.t = reset_at - 1
    assert quota.is_exhausted("m1")

    env.clock.t = reset_at + 1
    env.model_outcomes = {"m1": OK_JSON}
    env.calls.clear()
    result, status = env.translate(1)

    assert status == "AUTO"
    assert len(result) == 1
    assert len(env.calls) == 1
    assert not quota.is_exhausted("m1")


def test_expired_record_does_not_block_and_other_models_still_work(env):
    quota = get_model_quota_registry()
    quota.mark_exhausted("m1", until=env.clock.t + 3600)
    quota.mark_exhausted("m2", until=env.clock.t - 1)  # 已到期的紀錄
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(1, models=("m1", "m2"))

    assert status == "AUTO"
    assert env.model_calls == ["m2"]  # m1 仍耗盡被跳過，到期的 m2 被請求
    assert len(result) == 1


def test_exhausted_model_is_probed_after_the_interval_and_recovers_on_success(env):
    """使用者升級方案：耗盡的模型每隔一段時間探測一次，成功就立刻恢復（不必等到午夜）。"""
    quota = get_model_quota_registry()
    env.model_outcomes = {"m1": rpd(), "m2": OK_JSON}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.translate(1, models=("m1", "m2"))
    assert quota.is_exhausted("m1")
    env.model_calls.clear()

    env.translate(2, models=("m1", "m2"))  # 還沒到探測時間：完全不碰 m1
    assert "m1" not in env.model_calls

    env.clock.t += DEFAULT_PROBE_INTERVAL_SEC + 1
    env.model_outcomes = {"m1": OK_JSON, "m2": OK_JSON}  # 升級了：m1 現在有額度
    env.model_calls.clear()
    result, status = env.translate(2, models=("m1", "m2"))

    assert status == "AUTO"
    assert len(result) == 2
    assert env.model_calls[0] == "m1"  # 探測請求
    assert not quota.is_exhausted("m1")  # 探測成功：恢復
    assert env.model_calls.count("m1") == 2  # 之後的批次也恢復使用 m1


def test_failed_probe_keeps_the_model_exhausted_until_the_next_interval(env):
    quota = get_model_quota_registry()
    env.model_outcomes = {"m1": rpd(), "m2": OK_JSON}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.translate(1, models=("m1", "m2"))
    env.clock.t += DEFAULT_PROBE_INTERVAL_SEC + 1
    env.model_calls.clear()

    result, status = env.translate(3, models=("m1", "m2"))  # 探測仍是 429

    assert status == "AUTO"
    assert len(result) == 3
    assert env.model_calls.count("m1") == 1  # 只探測一次，其餘批次直接用 m2
    assert quota.is_exhausted("m1")


def test_all_models_exhausted_still_probes_once_the_interval_has_passed(env):
    env.model_outcomes = {"m1": rpd()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.translate(1)
    env.calls.clear()

    _result, status = env.translate(1)  # 還沒到探測時間：不送請求
    assert status == "ALL_KEYS_EXHAUSTED"
    assert env.calls == []

    env.clock.t += DEFAULT_PROBE_INTERVAL_SEC + 1
    env.model_outcomes = {"m1": OK_JSON}  # 額度恢復
    result, status = env.translate(1)

    assert status == "AUTO"
    assert len(result) == 1
    assert len(env.calls) == 1


def test_success_of_a_request_started_before_the_exhaustion_keeps_the_record(env):
    """併發：m1 的請求在 RPD 前送出、另一個 worker 的 429 先完成，之後該請求才成功 → 紀錄保留。"""
    quota = get_model_quota_registry()

    def api(**kwargs):
        # 這個請求「執行期間」，另一個 worker 對同一模型收到 429 並記錄耗盡
        env.clock.t += 5
        quota.mark_exhausted("m1")
        return OK_JSON

    with patch.object(main, "call_gemini_requests", side_effect=api):
        result, status = env.translate(1)

    assert status == "AUTO"
    assert len(result) == 1
    assert quota.is_exhausted("m1")  # 沒被先前送出的成功請求洗掉


def not_found():
    return _http_error(404, text="model not found")


def _exhaust_m1_and_wait_for_probe(env: Env, models=("m1",)) -> None:
    """m1 RPD 耗盡，並讓時間走到下一次探測。"""
    env.model_outcomes = {"m1": rpd()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}
    env.translate(1, models=models)
    env.clock.t += DEFAULT_PROBE_INTERVAL_SEC + 1
    env.calls.clear()
    env.model_calls.clear()


@pytest.mark.parametrize("transient", [rpm, overloaded], ids=["rpm", "overload"])
def test_probe_hit_by_a_transient_error_is_retried_on_the_same_model(env, transient):
    """探測請求遇到 RPM／503 overload：必須在同一次探測內重試，不能被自己的探測鎖擋掉。"""
    _exhaust_m1_and_wait_for_probe(env)
    env.model_outcomes = {"m1": [transient(), OK_JSON]}

    result, status = env.translate(1)

    assert status == "AUTO"
    assert len(result) == 1
    assert env.model_calls == ["m1", "m1"]  # 探測 + 重試
    assert not get_model_quota_registry().is_exhausted("m1")  # 重試成功：恢復


def test_probe_lost_to_another_worker_reports_exhausted_instead_of_shrinking(env):
    """另一個 worker 已領走探測名額：沒送任何請求，不能 SHRINK 後把原文回填成 AUTO。"""
    _exhaust_m1_and_wait_for_probe(env)
    quota = get_model_quota_registry()
    assert quota.claim("m1", object())  # 別的 worker 正在探測

    result, status = env.translate(2)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert env.calls == []


def test_probe_race_between_the_blocked_check_and_the_claim_is_exhausted(env):
    """TOCTOU：開頭的 is_blocked 檢查通過、之後 claim 才被別人搶走 → 仍是耗盡，不是 SHRINK。"""
    _exhaust_m1_and_wait_for_probe(env)
    quota = get_model_quota_registry()
    assert quota.claim("m1", object())

    with patch.object(quota, "is_blocked", return_value=False):
        result, status = env.translate(2)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert env.calls == []


def test_probe_lost_to_another_worker_falls_back_to_the_next_model(env):
    _exhaust_m1_and_wait_for_probe(env, models=("m1", "m2"))
    get_model_quota_registry().claim("m1", object())
    env.model_outcomes = {"m2": OK_JSON}

    result, status = env.translate(1, models=("m1", "m2"))

    assert status == "AUTO"
    assert len(result) == 1
    assert env.model_calls == ["m2"]


def test_pinned_model_hitting_rpd_unpins_and_falls_back_to_the_alternate(env):
    """m1 先 503 overload（被釘住重試），再遇 RPD：必須解除釘選並真的改用 m2。"""
    env.model_outcomes = {"m1": [overloaded(), rpd()], "m2": OK_JSON}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(1, models=("m1", "m2"))

    assert status == "AUTO"
    assert len(result) == 1
    assert env.model_calls == ["m1", "m1", "m2"]
    assert get_model_quota_registry().is_exhausted("m1")


def test_missing_model_then_rpd_is_terminal_not_a_shrink(env):
    """m1 不存在(404)、m2 RPD：沒有任何模型能用 → 耗盡；不能 SHRINK 後回填原文。"""
    env.model_outcomes = {"m1": not_found(), "m2": rpd()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(2, models=("m1", "m2"))

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result
    assert env.model_calls == ["m1", "m2"]  # 各只試一次；404 的 m1 不會每輪重打


def backend_503():
    return _http_error(503, text="backend unavailable")


def test_all_models_missing_is_a_terminal_failure_not_an_untranslated_auto(env):
    """整個模型池都是 404：不能 SHRINK 後把原文回填成 AUTO。"""
    env.model_outcomes = {"m1": not_found(), "m2": not_found()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(2, models=("m1", "m2"))

    assert status == "FAILED"
    assert not any(item.get("_untranslated") for item in result or [])
    assert not result
    assert env.model_calls == ["m1", "m2"]  # 各只試一次


def test_missing_model_plus_lost_probe_claim_is_exhausted(env):
    """m1 404、m2 的探測名額被別的 worker 領走：沒有任何模型能送 → 耗盡。"""
    quota = get_model_quota_registry()
    quota.mark_exhausted("m1")
    env.clock.t += DEFAULT_PROBE_INTERVAL_SEC + 1
    assert quota.claim("m1", object())  # 別的 worker 正在探測 m1
    env.model_outcomes = {"m2": not_found()}
    env.outcomes = {"k0": OK_JSON, "k1": OK_JSON, "k2": OK_JSON}

    result, status = env.translate(2, models=("m2", "m1"))

    assert status == "ALL_KEYS_EXHAUSTED"
    assert not result


@pytest.mark.parametrize("abandon", [backend_503, lambda: ""], ids=["503", "empty"])
def test_probe_lease_is_released_when_the_probe_falls_through_to_another_model(
    env, abandon
):
    """探測的模型被放棄（503 → 下一個模型、空回應）：租約收回並重新計時。

    後續批次不能每批都再探測一次，必須等下一個 10 分鐘週期。
    """
    _exhaust_m1_and_wait_for_probe(env, models=("m1", "m2"))
    env.model_outcomes = {"m1": abandon(), "m2": OK_JSON}

    result, status = env.translate(4, models=("m1", "m2"))  # 4 個批次（每批 1 筆）

    assert status == "AUTO"
    assert len(result) == 4
    assert env.model_calls.count("m1") == 1  # 只探測一次
    assert env.model_calls.count("m2") == 4


def test_slow_probe_keeps_its_lease_for_the_whole_request_timeout(env):
    """探測請求最久可飛行 rate_limit.timeout（預設 600 秒）：期間其他 worker 不能搶到第二個探測。"""
    _exhaust_m1_and_wait_for_probe(env)
    quota = get_model_quota_registry()
    seen: list[bool] = []

    def slow_api(**kwargs):
        env.clock.t += 400  # 比舊的 300 秒租約久、比請求逾時短
        seen.append(quota.claim("m1", object()))  # 另一個 worker 此時想探測
        return OK_JSON

    with patch.object(main, "call_gemini_requests", side_effect=slow_api):
        result, status = env.translate(1)

    assert status == "AUTO"
    assert len(result) == 1
    assert seen == [False]


def test_single_key_rpd_behaviour(env):
    env.keys = ["k0"]
    env.outcomes = {"k0": rpd()}

    _result, status = env.translate(2)

    assert status == "ALL_KEYS_EXHAUSTED"
    assert env.calls == ["k0"]
