"""token 預算切批的整合測試：以假的 Gemini 模擬截斷（issue #108 階段 1）。

驗收重點：
- 長內容依 token 預算切批，不因固定項目數而截斷
- 截斷後縮小的預算保留到後續批次，且會緩慢回升
- 輸入 / 輸出位置對應不變：不重翻、不漏翻、順序不變
- 截斷時 log 有 finishReason 與 token 用量
- 一定會終止
"""

from __future__ import annotations

import json

import pytest

from translation_tool.core import lm_batch_budget as bb
from translation_tool.core import lm_translator_shared_loop as loop_mod
from translation_tool.core.lm_translator_main import translate_batch_smart

LONG = "a" * 2000  # 約 500 value token；預設預算下一批約 31 條


def _lm_cfg(**extra) -> dict:
    cfg = {
        "initial_batch_size_lang": 300,
        "initial_batch_size_patchouli": 100,
        "batch_shrink_factor": 0.5,
        "min_batch_size": 50,
        "models": {"m": {"enabled": True}},
        "temperature": 0.2,
        "lang_system_prompt": "p",
        "patchouli_system_prompt": "p",
    }
    cfg.update(extra)
    return {"lm_translator": cfg}


def _items(n: int, text: str = LONG) -> list[dict]:
    return [
        {
            "path": f"k{i}",
            "text": f"{text}{i}",
            "source_text": f"{text}{i}",
            "cache_type": "lang",
        }
        for i in range(n)
    ]


class FakeGemini:
    """模擬 call_gemini_requests：可指定哪幾次呼叫被截斷，並回報 finishReason / 用量。"""

    def __init__(self, *, truncate_if=None, finish="MAX_TOKENS", actual_out=None):
        self.actual_out = actual_out  # 成功回應要回報的輸出 token（None = 與估算一致）
        self.calls: list[int] = []
        self.truncate_if = truncate_if or (lambda call_no, n: False)
        self.finish = finish
        self.payload_ids: list[list[str]] = []

    def __call__(self, *, payload, meta_out=None, max_output_tokens=None, **_kw):
        n = len(payload["items"])
        self.calls.append(n)
        self.payload_ids.append([i["id"] for i in payload["items"]])
        full = json.dumps(
            {
                "items": [
                    {"id": i["id"], "value": "T:" + i["value"]}
                    for i in payload["items"]
                ]
            },
            ensure_ascii=False,
        )
        truncated = self.truncate_if(len(self.calls), n)
        if self.actual_out is not None:
            realistic = self.actual_out
        else:
            # 與預設估算一致的用量（係數 1.5），校正後係數不會偏移
            realistic = int(
                sum(len(i["value"]) * 0.25 for i in payload["items"]) * 1.5 + 12 * n
            )
        if meta_out is not None:
            meta_out.update(
                finish_reason=self.finish if truncated else "STOP",
                prompt_tokens=1234,
                candidates_tokens=32000 if truncated else realistic,
                thoughts_tokens=777 if truncated else 0,
                total_tokens=1,
            )
        return full[: len(full) // 2] if truncated else full


@pytest.fixture
def env(monkeypatch):
    """套用假設定與假 API；回傳 (run, fake_setter)。"""
    state = {"cfg": _lm_cfg(), "fake": FakeGemini()}

    def config():
        return state["cfg"]

    monkeypatch.setattr("translation_tool.core.lm_translator_main.load_config", config)
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.get_current_api_key", lambda: "k"
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.interruptible_sleep", lambda *_: None
    )
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.call_gemini_requests",
        lambda **kw: state["fake"](**kw),
    )
    return state


def _assert_translated_in_order(results, items):
    assert [r["path"] for r in results] == [i["path"] for i in items]
    assert [r["text"] for r in results] == ["T:" + i["text"] for i in items]
    assert not any(r.get("_untranslated") for r in results)


# ---------------------------------------------------------------------------
# 長內容依 token 預算切批
# ---------------------------------------------------------------------------


