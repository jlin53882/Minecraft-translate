"""lm_batch_budget：token 估算、依預算取前綴、學習式預算（issue #108）。"""

from __future__ import annotations

import pytest

from translation_tool.core import lm_batch_budget as bb
from translation_tool.core.lm_batch_budget import (
    JSON_ITEM_OVERHEAD_TOKENS,
    BatchBudgetTracker,
    BudgetConfig,
    estimate_text_tokens,
    get_tracker,
    profile_for_cache_type,
    select_batch_size,
)


def _items(n: int, text: str = "Hello world") -> list[dict]:
    return [{"path": f"p{i}", "text": text, "cache_type": "lang"} for i in range(n)]


CFG = BudgetConfig()  # 預設：輸出預算 24K、輸入預算 60K、係數 1.5


# ---------------------------------------------------------------------------
# 估算
# ---------------------------------------------------------------------------


def test_ascii_and_cjk_estimates():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("a" * 100) == pytest.approx(25.0)
    assert estimate_text_tokens("中" * 10) == pytest.approx(12.0)
    # 混合：逐字元累加
    assert estimate_text_tokens("ab中") == pytest.approx(0.5 + 1.2)


def test_non_ascii_non_cjk_counts_between():
    assert estimate_text_tokens("é") == pytest.approx(0.5)


def test_estimate_includes_json_wrapper_and_fixed_input():
    tracker = BatchBudgetTracker("lang")
    est = tracker.estimate(_items(4, "a" * 40), CFG, fixed_input_tokens=100)

    value = 4 * 10.0
    assert est.count == 4
    assert est.value_tokens == pytest.approx(value)
    assert est.input_tokens == pytest.approx(
        100 + value + 4 * JSON_ITEM_OVERHEAD_TOKENS
    )
    assert est.output_tokens == pytest.approx(
        value * 1.5 + 4 * JSON_ITEM_OVERHEAD_TOKENS
    )


def test_profile_mapping():
    assert profile_for_cache_type("lang") == "lang"
    assert profile_for_cache_type("ftbquests") == "ftb"
    assert profile_for_cache_type("patchouli") == "patch"
    assert profile_for_cache_type("kubejs") == "kubejs"
    assert profile_for_cache_type("md") == "md"
    assert profile_for_cache_type(None) == "lang"
    assert profile_for_cache_type("whatever") == "lang"


# ---------------------------------------------------------------------------
# 切批：先到者為準、只取前綴
# ---------------------------------------------------------------------------


def test_fit_count_cap_wins_for_short_items():
    tracker = BatchBudgetTracker("lang")
    assert tracker.fit(_items(500), 300, CFG) == 300


def test_fit_output_budget_wins_for_long_items():
    tracker = BatchBudgetTracker("lang")
    # 每條 2000 ASCII 字元 = 500 value token → 輸出約 500*1.5+12 = 762
    n = tracker.fit(_items(200, "a" * 2000), 300, CFG)

    assert 25 <= n <= 35
    est = tracker.estimate(_items(n, "a" * 2000), CFG)
    assert est.output_tokens <= CFG.max_output_token_budget
    # 再多一條就會超過預算
    over = tracker.estimate(_items(n + 1, "a" * 2000), CFG)
    assert over.output_tokens > CFG.max_output_token_budget


def test_fit_input_budget_can_be_the_binding_constraint():
    tracker = BatchBudgetTracker("lang")
    cfg = BudgetConfig(max_input_token_budget=1000, max_output_token_budget=10**9)

    n = tracker.fit(_items(100, "a" * 400), 100, cfg)  # 每條輸入約 112

    assert n == 8  # 8*112 = 896 ≤ 1000 < 9*112


def test_fit_fixed_input_tokens_reduce_room():
    tracker = BatchBudgetTracker("lang")
    cfg = BudgetConfig(max_input_token_budget=1000, max_output_token_budget=10**9)

    with_prompt = tracker.fit(_items(100, "a" * 400), 100, cfg, fixed_input_tokens=500)
    without = tracker.fit(_items(100, "a" * 400), 100, cfg)

    assert with_prompt < without


def test_fit_always_takes_at_least_one_item_and_handles_empty():
    tracker = BatchBudgetTracker("lang")
    huge = _items(3, "中" * 100_000)  # 單一項目就遠超預算

    assert tracker.fit(huge, 300, CFG) == 1
    assert tracker.fit([], 300, CFG) == 0


