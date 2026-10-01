"""tests/test_api_key_cycle.py

API Key rotation contract 的單元測試（走真正的 KeyIndexTracker）：

- claim_api_key：atomic get-and-advance，回傳「實際領到的 index + key」；
  exclude 為本輪已實際失敗的 key。
- ApiKeyCycle：耗盡 = 所有 key 都在本輪實際失敗，而不是 tracker 恰好在最後一格。
- 並發分散（ATK-009）：get-and-advance 仍讓多執行緒平均分散到不同 key。
"""

from __future__ import annotations

import threading
from collections import Counter
from unittest.mock import patch

import pytest

from translation_tool.core import lm_config_rules as rules
from translation_tool.core.lm_config_rules import (
    ApiKeyCycle,
    claim_api_key,
    get_current_api_key,
)


@pytest.fixture
def keys():
    """可由測試調整的 key 清單；每個測試前後重置 tracker。"""
    state = {"keys": ["k0", "k1", "k2"]}
    with patch.object(rules, "_get_all_keys", side_effect=lambda: list(state["keys"])):
        rules._key_tracker.reset()
        yield state
        rules._key_tracker.reset()


# ---------------------------------------------------------------------------
# claim_api_key
# ---------------------------------------------------------------------------


def test_claim_single_key_always_returns_it(keys):
    keys["keys"] = ["only"]

    assert [claim_api_key() for _ in range(3)] == [(0, "only")] * 3
    assert rules.get_current_key_index() == 0


def test_claim_two_keys_alternates(keys):
    keys["keys"] = ["k0", "k1"]

    assert [claim_api_key() for _ in range(4)] == [
        (0, "k0"),
        (1, "k1"),
        (0, "k0"),
        (1, "k1"),
    ]


def test_claim_three_keys_wraps_around(keys):
    assert [claim_api_key() for _ in range(7)] == [
        (0, "k0"),
        (1, "k1"),
        (2, "k2"),
        (0, "k0"),
        (1, "k1"),
        (2, "k2"),
        (0, "k0"),
    ]


def test_claim_leaves_tracker_pointing_at_the_next_key_not_the_one_just_used(keys):
    """contract：claim 之後 tracker 指的是「下一把」，不是剛才使用的那一把。"""
    keys["keys"] = ["k0", "k1"]

    index, _key = claim_api_key()

    assert index == 0
    assert rules.get_current_key_index() == 1


def test_claim_after_reset_starts_from_first_key(keys):
    claim_api_key()
    claim_api_key()

    rules.reset_key_index()

    assert claim_api_key() == (0, "k0")


def test_claim_exclude_skips_failed_keys_and_advances_past_the_claimed_one(keys):
    assert claim_api_key(exclude={0}) == (1, "k1")
    assert rules.get_current_key_index() == 2
    assert claim_api_key(exclude={0}) == (2, "k2")
    assert rules.get_current_key_index() == 0  # 領取 k2 後環繞回 k0
    assert claim_api_key(exclude={0}) == (1, "k1")


def test_claim_exclude_wraps_when_needed(keys):
    rules._key_tracker._index = 2  # 下一把是 k2；k2 已失敗

    assert claim_api_key(exclude={2}) == (0, "k0")


def test_claim_returns_none_when_every_key_is_excluded(keys):
    assert claim_api_key(exclude={0, 1, 2}) is None


def test_claim_returns_none_without_any_key(keys):
    keys["keys"] = []

    assert claim_api_key() is None


def test_get_current_api_key_keeps_get_and_advance_behavior(keys):
    """向後相容：行為與舊版相同（回傳字串、領取後前進、無 key 回空字串）。"""
    assert [get_current_api_key() for _ in range(4)] == ["k0", "k1", "k2", "k0"]
    keys["keys"] = []
    assert get_current_api_key() == ""


def test_claim_with_stale_index_beyond_key_count_is_clamped(keys):
    """key 清單縮短後 tracker index 可能超出範圍：用最後一把，不越界。"""
    rules._key_tracker._index = 9
    keys["keys"] = ["k0", "k1"]

    assert claim_api_key() == (1, "k1")
    assert 0 <= rules.get_current_key_index() < 2