def test_long_items_are_split_by_token_budget_instead_of_item_count(env):
    env["fake"] = fake = FakeGemini()
    items = _items(100)

    results, status = translate_batch_smart(items, len(items))

    assert status == "AUTO"
    assert len(fake.calls) >= 3
    assert max(fake.calls) <= 35  # 遠小於項目數上限 300
    assert sum(fake.calls) == 100  # 不重翻、不漏翻
    _assert_translated_in_order(results, items)


def test_reported_usage_calibrates_the_output_factor(env):
    """實際輸出遠小於估算時，係數會往下校正，後續批次變大（但只在成功回應後）。"""
    env["fake"] = fake = FakeGemini(actual_out=500)  # 每批只回報 500 token
    items = _items(200)

    translate_batch_smart(items, len(items))

    assert fake.calls[-2] > fake.calls[0]  # 校正後批次變大
    assert bb.get_tracker("lang").output_factor(bb.BudgetConfig()) < 1.5


def test_thinking_tokens_count_toward_the_calibrated_output(env):
    """輸出額度包含思考 token：candidates 很小但思考很多時，不可把係數校正得太低。"""

    class ThinkingHeavy(FakeGemini):
        def __call__(self, **kw):
            text = super().__call__(**kw)
            meta = kw.get("meta_out")
            if meta is not None and meta.get("finish_reason") == "STOP":
                meta.update(candidates_tokens=100, thoughts_tokens=20000)
            return text

    env["fake"] = ThinkingHeavy()
    translate_batch_smart(_items(40), 40)

    assert bb.get_tracker("lang").output_factor(bb.BudgetConfig()) >= 1.5


def test_short_items_still_use_the_item_count_cap(env):
    env["fake"] = fake = FakeGemini()
    items = _items(250, text="Hi")

    results, _ = translate_batch_smart(items, len(items))

    assert fake.calls == [250]  # 短內容不被預算限制，一次送完
    _assert_translated_in_order(results, items)


def test_max_output_tokens_from_config_is_sent_to_api(env, monkeypatch):
    seen = {}

    def spy(**kw):
        seen["max_output_tokens"] = kw.get("max_output_tokens")
        return env["fake"](**kw)

    env["cfg"] = _lm_cfg(max_output_tokens=12345)
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.call_gemini_requests", spy
    )

    translate_batch_smart(_items(3, "Hi"), 3)

    assert seen["max_output_tokens"] == 12345


# ---------------------------------------------------------------------------
# 截斷後縮小並保留預算
# ---------------------------------------------------------------------------


def test_truncation_shrinks_budget_and_retries_smaller_prefix(env):
    # 超過 10 條就被截斷（MAX_TOKENS）
    env["fake"] = fake = FakeGemini(truncate_if=lambda _c, n: n > 10)
    items = _items(30)

    results, status = translate_batch_smart(items, len(items))

    assert status == "AUTO"
    assert fake.calls[0] == 30 and fake.calls[0] > fake.calls[1] > fake.calls[2]
    assert fake.calls[2] <= 10
    assert bb.get_tracker("lang").truncations >= 2
    _assert_translated_in_order(results, items)


def test_truncated_call_is_retried_with_the_same_prefix_ids(env):
    """被截斷後重送的是同一批輸入的前綴：id 由 0 起算，對應不會錯位。"""
    env["fake"] = fake = FakeGemini(truncate_if=lambda c, _n: c == 1)
    items = _items(30)

    results, _ = translate_batch_smart(items, len(items))

    assert fake.payload_ids[0] == [str(i) for i in range(30)]
    assert fake.payload_ids[1] == [str(i) for i in range(len(fake.payload_ids[1]))]
    _assert_translated_in_order(results, items)