def test_fit_takes_prefix_in_order():
    """切批只看前綴：中間有長項目時在它之前停下，不會跳過去拿後面的短項目。"""
    tracker = BatchBudgetTracker("lang")
    cfg = BudgetConfig(max_output_token_budget=300)
    items = _items(5, "a" * 40) + _items(1, "a" * 4000) + _items(5, "a" * 40)

    n = tracker.fit(items, 100, cfg)

    assert n == 5  # 第 6 條（長項目）放不下，不會跳過它


# ---------------------------------------------------------------------------
# 學習：撞牆減半、持續保存、緩慢回升
# ---------------------------------------------------------------------------


def test_truncation_halves_output_budget_and_shrinks_next_fit():
    tracker = BatchBudgetTracker("lang")
    items = _items(200, "a" * 2000)
    before = tracker.fit(items, 300, CFG)

    assert tracker.on_truncated("MAX_TOKENS") is True
    after = tracker.fit(items, 300, CFG)

    assert tracker.scale == pytest.approx(0.5)
    assert after < before
    assert after == pytest.approx(before / 2, abs=1.5)


def test_scale_never_goes_below_min_scale_in_effective_budget():
    tracker = BatchBudgetTracker("lang")
    cfg = BudgetConfig(min_scale=0.25)

    for _ in range(10):
        tracker.on_truncated("MAX_TOKENS")

    out_budget, _ = tracker.effective_budgets(cfg)
    assert out_budget == pytest.approx(cfg.max_output_token_budget * 0.25)


def test_stop_finish_reason_does_not_shrink_budget():
    """模型正常結束（STOP）卻產出壞 JSON：不是大小問題，不學習、交給項目數縮小。"""
    tracker = BatchBudgetTracker("lang")

    assert tracker.on_truncated("STOP") is False
    assert tracker.scale == 1.0


def test_unknown_finish_reason_is_treated_as_truncation():
    tracker = BatchBudgetTracker("lang")

    assert tracker.on_truncated(None) is True
    assert tracker.scale == pytest.approx(0.5)


def test_missing_items_only_learn_on_explicit_max_tokens():
    tracker = BatchBudgetTracker("lang")

    assert tracker.on_truncated("STOP", kind="missing") is False
    assert tracker.on_truncated(None, kind="missing") is False
    assert tracker.scale == 1.0
    assert tracker.on_truncated("MAX_TOKENS", kind="missing") is True
    assert tracker.scale == pytest.approx(0.5)


def test_budget_recovers_slowly_after_consecutive_successes():
    tracker = BatchBudgetTracker("lang")
    cfg = BudgetConfig(recover_after=3, recover_factor=2.0)
    tracker.on_truncated("MAX_TOKENS")
    tracker.on_truncated("MAX_TOKENS")  # scale 0.25

    def ok():
        tracker.on_success(cfg, value_tokens=100, item_count=10)

    ok()
    ok()
    assert tracker.scale == pytest.approx(0.25)  # 還沒連續 3 次成功
    ok()
    assert tracker.scale == pytest.approx(0.5)
    ok()
    ok()
    ok()
    assert tracker.scale == pytest.approx(1.0)
    for _ in range(10):  # 回升到頂就不再增加
        ok()
    assert tracker.scale == pytest.approx(1.0)


def test_truncation_resets_recovery_streak():
    tracker = BatchBudgetTracker("lang")
    cfg = BudgetConfig(recover_after=3, recover_factor=2.0)
    tracker.on_truncated("MAX_TOKENS")  # 0.5

    tracker.on_success(cfg, value_tokens=10, item_count=1)
    tracker.on_success(cfg, value_tokens=10, item_count=1)
    tracker.on_truncated("MAX_TOKENS")  # 0.25，連續成功歸零
    tracker.on_success(cfg, value_tokens=10, item_count=1)
    tracker.on_success(cfg, value_tokens=10, item_count=1)

    assert tracker.scale == pytest.approx(0.25)


