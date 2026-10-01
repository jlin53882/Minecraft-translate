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
from translation_tool.core.lm_config_rules import ApiKeyCycle

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


def _rpm_limited():
    """429 每分鐘限制（RPM）：只等待後重試，不代表任何一把 key 失效。"""
    body = {
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
    return _http_error(429, body, "rate limited")


def _overloaded():
    return _http_error(503, text="The model is overloaded. Please try again later.")


def _backend_503():
    return _http_error(503, text="backend unavailable")


def _config(models: dict[str, bool], lang_batch: int = 300) -> dict:
    return {
        "lm_translator": {
            "initial_batch_size_lang": lang_batch,
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
    models: list[str] = field(default_factory=list)

    def fake_api(self, **kwargs):
        """依「實際使用的 key」決定回應。

        outcomes[key] 可以是單一回應（每次都一樣），或 list：依該把 key 被使用的順序取用，
        用到最後一個後重複最後一個。Exception 會被 raise，字串會被回傳。
        """
        key = kwargs["api_key"]
        self.calls.append(key)
        self.models.append(kwargs["model_name"])
        outcome = self.outcomes[key]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
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


def _spy_mark_failed():
    """包住真正的 ApiKeyCycle.mark_failed（行為不變），記錄每次被標記失敗的 key index。"""
    marked: list[int | None] = []
    original = ApiKeyCycle.mark_failed

    def wrapper(self):
        marked.append(self.current_index)  # 呼叫前的 current_index = 被標記失敗的那把
        return original(self)

    return patch.object(ApiKeyCycle, "mark_failed", wrapper), marked


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


# 503 overload：門檻是「逐把 key」。claim 是 get-and-advance，連續請求會輪流用不同的 key，
# 所以不同 key 的 overload 不可互相累加；只有同一把 key 自己達門檻才會被標記失敗。
_T = 3  # 下面的序列都依「同一把 key 累積 3 次」設計


def test_overload_threshold_constant_matches_the_sequences_below():
    assert main.OVERLOAD_KEY_SWITCH_THRESHOLD == _T


def test_503_overload_recovers_on_another_key(env):
    env.outcomes = {"key0": _overloaded(), "key1": _OK_JSON}

    result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_across_two_keys_then_success_marks_no_key_failed(env):
    """key0 overload → key1 overload → key0 成功：沒有任何 key 被標記失敗。"""
    env.outcomes = {"key0": [_overloaded(), _OK_JSON], "key1": _overloaded()}
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key0"]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]
    assert marked == []


def test_503_overload_is_not_summed_across_keys(env):
    """舊 bug：整個 batch 共用一個計數器，不同 key 的 overload 加總到門檻就把某一把 key 淘汰。

    key0 只 overload 了 2 次、key1 1 次（合計 3 次），沒有任何一把自己達門檻 → 不可 mark_failed。
    """
    # key0 一直 overload；key1 第一次 overload、第二次成功
    env.outcomes = {"key0": _overloaded(), "key1": [_overloaded(), _OK_JSON]}
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key0", "key1"]
    assert env.calls.count("key0") == 2  # 只有 2 次 overload，沒達門檻
    assert marked == []
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_marks_only_the_key_that_reached_the_threshold(env):
    """同一把 key（key0）自己累積到門檻 → 只標記它；下一把 key 真的被使用並成功。"""
    env.outcomes = {
        "key0": _overloaded(),
        "key1": [_overloaded(), _overloaded(), _OK_JSON],
    }
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    # key0 在自己的第 3 次 overload 時才被排除；之後只剩 key1 被使用
    assert env.calls == ["key0", "key1", "key0", "key1", "key0", "key1"]
    assert marked == [0]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_partial_only_after_every_key_reached_its_own_threshold(env):
    """所有 key 各自達門檻才回 PARTIAL；不是「整體累積 N 次 overload」就結束。"""
    env.outcomes = {"key0": _overloaded(), "key1": _overloaded()}
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    assert result == []
    assert status == "PARTIAL"
    assert env.calls.count("key0") == _T
    assert env.calls.count("key1") == _T
    assert env.calls == ["key0", "key1"] * _T  # 有界，沒有無限重試
    assert marked == [0, 1]


def test_503_overload_three_keys_none_is_dropped_before_its_own_threshold(env):
    """3 把 key：key0、key1 一直 overload，key2 前兩次 overload 之後成功。

    每一把都必須用到自己的門檻次數（key2 的第 3 次請求才成功）；舊的共用計數器會在 key2
    只 overload 1 次時就把它淘汰，導致 key2 永遠等不到成功。
    """
    env.keys = ["key0", "key1", "key2"]
    env.outcomes = {
        "key0": _overloaded(),
        "key1": _overloaded(),
        "key2": [_overloaded(), _overloaded(), _OK_JSON],
    }
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key2"] * 3
    assert marked == [0, 1]
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_three_keys_third_key_succeeds_without_marking_any(env):
    env.keys = ["key0", "key1", "key2"]
    env.outcomes = {"key0": _overloaded(), "key1": _overloaded(), "key2": _OK_JSON}
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key2"]
    assert marked == []
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


def test_503_overload_keeps_model_pinned_and_retries_in_place(env):
    """真正 overload → 同一個模型原地重試（pinned）；換 key 後也不會亂跳到其他模型。"""
    env.use_models("m1", "m2")
    env.outcomes = {
        "key0": _overloaded(),
        "key1": [_overloaded(), _overloaded(), _OK_JSON],
    }

    result, status = main.translate_batch_smart(_items(), 1)

    assert status == "AUTO"
    assert _texts(result) == ["你好"]
    assert env.models == ["m1"] * len(env.calls)  # 一直是同一個模型，m2 從未被用到


def test_non_503_error_interrupts_the_overload_streak(env):
    """429 RPM（不是 503）會中斷 overload 連續紀錄：之前累積的 overload 不再計入門檻。

    中斷之後 key0 只再 overload 2 次（第 3、5 次請求），沒有達門檻，所以沒有任何 key 被標記失敗。
    若沒有 clear_overload，key0 會累積到第 1、3、5 次共 3 次而被標記失敗。
    """
    env.outcomes = {
        "key0": _overloaded(),
        "key1": [_rpm_limited(), _overloaded(), _OK_JSON],
    }
    ctx, marked = _spy_mark_failed()

    with ctx:
        result, status = main.translate_batch_smart(_items(), 1)

    assert env.calls == ["key0", "key1", "key0", "key1", "key0", "key1"]
    assert marked == []
    assert status == "AUTO"
    assert _texts(result) == ["你好"]


# ---------------------------------------------------------------------------
# 成功後重新開始新的 cycle（同一次 translate_batch_smart 呼叫、跨兩個 batch）
# ---------------------------------------------------------------------------


def test_success_resets_failed_keys_for_the_next_batch_of_the_same_call(env):
    """同一次 translate_batch_smart 處理兩個 batch。

    batch 1：key0 → 429 RPD（key0 被標記失敗）→ 改用 key1 成功 → production 必須 reset。
    batch 2：key0 重新有資格被使用，所以輪到的 key0 真的被領取並成功。

    沒有 reset 的話，key0 會一直留在失敗清單，batch 2 會跳過它改用 key1。
    （不是兩次獨立的 translate 呼叫：每次呼叫本來就會建立新的 ApiKeyCycle。）
    """
    env.cfg.return_value = _config({"m1": True}, lang_batch=1)
    env.outcomes = {"key0": [_rpd(), _OK_JSON], "key1": _OK_JSON}
    items = [
        {"path": "a", "text": "Hello", "cache_type": "lang"},
        {"path": "b", "text": "World", "cache_type": "lang"},
    ]

    result, status = main.translate_batch_smart(items, 2)

    assert env.calls == ["key0", "key1", "key0"]
    assert status == "AUTO"
    assert _texts(result) == ["你好", "你好"]


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