def test_learned_budget_persists_across_calls(env):
    """第二次呼叫不必從頭再撞一次：起始批次就是學到的大小。"""
    env["fake"] = first = FakeGemini(truncate_if=lambda c, _n: c == 1)
    translate_batch_smart(_items(30), 30)
    assert first.calls[0] == 30  # 第一次從完整預算開始、被截斷
    assert bb.get_tracker("lang").scale < 1.0

    env["fake"] = second = FakeGemini()
    translate_batch_smart(_items(30), 30)

    assert second.calls[0] < 30  # 沒有從 initial 重新開始


def test_budget_recovers_over_successful_batches(env):
    """只有一次截斷：之後大量成功，預算會回升到接近設定值，不會永遠停在保守值。"""
    env["fake"] = FakeGemini(truncate_if=lambda c, _n: c == 1)
    translate_batch_smart(_items(30), 30)
    assert bb.get_tracker("lang").scale == pytest.approx(0.5)

    env["fake"] = long_run = FakeGemini()
    translate_batch_smart(_items(200), 200)

    assert bb.get_tracker("lang").scale == pytest.approx(1.0)
    assert max(long_run.calls) > min(long_run.calls)  # 批次隨預算回升而變大


def test_stop_with_broken_json_does_not_shrink_learned_budget(env):
    """finishReason=STOP 卻產出壞 JSON 不是 token 問題：走既有的項目數縮小，不動預算。"""
    env["fake"] = fake = FakeGemini(truncate_if=lambda _c, n: n > 25, finish="STOP")
    items = _items(30, text="Hi")

    results, status = translate_batch_smart(items, len(items))

    assert status == "AUTO"
    assert fake.calls == [30, 20, 10]  # 既有的項目數縮小（lang 最小 20）
    assert bb.get_tracker("lang").scale == 1.0
    assert bb.get_tracker("lang").truncations == 0
    _assert_translated_in_order(results, items)


def test_always_truncating_api_terminates_and_keeps_input_order(env):
    env["fake"] = fake = FakeGemini(truncate_if=lambda _c, _n: True)
    items = _items(50, text="Hi")

    results, status = translate_batch_smart(items, len(items))

    assert status == "AUTO"
    assert len(fake.calls) < 12  # 一定會終止，不會無限重試
    assert [r["path"] for r in results] == [i["path"] for i in items]
    assert all(r["_untranslated"] is True for r in results)
    assert [r["text"] for r in results] == [i["text"] for i in items]  # 原文回填


def test_disabling_token_budget_restores_count_only_batching(env):
    env["cfg"] = _lm_cfg(token_budget_enabled=False)
    env["fake"] = fake = FakeGemini()
    items = _items(100)

    translate_batch_smart(items, len(items))

    assert fake.calls == [100]  # 與舊版相同：只看項目數


# ---------------------------------------------------------------------------
# 階段 0：截斷診斷 log
# ---------------------------------------------------------------------------


def test_truncation_log_contains_finish_reason_and_token_usage(env, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.log_warning",
        lambda msg, *a, **k: warnings.append(str(msg)),
    )
    env["fake"] = FakeGemini(truncate_if=lambda c, _n: c == 1)

    translate_batch_smart(_items(30), 30)

    diag = [w for w in warnings if "截斷診斷" in w]
    assert diag, "截斷時應有診斷 log"
    assert "MAX_TOKENS" in diag[0]
    assert "prompt=1234" in diag[0]
    assert "output=32000" in diag[0]
    assert "thoughts=777" in diag[0]


def test_stop_truncation_log_says_it_is_not_a_token_limit(env, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.log_warning",
        lambda msg, *a, **k: warnings.append(str(msg)),
    )
    env["fake"] = FakeGemini(truncate_if=lambda c, _n: c == 1, finish="STOP")

    translate_batch_smart(_items(30, "Hi"), 30)

    diag = [w for w in warnings if "截斷診斷" in w]
    assert diag and "STOP" in diag[0] and "並非 token 上限" in diag[0]