def test_conservative_budget_does_not_keep_batches_small_forever():
    """預算過保守時不會讓批次無謂變小：連續成功後批次大小會回到原本。"""
    tracker = BatchBudgetTracker("lang")
    items = _items(300, "a" * 2000)
    baseline = tracker.fit(items, 300, CFG)
    tracker.on_truncated("MAX_TOKENS")
    shrunk = tracker.fit(items, 300, CFG)
    assert shrunk < baseline

    for _ in range(20):
        tracker.on_success(CFG, value_tokens=1000, item_count=shrunk)

    assert tracker.fit(items, 300, CFG) == baseline


def test_output_factor_calibrates_from_actual_usage():
    tracker = BatchBudgetTracker("lang")
    assert tracker.output_factor(CFG) == pytest.approx(1.5)

    # 100 條、value 1000 token；實際輸出 = 100*12 + 1000*3.0 → 觀測係數 3.0
    for _ in range(30):
        tracker.on_success(
            CFG, value_tokens=1000, item_count=100, actual_output_tokens=100 * 12 + 3000
        )

    assert tracker.output_factor(CFG) == pytest.approx(3.0, abs=0.05)
    # 係數變大 → 同樣的項目會被切成更小的批次
    fresh = BatchBudgetTracker("lang")
    items = _items(200, "a" * 2000)
    assert tracker.fit(items, 300, CFG) < fresh.fit(items, 300, CFG)


def test_calibration_is_clamped_and_ignores_missing_usage():
    tracker = BatchBudgetTracker("lang")
    tracker.on_success(CFG, value_tokens=1000, item_count=1, actual_output_tokens=None)
    assert tracker.output_factor(CFG) == pytest.approx(1.5)

    for _ in range(100):  # 荒謬的觀測值也不能讓係數失控
        tracker.on_success(
            CFG, value_tokens=10, item_count=1, actual_output_tokens=10**7
        )
    assert tracker.output_factor(CFG) <= bb.MAX_OUTPUT_FACTOR


# ---------------------------------------------------------------------------
# 登錄：依 profile 保存、跨呼叫保留
# ---------------------------------------------------------------------------


def test_registry_keeps_state_across_calls_and_isolates_profiles():
    get_tracker("lang").on_truncated("MAX_TOKENS")

    assert get_tracker("lang") is get_tracker("lang")
    assert get_tracker("lang").scale == pytest.approx(0.5)
    assert get_tracker("ftb").scale == 1.0


def test_select_batch_size_uses_learned_budget_across_calls():
    items = _items(200, "a" * 2000)
    first = select_batch_size(items, "lang", 300, {})
    get_tracker("lang").on_truncated("MAX_TOKENS")

    second = select_batch_size(items, "lang", 300, {})

    assert second < first


def test_select_batch_size_disabled_matches_old_count_only_behaviour():
    items = _items(500, "a" * 4000)

    assert select_batch_size(items, "lang", 300, {"token_budget_enabled": False}) == 300
    assert (
        select_batch_size(items, "lang", 1000, {"token_budget_enabled": False}) == 500
    )
    assert select_batch_size([], "lang", 300, {}) == 0


# ---------------------------------------------------------------------------
# 設定讀取
# ---------------------------------------------------------------------------


def test_budget_config_reads_values_and_falls_back_on_bad_ones():
    cfg = BudgetConfig.from_config(
        {
            "max_output_token_budget": 1000,
            "max_input_token_budget": "abc",  # 型別錯 → 預設
            "max_output_tokens": -5,  # 不合法 → 預設
            "output_token_factor": 99,  # 超出範圍 → 預設
            "budget_min_scale": 0.5,
            "budget_recover_after": 0,  # 不合法 → 預設
            "budget_recover_factor": 1.0,  # 必須 > 1 → 預設
            "token_budget_enabled": False,
        }
    )

    d = BudgetConfig()
    assert cfg.max_output_token_budget == 1000
    assert cfg.max_input_token_budget == d.max_input_token_budget
    assert cfg.max_output_tokens == d.max_output_tokens
    assert cfg.output_token_factor == d.output_token_factor
    assert cfg.min_scale == 0.5
    assert cfg.recover_after == d.recover_after
    assert cfg.recover_factor == d.recover_factor
    assert cfg.enabled is False


def test_budget_config_defaults_when_missing():
    assert BudgetConfig.from_config(None) == BudgetConfig()
    assert BudgetConfig.from_config({}) == BudgetConfig()