# ---------------------------------------------------------------------------
# ApiKeyCycle：耗盡的定義
# ---------------------------------------------------------------------------


def test_cycle_two_keys_first_failure_is_not_exhaustion(keys):
    """舊 bug：領取 key0 後 tracker 已指向 key1，rotate 判成『已用盡』，key1 其實沒試過。"""
    keys["keys"] = ["k0", "k1"]
    cycle = ApiKeyCycle()

    assert cycle.claim() == "k0"
    assert cycle.mark_failed() is True  # 還有 k1 沒試過
    assert cycle.claim() == "k1"
    assert cycle.mark_failed() is False  # 兩把都實際失敗才耗盡


def test_cycle_three_keys_never_skips_a_key(keys):
    cycle = ApiKeyCycle()

    used = []
    for _ in range(3):
        used.append(cycle.claim())
        exhausted = not cycle.mark_failed()

    assert used == ["k0", "k1", "k2"]
    assert exhausted is True


def test_cycle_single_key_first_failure_is_exhaustion(keys):
    keys["keys"] = ["only"]
    cycle = ApiKeyCycle()

    cycle.claim()

    assert cycle.mark_failed() is False


def test_cycle_mark_failed_is_idempotent_for_the_same_key(keys):
    keys["keys"] = ["k0", "k1"]
    cycle = ApiKeyCycle()
    cycle.claim()

    assert cycle.mark_failed() is True
    assert cycle.mark_failed() is True  # 同一把重複標記不會算成兩把失敗
    assert cycle.failed_indexes == frozenset({0})


def test_cycle_claim_never_returns_a_failed_key_while_another_is_available(keys):
    cycle = ApiKeyCycle()
    cycle.claim()  # k0，tracker -> 1
    cycle.mark_failed()
    rules._key_tracker._index = 0  # 模擬其他執行緒讓 tracker 繞回 k0

    assert cycle.claim() != "k0"


def test_cycle_reset_starts_a_new_cycle(keys):
    keys["keys"] = ["k0", "k1"]
    cycle = ApiKeyCycle()
    cycle.claim()
    cycle.mark_failed()

    cycle.reset()

    assert cycle.failed_indexes == frozenset()
    cycle.claim()
    assert cycle.mark_failed() is True  # 新 cycle：一次失敗不再算耗盡


def test_cycle_without_keys_is_exhausted_and_claims_empty_string(keys):
    keys["keys"] = []
    cycle = ApiKeyCycle()

    assert cycle.claim() == ""
    assert cycle.current_index is None
    assert cycle.mark_failed() is False


def test_cycle_claim_falls_back_to_rotation_when_every_key_already_failed(keys):
    """呼叫端應在 mark_failed() 回傳 False 時就中止；萬一仍要 claim，不可回空字串。"""
    keys["keys"] = ["k0", "k1"]
    cycle = ApiKeyCycle()
    cycle.claim()
    cycle.mark_failed()
    cycle.claim()
    cycle.mark_failed()

    assert cycle.claim() in {"k0", "k1"}


def test_cycle_has_alternative_key(keys):
    assert ApiKeyCycle().has_alternative_key() is True
    keys["keys"] = ["only"]
    assert ApiKeyCycle().has_alternative_key() is False


# ---------------------------------------------------------------------------
# 並發分散（ATK-009）
# ---------------------------------------------------------------------------


def _claim_concurrently(count: int, **kwargs) -> list[tuple[int, str]]:
    barrier = threading.Barrier(count)
    results: list[tuple[int, str]] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        claim = claim_api_key(**kwargs)
        assert claim is not None
        with lock:
            results.append(claim)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_claims_are_distributed_evenly_across_keys(keys):
    results = _claim_concurrently(30)

    assert Counter(index for index, _ in results) == {0: 10, 1: 10, 2: 10}
    assert all(key == f"k{index}" for index, key in results)


def test_concurrent_claims_with_exclude_never_return_an_excluded_key(keys):
    results = _claim_concurrently(20, exclude={0})

    counts = Counter(index for index, _ in results)
    assert 0 not in counts
    assert set(counts) == {1, 2}
    assert counts[1] == counts[2] == 10