def test_no_diagnostic_log_when_nothing_is_truncated(env, monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.log_warning",
        lambda msg, *a, **k: warnings.append(str(msg)),
    )

    translate_batch_smart(_items(10), 10)

    assert not [w for w in warnings if "截斷診斷" in w]


# ---------------------------------------------------------------------------
# 共用迴圈（外層切批）與 lm_translator_main 共用同一個估算
# ---------------------------------------------------------------------------


def _patch_loop(monkeypatch):
    monkeypatch.setattr(loop_mod, "reload_translation_cache", lambda: None)
    monkeypatch.setattr(loop_mod, "add_to_cache", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "save_translation_cache", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "load_config", lambda: _lm_cfg())


def _run_loop(items):
    batches: list[list[str]] = []

    def fake_translate(batch, _total):
        batches.append([it["path"] for it in batch])
        return [{**it, "text": "譯"} for it in batch], "AUTO"

    delivered: list[dict] = []
    res = loop_mod.translate_items_with_cache_loop(
        items,
        translate_batch_smart=fake_translate,
        sleep_seconds_between_batches=0,
        on_translated_item=delivered.append,
    )
    return res, batches, delivered


def test_shared_loop_outer_batches_follow_token_budget(monkeypatch):
    _patch_loop(monkeypatch)
    items = _items(100)

    res, batches, delivered = _run_loop(items)

    assert res.status == "DONE" and res.processed == 100
    assert len(batches) >= 3 and max(len(b) for b in batches) <= 35
    # 切片是輸入的前綴：依序、不重複、不遺漏
    assert [p for b in batches for p in b] == [i["path"] for i in items]
    assert [d["path"] for d in delivered] == [i["path"] for i in items]


def test_shared_loop_uses_budget_learned_by_translate_batch_smart(monkeypatch):
    _patch_loop(monkeypatch)
    items = _items(100)
    _, before, _ = _run_loop(items)

    bb.get_tracker("lang").on_truncated("MAX_TOKENS")
    _, after, _ = _run_loop(items)

    assert max(len(b) for b in after) < max(len(b) for b in before)


def test_shared_loop_short_items_keep_count_behaviour(monkeypatch):
    _patch_loop(monkeypatch)
    items = _items(250, text="Hi")

    res, batches, _ = _run_loop(items)

    assert res.processed == 250
    assert [len(b) for b in batches] == [250]


# ---------------------------------------------------------------------------
# maxOutputTokens 超過模型上限：給出可行動的錯誤，而不是一路縮批次後全部放棄
# ---------------------------------------------------------------------------


def test_max_output_tokens_over_model_limit_raises_actionable_error(env, monkeypatch):
    import requests

    response = requests.Response()
    response.status_code = 400
    response._content = (
        b'{"error": {"code": 400, "message": "Unable to submit request because it has '
        b'a maxOutputTokens value of 32768 but the supported range is from 1 to 8193",'
        b' "status": "INVALID_ARGUMENT"}}'
    )
    calls = []

    def boom(**_kw):
        calls.append(1)
        raise requests.HTTPError("400 " + response.text, response=response)

    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.call_gemini_requests", boom
    )

    with pytest.raises(RuntimeError, match="max_output_tokens"):
        translate_batch_smart(_items(30, "Hi"), 30)

    assert len(calls) == 1  # 不會縮批次後重試


def test_other_400_errors_still_shrink_batches(env, monkeypatch):
    import requests

    response = requests.Response()
    response.status_code = 400
    response._content = b'{"error": {"message": "payload too large"}}'

    def boom(**_kw):
        raise requests.HTTPError("400 " + response.text, response=response)

    monkeypatch.setattr(
        "translation_tool.core.lm_translator_main.call_gemini_requests", boom
    )

    results, status = translate_batch_smart(_items(30, "Hi"), 30)

    assert status == "AUTO"
    assert all(r["_untranslated"] for r in results)  # 既有行為：縮到極限後放棄該批
