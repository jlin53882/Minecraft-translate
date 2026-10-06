"""API Key 健康狀態（issue #113）：registry、ApiKeyCycle 的冷卻 / 試探，以及設定。"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from translation_tool.core import lm_config_rules as rules
from translation_tool.core.lm_config_rules import ApiKeyCycle, get_key_health_snapshot
from translation_tool.core.lm_key_health import (
    REASON_FORBIDDEN,
    REASON_RPD,
    STATUS_COOLING,
    STATUS_OK,
    STATUS_PROBING,
    KeyHealthRegistry,
    get_key_health_registry,
    key_fingerprint,
    mask_key,
)
from translation_tool.utils.config_manager import (
    DEFAULT_CONFIG,
    ConfigValidationError,
    _validate_lm_translator_config,
)

KEY_A = "AIzaSyA_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
KEY_B = "AIzaSyB_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
KEY_C = "AIzaSyC_cccccccccccccccccccccccccccccccccc"


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_mark_failed_starts_a_cooldown_and_expires():
    clock = Clock()
    reg = KeyHealthRegistry(clock)

    assert reg.mark_failed(KEY_A, REASON_RPD, 600) is True

    assert reg.is_cooling(KEY_A)
    assert reg.seconds_remaining(KEY_A) == pytest.approx(600)
    clock.advance(599)
    assert reg.is_cooling(KEY_A)
    clock.advance(2)
    assert not reg.is_cooling(KEY_A)  # 到期：下一次領取會再給它一次機會
    assert reg.seconds_remaining(KEY_A) == 0


def test_only_rpd_and_forbidden_are_recorded():
    reg = KeyHealthRegistry(Clock())

    assert reg.mark_failed(KEY_A, "rpm", 600) is False
    assert reg.mark_failed(KEY_A, "overload", 600) is False
    assert reg.mark_failed(KEY_A, "", 600) is False
    assert not reg.is_cooling(KEY_A)
    assert reg.mark_failed(KEY_A, REASON_FORBIDDEN, 600) is True


def test_zero_or_negative_cooldown_disables_memory():
    reg = KeyHealthRegistry(Clock())

    assert reg.mark_failed(KEY_A, REASON_RPD, 0) is False
    assert reg.mark_failed(KEY_A, REASON_RPD, -5) is False
    assert not reg.is_cooling(KEY_A)


def test_mark_ok_clears_the_record():
    reg = KeyHealthRegistry(Clock())
    reg.mark_failed(KEY_A, REASON_RPD, 600)

    assert reg.mark_ok(KEY_A) is True
    assert not reg.is_cooling(KEY_A)
    assert reg.mark_ok(KEY_A) is False  # 本來就沒有紀錄


def test_repeated_failures_increase_the_failure_count():
    clock = Clock()
    reg = KeyHealthRegistry(clock)
    reg.mark_failed(KEY_A, REASON_RPD, 60)
    clock.advance(61)
    reg.mark_failed(KEY_A, REASON_RPD, 60)

    assert reg.snapshot([KEY_A])[0].failures == 2


def test_snapshot_follows_config_order_and_reports_status():
    clock = Clock()
    reg = KeyHealthRegistry(clock)
    reg.mark_failed(KEY_B, REASON_RPD, 600)
    reg.mark_failed(KEY_C, REASON_FORBIDDEN, 100)
    clock.advance(200)  # KEY_C 冷卻已到期

    snap = reg.snapshot([KEY_A, KEY_B, KEY_C])

    assert [h.index for h in snap] == [0, 1, 2]
    assert [h.status for h in snap] == [STATUS_OK, STATUS_COOLING, STATUS_PROBING]
    assert [h.reason for h in snap] == [None, REASON_RPD, REASON_FORBIDDEN]
    assert snap[1].seconds_remaining == pytest.approx(400)
    assert snap[2].seconds_remaining == 0


def test_state_is_tied_to_the_key_not_its_position():
    """設定檔調換順序或增刪 key 之後，狀態仍屬於同一把 key。"""
    reg = KeyHealthRegistry(Clock())
    reg.mark_failed(KEY_B, REASON_RPD, 600)

    reordered = reg.snapshot([KEY_C, KEY_B, KEY_A])

    assert [h.status for h in reordered] == [STATUS_OK, STATUS_COOLING, STATUS_OK]


def test_prune_drops_keys_that_left_the_config():
    reg = KeyHealthRegistry(Clock())
    reg.mark_failed(KEY_A, REASON_RPD, 600)
    reg.mark_failed(KEY_B, REASON_RPD, 600)

    reg.prune([KEY_B])

    assert not reg.is_cooling(KEY_A)
    assert reg.is_cooling(KEY_B)


def test_raw_key_never_appears_in_state_or_masks():
    reg = KeyHealthRegistry(Clock())
    reg.mark_failed(KEY_A, REASON_RPD, 600)
    health = reg.snapshot([KEY_A])[0]

    assert KEY_A not in repr(health)
    assert KEY_A not in repr(vars(reg))
    assert KEY_A not in key_fingerprint(KEY_A)
    assert mask_key(KEY_A) == f"{KEY_A[:4]}••••{KEY_A[-4:]}"
    assert mask_key("short") == "••••"


def test_registry_is_thread_safe():
    reg = KeyHealthRegistry()
    keys = [f"AIza{i:036d}" for i in range(8)]
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(300):
                key = keys[(n + i) % len(keys)]
                reg.mark_failed(key, REASON_RPD, 60)
                reg.is_cooling(key)
                reg.snapshot(keys)
                if i % 7 == 0:
                    reg.mark_ok(key)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


# ---------------------------------------------------------------------------
# ApiKeyCycle
# ---------------------------------------------------------------------------


@pytest.fixture
def keys():
    """設定檔 key 清單 + 可控時鐘 + 真正的 tracker / registry。"""
    state = {"keys": [KEY_A, KEY_B, KEY_C]}
    clock = Clock()
    registry = get_key_health_registry()
    original_clock = registry._clock
    registry._clock = clock
    with patch.object(rules, "_get_all_keys", side_effect=lambda: list(state["keys"])):
        rules._key_tracker.reset()
        state["clock"] = clock
        yield state
        rules._key_tracker.reset()
    registry._clock = original_clock


def _claim_all(cycle: ApiKeyCycle, n: int) -> list[str]:
    return [cycle.claim() for _ in range(n)]


def test_claim_skips_keys_in_cooldown(keys):
    get_key_health_registry().mark_failed(KEY_B, REASON_RPD, 600)

    claimed = _claim_all(ApiKeyCycle(), 6)

    assert KEY_B not in claimed
    assert set(claimed) == {KEY_A, KEY_C}


def test_without_any_cooldown_claims_rotate_over_all_keys(keys):
    claimed = _claim_all(ApiKeyCycle(), 6)

    assert claimed == [KEY_A, KEY_B, KEY_C, KEY_A, KEY_B, KEY_C]


def test_mark_failed_with_rpd_records_the_key_once_per_cooldown(keys):
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_A

    assert cycle.mark_failed(reason=REASON_RPD) is True

    assert get_key_health_registry().is_cooling(KEY_A)
    assert get_key_health_snapshot()[0].status == STATUS_COOLING


def test_mark_failed_with_forbidden_is_recorded(keys):
    cycle = ApiKeyCycle()
    cycle.claim()

    cycle.mark_failed(reason=REASON_FORBIDDEN)

    assert get_key_health_registry().reason(KEY_A) == REASON_FORBIDDEN


@pytest.mark.parametrize("reason", [None, "rpm", "overload"])
def test_transient_failures_are_never_recorded(keys, reason):
    cycle = ApiKeyCycle()
    cycle.claim()

    cycle.mark_failed(reason=reason)

    assert not get_key_health_registry().is_cooling(KEY_A)


def test_cooldown_setting_zero_disables_memory(keys):
    cycle = ApiKeyCycle()
    cycle.claim()

    with patch.object(rules, "get_key_failure_cooldown_sec", return_value=0):
        cycle.mark_failed(reason=REASON_RPD)

    assert not get_key_health_registry().is_cooling(KEY_A)


def test_a_failed_key_is_used_again_after_the_cooldown_expires(keys):
    cycle = ApiKeyCycle()
    cycle.claim()
    cycle.mark_failed(reason=REASON_RPD)  # KEY_A 冷卻 3600 秒
    cycle.reset()
    assert KEY_A not in _claim_all(cycle, 4)

    keys["clock"].advance(3601)

    assert KEY_A in _claim_all(ApiKeyCycle(), 3)  # 不會永久排除
    assert get_key_health_snapshot()[0].status == STATUS_PROBING


def test_success_clears_the_record_of_the_key_that_worked(keys):
    registry = get_key_health_registry()
    registry.mark_failed(KEY_A, REASON_RPD, 100)
    keys["clock"].advance(101)  # 冷卻到期，等待確認恢復
    cycle = ApiKeyCycle()
    cycle.claim()
    assert cycle.current_index == 0

    cycle.record_success()

    assert get_key_health_snapshot()[0].status == STATUS_OK


def test_reset_alone_does_not_clear_the_shared_record(keys):
    cycle = ApiKeyCycle()
    cycle.claim()
    cycle.mark_failed(reason=REASON_RPD)

    cycle.reset()

    assert get_key_health_registry().is_cooling(KEY_A)


def test_all_keys_cooling_probes_the_soonest_to_expire_once(keys):
    reg = get_key_health_registry()
    reg.mark_failed(KEY_A, REASON_RPD, 3000)
    reg.mark_failed(KEY_B, REASON_RPD, 1000)  # 最快到期
    reg.mark_failed(KEY_C, REASON_RPD, 2000)
    cycle = ApiKeyCycle()

    first = cycle.claim()

    assert first == KEY_B
    # 試探失敗 → 這個 cycle 已試探過，不再試探其他 key：耗盡
    assert cycle.mark_failed(reason=REASON_RPD) is False


def test_probe_success_recovers_the_key(keys):
    reg = get_key_health_registry()
    for key in (KEY_A, KEY_B, KEY_C):
        reg.mark_failed(key, REASON_RPD, 1000)
    cycle = ApiKeyCycle()
    cycle.claim()

    cycle.record_success()

    assert [h.status for h in get_key_health_snapshot()].count(STATUS_OK) == 1


def test_probe_is_limited_to_one_per_cycle_and_resets_with_the_cycle(keys):
    reg = get_key_health_registry()
    for key in (KEY_A, KEY_B, KEY_C):
        reg.mark_failed(key, REASON_RPD, 1000)
    cycle = ApiKeyCycle()
    cycle.claim()
    assert cycle.mark_failed(reason=REASON_RPD) is False

    cycle.reset()  # 新的 cycle 可以再試探一次

    assert cycle.claim() in (KEY_A, KEY_B, KEY_C)
    assert cycle.mark_failed(reason=REASON_RPD) is False


def _all_cooling(clock_seconds=(3000, 1000, 2000)):
    """三把 key 全部冷卻；KEY_B 最快到期（會被試探）。"""
    reg = get_key_health_registry()
    for key, seconds in zip((KEY_A, KEY_B, KEY_C), clock_seconds, strict=True):
        reg.mark_failed(key, REASON_RPD, seconds)


def test_all_cooling_probe_with_transient_rpm_never_claims_other_cooling_keys(keys):
    """全部冷卻 → 試探 B → B 回 429 RPM（暫時性，不 mark_failed）→ 同一 cycle 重試。

    重試只能再用試探 key B；不可改領冷卻中的 A / C（#113 的 single-probe contract）。
    """
    _all_cooling()
    cycle = ApiKeyCycle()

    probe = cycle.claim()
    assert probe == KEY_B
    retries = [cycle.claim() for _ in range(6)]  # RPM 沒有 mark_failed，反覆重試

    assert retries == [KEY_B] * 6
    assert KEY_A not in retries and KEY_C not in retries


def test_all_cooling_probe_rpd_failure_reports_exhausted_and_hands_out_nothing(keys):
    _all_cooling()
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_B

    assert cycle.mark_failed(reason=REASON_RPD) is False  # 試探失敗 = 耗盡

    assert cycle.claim() == ""  # 之後也不會繞過冷卻去領 A / C
    assert cycle.current_index is None


def test_all_cooling_probe_success_clears_only_the_probe_key(keys):
    _all_cooling()
    reg = get_key_health_registry()
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_B
    assert cycle.claim() == KEY_B  # 先經過一次暫時性重試

    cycle.record_success()

    assert not reg.is_cooling(KEY_B)
    assert reg.is_cooling(KEY_A) and reg.is_cooling(KEY_C)


def test_cooling_key_is_not_probed_while_healthy_keys_exist(keys):
    get_key_health_registry().mark_failed(KEY_A, REASON_RPD, 600)
    cycle = ApiKeyCycle()

    claimed = _claim_all(cycle, 6)

    assert KEY_A not in claimed
    assert set(claimed) == {KEY_B, KEY_C}  # 有健康的 key 時直接用，不會試探 A


def test_healthy_key_taking_over_after_the_probe_wins(keys):
    """試探進行中，別處已證明 C 可用（例如另一個執行緒成功）：之後改領健康的 C。"""
    _all_cooling()
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_B

    get_key_health_registry().mark_ok(KEY_C)

    assert cycle.claim() == KEY_C


def test_cooldown_expiry_makes_the_key_claimable_again_after_a_probe(keys):
    _all_cooling()
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_B

    keys["clock"].advance(1500)  # B（1000 秒）與 C（2000 秒）之中，B 到期；A 還在冷卻
    claimed = {cycle.claim() for _ in range(4)}

    assert KEY_A not in claimed
    assert KEY_B in claimed


def test_probe_key_removed_from_config_does_not_fall_back_to_cooling_keys(keys):
    _all_cooling()
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_B

    keys["keys"] = [KEY_A, KEY_C]  # 試探中的 B 被使用者從設定移除

    assert cycle.claim() == ""  # 不會因此改領冷卻中的 A / C


def test_new_cycle_can_probe_again(keys):
    _all_cooling()
    cycle = ApiKeyCycle()
    assert cycle.claim() == KEY_B
    cycle.reset()  # 新的 cycle：可以再試探一次

    assert cycle.claim() == KEY_B


def test_mark_failed_is_true_while_a_healthy_key_remains(keys):
    reg = get_key_health_registry()
    reg.mark_failed(KEY_B, REASON_RPD, 1000)
    cycle = ApiKeyCycle()

    cycle.claim()  # KEY_A

    assert cycle.mark_failed(reason=REASON_RPD) is True  # KEY_C 還健康


def test_single_key_exhaustion_is_unchanged(keys):
    keys["keys"] = [KEY_A]
    cycle = ApiKeyCycle()
    cycle.claim()

    assert cycle.mark_failed(reason=REASON_RPD) is False


def test_no_keys_configured_returns_empty_string(keys):
    keys["keys"] = []

    assert ApiKeyCycle().claim() == ""


def test_snapshot_prunes_keys_that_left_the_config(keys):
    get_key_health_registry().mark_failed(KEY_A, REASON_RPD, 600)
    keys["keys"] = [KEY_B, KEY_C]

    snap = get_key_health_snapshot()

    assert [h.status for h in snap] == [STATUS_OK, STATUS_OK]
    assert not get_key_health_registry().is_cooling(KEY_A)


def test_concurrent_claims_stay_spread_across_healthy_keys(keys):
    """ATK-009：冷卻中的 key 不會被領取，其餘健康的 key 仍平均分散。"""
    get_key_health_registry().mark_failed(KEY_B, REASON_RPD, 600)
    counts = {KEY_A: 0, KEY_B: 0, KEY_C: 0}
    lock = threading.Lock()

    def worker() -> None:
        cycle = ApiKeyCycle()
        for _ in range(20):
            key = cycle.claim()
            with lock:
                counts[key] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert counts[KEY_B] == 0
    assert counts[KEY_A] + counts[KEY_C] == 200
    assert abs(counts[KEY_A] - counts[KEY_C]) <= 10


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


def test_cooldown_default_and_accessor():
    assert DEFAULT_CONFIG["lm_translator"]["key_failure_cooldown_sec"] == 3600
    with patch.object(rules, "load_config", return_value={"lm_translator": {}}):
        assert rules.get_key_failure_cooldown_sec() == 3600
    with patch.object(
        rules,
        "load_config",
        return_value={"lm_translator": {"key_failure_cooldown_sec": 120}},
    ):
        assert rules.get_key_failure_cooldown_sec() == 120
    with patch.object(
        rules,
        "load_config",
        return_value={"lm_translator": {"key_failure_cooldown_sec": "oops"}},
    ):
        assert rules.get_key_failure_cooldown_sec() == 3600


@pytest.mark.parametrize("value", [0, 1, 3600, 90.5])
def test_valid_cooldown_values(value):
    _validate_lm_translator_config({"key_failure_cooldown_sec": value})


@pytest.mark.parametrize("value", [-1, "3600", True, "abc"])
def test_invalid_cooldown_values_are_rejected(value):
    with pytest.raises(ConfigValidationError, match="key_failure_cooldown_sec"):
        _validate_lm_translator_config({"key_failure_cooldown_sec": value})


# ---------------------------------------------------------------------------
# 同專案模式：模型每日配額（RPD）與太平洋時間午夜重置
# ---------------------------------------------------------------------------


def _utc(*args: int) -> float:
    from datetime import UTC, datetime

    return datetime(*args, tzinfo=UTC).timestamp()


def test_next_quota_reset_is_pacific_midnight_in_summer():
    """夏令（PDT, UTC-7）：太平洋午夜 = UTC 07:00 = 台灣 15:00。"""
    from translation_tool.core.lm_key_health import next_quota_reset

    now = _utc(2026, 10, 6, 10, 0)  # 太平洋 10/06 03:00
    assert next_quota_reset(now) == _utc(2026, 10, 7, 7, 0)


def test_next_quota_reset_is_pacific_midnight_in_winter():
    """冬令（PST, UTC-8）：太平洋午夜 = UTC 08:00 = 台灣 16:00。"""
    from translation_tool.core.lm_key_health import next_quota_reset

    now = _utc(2026, 12, 1, 10, 0)  # 太平洋 12/01 02:00
    assert next_quota_reset(now) == _utc(2026, 12, 2, 8, 0)


def test_next_quota_reset_follows_the_dst_change():
    """2026-11-01 夏令結束：這一天有 25 小時，隔天午夜變成 PST。"""
    from translation_tool.core.lm_key_health import next_quota_reset

    now = _utc(2026, 11, 1, 7, 30)  # 太平洋 11/01 00:30 PDT
    assert next_quota_reset(now) == _utc(2026, 11, 2, 8, 0)  # 11/02 00:00 PST


def test_next_quota_reset_at_exactly_midnight_is_the_following_day():
    from translation_tool.core.lm_key_health import next_quota_reset

    now = _utc(2026, 10, 7, 7, 0)  # 太平洋 10/07 00:00:00 PDT
    assert next_quota_reset(now) == _utc(2026, 10, 8, 7, 0)


def test_next_quota_reset_falls_back_without_tzdata():
    """沒有時區資料庫時退回固定 UTC-7：不能拋例外，且不會比實際重置晚。"""
    from unittest.mock import patch
    from zoneinfo import ZoneInfoNotFoundError

    from translation_tool.core import lm_key_health as health

    with patch.object(health, "ZoneInfo", side_effect=ZoneInfoNotFoundError("x")):
        assert health.next_quota_reset(_utc(2026, 12, 1, 10, 0)) == _utc(
            2026, 12, 2, 7, 0
        )


def test_model_quota_registry_mark_expire_and_clear():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock)
    assert reg.is_exhausted("m1") is False
    assert reg.soonest_reset_in(["m1"]) is None

    reg.mark_exhausted("m1", until=clock.t + 600)
    assert reg.is_exhausted("m1") is True
    assert reg.is_exhausted("m2") is False  # 其他模型不受影響
    assert reg.soonest_reset_in(["m1", "m2"]) == 600

    clock.t += 601  # 到期
    assert reg.is_exhausted("m1") is False

    reg.mark_exhausted("m1", until=clock.t + 600)
    assert reg.mark_ok("m1") is True
    assert reg.mark_ok("m1") is False
    reg.mark_exhausted("m2", until=clock.t + 600)
    reg.clear()
    assert reg.is_exhausted("m2") is False


def test_model_quota_registry_defaults_to_the_next_pacific_midnight():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    now = _utc(2026, 10, 6, 10, 0)
    reg = ModelQuotaRegistry(lambda: now)

    assert reg.mark_exhausted("m1") == _utc(2026, 10, 7, 7, 0)


def test_model_quota_snapshot_lists_only_exhausted_models_in_order():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock)
    reg.mark_exhausted("m2", until=clock.t + 600)
    reg.mark_exhausted("m1", until=clock.t - 1)  # 已到期，不列出

    snap = reg.snapshot(["m1", "m2", "m3"])

    assert [h.model for h in snap] == ["m2"]
    assert snap[0].seconds_remaining == 600
    assert snap[0].reset_at == clock.t + 600


def test_get_model_quota_snapshot_only_covers_enabled_models():
    from unittest.mock import patch

    from translation_tool.core import lm_config_rules as rules
    from translation_tool.core.lm_key_health import get_model_quota_registry

    registry = get_model_quota_registry()
    registry.mark_exhausted("on", until=registry._clock() + 600)
    registry.mark_exhausted("off", until=registry._clock() + 600)
    cfg = {
        "lm_translator": {
            "models": {"on": {"enabled": True}, "off": {"enabled": False}}
        }
    }

    with patch.object(rules, "load_config", return_value=cfg):
        snap = rules.get_model_quota_snapshot()

    assert [h.model for h in snap] == ["on"]


def test_next_quota_reset_fallback_never_lands_later_than_the_real_reset():
    """沒有 tzdata：冬令提早探測後再遇 RPD，下一次到期仍是當天 08:00 UTC，不是隔天 07:00。"""
    from unittest.mock import patch
    from zoneinfo import ZoneInfoNotFoundError

    from translation_tool.core import lm_key_health as health

    with patch.object(health, "ZoneInfo", side_effect=ZoneInfoNotFoundError("x")):
        # 冬令 07:30 UTC（真實重置 08:00 UTC 之前的 30 分鐘）
        assert health.next_quota_reset(_utc(2026, 12, 1, 7, 30)) == _utc(
            2026, 12, 1, 8, 0
        )
        # 夏令：真實重置是 07:00 UTC，備援不得更晚
        assert health.next_quota_reset(_utc(2026, 10, 6, 10, 0)) == _utc(
            2026, 10, 7, 7, 0
        )


def test_model_quota_claim_blocks_until_the_probe_interval_then_grants_one_lease():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock, probe_interval=600)
    a, b = object(), object()

    assert reg.claim("m1", a) is True  # 沒耗盡：直接放行
    reg.mark_exhausted("m1", until=clock.t + 86_400)
    assert reg.is_blocked("m1", a) is True
    assert reg.claim("m1", a) is False

    clock.t += 601  # 輪到探測
    assert reg.is_blocked("m1", a) is False
    assert reg.claim("m1", a) is True  # a 領到探測租約
    assert reg.claim("m1", b) is False  # 租約期間其他 worker 不能再探測
    assert reg.is_blocked("m1", b) is True
    assert reg.is_blocked("m1", a) is False  # 持有者不被擋
    assert reg.is_exhausted("m1") is True  # 探測期間仍算耗盡（儀表板照常顯示）


def test_model_quota_lease_holder_can_reenter_for_transient_retries():
    """探測遇到 RPM／503 要在同一個租約內重試：持有者可重入，別人仍被擋。"""
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock, probe_interval=600)
    a, b = object(), object()
    reg.mark_exhausted("m1", until=clock.t + 86_400)
    clock.t += 601
    assert reg.claim("m1", a) is True

    clock.t += 30  # 等待 RPM 重試
    assert reg.claim("m1", a) is True
    assert reg.claim("m1", b) is False


def test_model_quota_lease_outcomes_rpd_rearms_release_rearms_ok_clears():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock, probe_interval=600)
    a, b = object(), object()
    reg.mark_exhausted("m1", until=clock.t + 86_400)
    clock.t += 601
    assert reg.claim("m1", a)

    reg.mark_exhausted("m1", until=clock.t + 86_400)  # 探測遇到 RPD：租約收回、重新計時
    assert reg.claim("m1", a) is False
    assert reg.claim("m1", b) is False

    clock.t += 601
    assert reg.claim("m1", a)
    reg.release_owner(a)  # 探測以其他方式結束：收回租約並重新計時
    assert reg.claim("m1", b) is False
    clock.t += 601
    assert reg.claim("m1", b) is True

    reg.mark_ok("m1", started_at=clock.t)  # 探測成功：整筆紀錄清除
    assert reg.claim("m1", a) is True
    assert reg.is_exhausted("m1") is False


def test_model_quota_lease_expires_if_the_holder_never_releases():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock, probe_interval=600, lease_ttl=300)
    a, b = object(), object()
    reg.mark_exhausted("m1", until=clock.t + 86_400)
    clock.t += 601
    assert reg.claim("m1", a)

    clock.t += 301  # 持有者異常結束：租約逾時，換別人探測
    assert reg.claim("m1", b) is True


def test_model_quota_mark_ok_ignores_requests_that_started_before_the_exhaustion():
    from translation_tool.core.lm_key_health import ModelQuotaRegistry

    clock = Clock()
    reg = ModelQuotaRegistry(clock)
    started_before = clock.t
    clock.t += 5
    reg.mark_exhausted("m1", until=clock.t + 3600)  # 另一個 worker 的 429

    # 耗盡「之前」送出的請求之後才完成：不能洗掉較新的耗盡紀錄
    assert reg.mark_ok("m1", started_at=started_before) is False
    assert reg.is_exhausted("m1") is True

    # 耗盡「之後」才開始的請求（探測）成功：清除
    assert reg.mark_ok("m1", started_at=clock.t) is True
    assert reg.is_exhausted("m1") is False
    assert reg.mark_ok("m1", started_at=clock.t) is False  # 沒有紀錄可清
