"""translation_tool/core/lm_key_health.py 模組。

用途：記住「已確定當天配額用盡 / 無權限」的結果，讓後續批次不再白打一次注定失敗的請求
（issue #113）。同一份狀態也提供給 UI 顯示每把 key 的健康度。

兩層狀態（同專案模式：所有 API Key 屬於同一個 Google 專案）：
- ``KeyHealthRegistry``：以 **key** 為單位，記 403 無權限。
- ``ModelQuotaRegistry``：以 **模型** 為單位，記 RPD 耗盡。RPD 配額算在「專案 × 模型」，
  換 key 拿不到額度，所以耗盡的是模型，不是 key；到下一個太平洋時間午夜才恢復。

設計重點：
- **只記兩種失敗**：RPD 耗盡（429 PERDAY / DAILY）與 403 無權限。429 RPM、503 overload 是暫時性的，
  不會被記錄。（目前流程中 RPD 只記在模型層級；``KeyHealthRegistry`` 的 ``rpd`` 原因保留為
  通用能力，翻譯流程不再使用。）
- **固定冷卻，到期再給一次機會**：冷卻秒數由設定 ``lm_translator.key_failure_cooldown_sec`` 決定
  （預設 3600；0 = 不記憶，回到每批都重試的舊行為）。不依賴時區資料庫。
- **以 key 的雜湊識別，不是 index**：設定檔增減或調換 key 順序後狀態仍對得上；
  狀態與日誌都不會出現原始 key（只有遮罩後的字串）。
- **執行緒安全**：多個翻譯執行緒共用同一份狀態，不影響 ``claim_api_key`` 的並發分散（ATK-009）。

維護注意：本模組只管「誰在冷卻」，不決定要領哪一把 key；挑選邏輯在 ``lm_config_rules``。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from datetime import time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 失敗原因
REASON_RPD = "rpd"  # 每日配額用盡
REASON_FORBIDDEN = "forbidden"  # 403 無權限
RECORDED_REASONS = frozenset({REASON_RPD, REASON_FORBIDDEN})

# UI 顯示用的狀態
STATUS_OK = "ok"  # 沒有失敗紀錄
STATUS_COOLING = "cooling"  # 冷卻中，不會被領取
STATUS_PROBING = "probing"  # 冷卻已到期、尚未確認恢復：下一次會再給它一次機會

DEFAULT_COOLDOWN_SEC = 3600.0


def key_fingerprint(key: str) -> str:
    """key 的短雜湊（用來當狀態的識別，不可還原成 key）。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def mask_key(key: str) -> str:
    """遮罩後的 key（只留前 4 與後 4 個字元），給日誌與 UI 顯示。"""
    if len(key) < 12:
        return "••••"
    return f"{key[:4]}••••{key[-4:]}"


@dataclass(frozen=True)
class KeyHealth:
    """單一 key 的健康狀態快照（給 UI 讀取）。"""

    index: int
    masked: str
    status: str
    reason: str | None
    seconds_remaining: float
    failures: int


@dataclass
class _Record:
    reason: str
    since: float
    until: float
    failures: int


class KeyHealthRegistry:
    """已確定失敗的 key 的冷卻狀態（執行緒安全）。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}

    def now(self) -> float:
        return self._clock()

    # -- 寫入 --------------------------------------------------------------

    def mark_failed(self, key: str, reason: str, cooldown_sec: float) -> bool:
        """記錄這把 key 失敗並開始冷卻；回傳是否有記錄（冷卻 <= 0 或原因不在記錄範圍則不記）。"""
        if reason not in RECORDED_REASONS or cooldown_sec <= 0 or not key:
            return False
        now = self._clock()
        fp = key_fingerprint(key)
        with self._lock:
            previous = self._records.get(fp)
            failures = previous.failures + 1 if previous else 1
            self._records[fp] = _Record(
                reason=reason, since=now, until=now + cooldown_sec, failures=failures
            )
        return True

    def mark_ok(self, key: str) -> bool:
        """這把 key 成功了：清除失敗紀錄。回傳是否真的清掉了紀錄。"""
        if not key:
            return False
        with self._lock:
            return self._records.pop(key_fingerprint(key), None) is not None

    def clear(self) -> None:
        """清除全部紀錄（測試、或使用者想重新開始時）。"""
        with self._lock:
            self._records.clear()

    def prune(self, keys: Sequence[str]) -> None:
        """丟掉已不在目前設定檔裡的 key 的紀錄。"""
        alive = {key_fingerprint(k) for k in keys}
        with self._lock:
            for fp in [fp for fp in self._records if fp not in alive]:
                del self._records[fp]

    # -- 讀取 --------------------------------------------------------------

    def is_cooling(self, key: str) -> bool:
        """冷卻中（尚未到期）。到期後回傳 False：下一次領取會再給它一次機會。"""
        return self.seconds_remaining(key) > 0

    def seconds_remaining(self, key: str) -> float:
        with self._lock:
            record = self._records.get(key_fingerprint(key))
        if record is None:
            return 0.0
        return max(0.0, record.until - self._clock())

    def reason(self, key: str) -> str | None:
        with self._lock:
            record = self._records.get(key_fingerprint(key))
        return record.reason if record else None

    def snapshot(self, keys: Sequence[str]) -> list[KeyHealth]:
        """依設定檔順序回傳每把 key 的狀態。"""
        now = self._clock()
        with self._lock:
            records = {fp: rec for fp, rec in self._records.items()}
        result: list[KeyHealth] = []
        for index, key in enumerate(keys):
            record = records.get(key_fingerprint(key))
            if record is None:
                status, reason, remaining, failures = STATUS_OK, None, 0.0, 0
            else:
                remaining = max(0.0, record.until - now)
                status = STATUS_COOLING if remaining > 0 else STATUS_PROBING
                reason, failures = record.reason, record.failures
            result.append(
                KeyHealth(
                    index=index,
                    masked=mask_key(key),
                    status=status,
                    reason=reason,
                    seconds_remaining=remaining,
                    failures=failures,
                )
            )
        return result


_REGISTRY = KeyHealthRegistry()


def get_key_health_registry() -> KeyHealthRegistry:
    """全程式共用的 key 健康狀態。"""
    return _REGISTRY


# -- 同專案模式：每日配額（RPD）以「模型」為單位 ---------------------------------
#
# Gemini 的 RPD 配額算在「專案 × 模型」，不是單一 API Key。所有 key 都屬於同一個專案時，
# 換 key 無法取得額度，所以 RPD 耗盡要記在「模型」上：該模型到下一次配額重置前，
# 所有 key 都不再請求它。重置點是太平洋時間午夜 00:00（夏令為台灣 15:00、冬令 16:00）。

QUOTA_RESET_TZ = "America/Los_Angeles"


def _next_midnight(now: float, tz: tzinfo) -> float:
    today = datetime.fromtimestamp(now, tz).date()
    return datetime.combine(
        today + timedelta(days=1), dt_time.min, tzinfo=tz
    ).timestamp()


def quota_reset_window(now: float) -> tuple[float, float]:
    """``now`` 之後下一次配額重置的 (最早, 最晚) 時間（epoch 秒）。

    有時區資料庫時兩者相同（太平洋時間午夜，夏令時間自動處理）。沒有時區資料庫時（例如沒裝
    tzdata 的 Windows），實際重置必為 UTC-7（夏令）或 UTC-8（冬令）的午夜之一，所以回傳兩者：
    最早的時間只是「可能已恢復」，在最晚的時間之前仍須由單一探測確認（見 ``ModelQuotaRegistry``）。
    """
    try:
        tz = ZoneInfo(QUOTA_RESET_TZ)
    except ZoneInfoNotFoundError:
        candidates = [
            _next_midnight(now, timezone(timedelta(hours=hours))) for hours in (-7, -8)
        ]
        return min(candidates), max(candidates)
    reset = _next_midnight(now, tz)
    return reset, reset


def next_quota_reset(now: float) -> float:
    """``now``（epoch 秒）之後最早可能的配額重置時間（太平洋時間午夜；沒有時區資料庫時取較早者）。"""
    return quota_reset_window(now)[0]


@dataclass(frozen=True)
class ModelQuotaHealth:
    """單一模型今日配額耗盡的狀態快照（給 UI 顯示）。"""

    model: str
    seconds_remaining: float
    reset_at: float  # epoch 秒
    # 沒有時區資料庫、已過最早可能的重置時間但還沒到最晚時間：正由單一探測確認是否已恢復。
    uncertain: bool = False


DEFAULT_PROBE_INTERVAL_SEC = 600.0
# 探測租約的保底逾時：必須比探測請求可能飛行的時間（請求逾時）長，否則慢速探測期間
# 其他 worker 會搶到第二個探測；持有者異常結束又沒釋放時，也不會永遠卡住其他 worker。
PROBE_LEASE_MARGIN_SEC = 60.0
# 預設租約長度：預設請求逾時（rate_limit.timeout = 600 秒）+ 餘裕；實際使用時由呼叫端依設定傳入。
DEFAULT_PROBE_LEASE_SEC = 660.0


class ModelQuotaRegistry:
    """已確定每日配額（RPD）用盡的模型（執行緒安全）。

    耗盡的模型到太平洋午夜才算恢復，但期間每隔 ``probe_interval`` 秒會放行**一個**探測：
    ``claim(model, owner)`` 把「探測租約」發給第一個來的 owner。

    - 租約跨越整個探測流程：探測遇到 RPM／503 這類暫時性錯誤時，同一個 owner 可以重入
      （``claim`` 再次回傳 True）並正常重試，其他 worker 在租約期間一律被擋下。
    - 探測結果：成功 → ``mark_ok`` 清除紀錄；RPD → ``mark_exhausted`` 重新計時；
      其他結束方式 → ``release_owner`` 收回租約並重新計時。
    - 只有「在耗盡紀錄之後才開始」的請求成功才能清除紀錄（``mark_ok(started_at=)``），否則併發時
      「耗盡前送出、之後才完成」的請求會把較新的耗盡紀錄洗掉。
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        probe_interval: float = DEFAULT_PROBE_INTERVAL_SEC,
        lease_ttl: float = DEFAULT_PROBE_LEASE_SEC,
    ) -> None:
        self._clock = clock
        self._probe_interval = probe_interval
        self._lease_ttl = lease_ttl
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}
        self._hard_until: dict[str, float] = {}  # 紀錄真正失效的時間（>= _until）
        self._marked_at: dict[str, float] = {}
        self._next_probe: dict[str, float] = {}
        self._lease: dict[str, tuple[object, float]] = {}  # model -> (owner, 到期時間)

    def now(self) -> float:
        return self._clock()

    def mark_exhausted(
        self,
        model: str,
        until: float | None = None,
        grace_until: float | None = None,
    ) -> float:
        """記錄模型今日配額用盡（同時收回探測租約並重新計時）。預設持續到下一個太平洋午夜。

        ``grace_until``：重置時間不確定（沒有時區資料庫）時的最晚可能時間。``until`` 到了之後
        紀錄不會直接失效，而是進入「不確定視窗」：仍由單一探測確認，到 ``grace_until`` 才完全失效。
        """
        now = self._clock()
        if until is None:
            until, grace_until = quota_reset_window(now)
        hard_until = max(until, grace_until if grace_until is not None else until)
        with self._lock:
            self._until[model] = until
            self._hard_until[model] = hard_until
            self._marked_at[model] = now
            self._next_probe[model] = now + self._probe_interval
            self._lease.pop(model, None)
        return until

    def mark_ok(self, model: str, started_at: float | None = None) -> bool:
        """這個模型成功了：清除耗盡紀錄。回傳是否真的清掉了紀錄。

        ``started_at`` 是該請求的開始時間；早於耗盡紀錄的請求（耗盡前就送出）不會清除紀錄。
        """
        with self._lock:
            if model not in self._until:
                return False
            marked_at = self._marked_at.get(model)
            if (
                started_at is not None
                and marked_at is not None
                and started_at < marked_at
            ):
                return False
            for table in (
                self._until,
                self._hard_until,
                self._marked_at,
                self._next_probe,
                self._lease,
            ):
                table.pop(model, None)
            return True

    def is_exhausted(self, model: str) -> bool:
        """紀錄還有效（與 ``is_blocked`` / ``claim`` 的門控一致，含不確定視窗）。"""
        now = self._clock()
        with self._lock:
            return self._hard_until.get(model, 0.0) > now

    def _claimable_locked(self, model: str, owner: object, now: float) -> bool:
        until = self._hard_until.get(model)
        if until is None or until <= now:
            return True  # 沒耗盡（或已確定過了重置時間）
        lease = self._lease.get(model)
        if lease is not None and lease[1] > now:
            return lease[0] is owner  # 租約有效：只有持有者能（重入）使用
        return now >= self._next_probe.get(model, until)  # 輪到探測

    def is_blocked(self, model: str, owner: object = None) -> bool:
        """對 ``owner`` 而言這個模型現在不能用（耗盡且沒輪到探測，或探測租約在別人手上）。

        不消耗探測名額。``owner`` 持有租約時回傳 False。
        """
        now = self._clock()
        with self._lock:
            until = self._hard_until.get(model)
            if until is None or until <= now:
                return False
            return not self._claimable_locked(model, owner, now)

    def claim(self, model: str, owner: object, lease_sec: float | None = None) -> bool:
        """``owner`` 現在可以對這個模型送請求嗎？

        沒耗盡 → True；耗盡 → 只有輪到探測、或本來就持有租約時回傳 True（必要時發出租約）。
        ``lease_sec`` 是租約長度，必須比探測請求可能飛行的時間（請求逾時）長；預設用建構時的值。
        """
        now = self._clock()
        with self._lock:
            if not self._claimable_locked(model, owner, now):
                return False
            until = self._hard_until.get(model)
            if until is not None and until > now:
                ttl = self._lease_ttl if lease_sec is None else lease_sec
                self._lease[model] = (owner, now + ttl)
            return True

    def holds_lease(self, model: str, owner: object) -> bool:
        with self._lock:
            lease = self._lease.get(model)
            return lease is not None and lease[0] is owner

    def renew(self, model: str, owner: object, lease_sec: float) -> bool:
        """``owner`` 仍持有這個模型的探測租約時續租；租約已被收回或換人則不動。"""
        now = self._clock()
        with self._lock:
            lease = self._lease.get(model)
            if lease is None or lease[0] is not owner:
                return False
            self._lease[model] = (owner, now + lease_sec)
            return True

    @contextmanager
    def hold_probe_lease(
        self, model: str, owner: object, lease_sec: float
    ) -> Iterator[None]:
        """探測請求飛行期間持續續租（心跳），請求結束就停止。

        租約長度只是持有者異常結束時的保底；請求還活著就一直續租，不必推算請求「最久會花多久」
        （``requests`` 的 timeout 不是整段 wall-clock 上限，慢速傳輸或多位址連線都可能超過）。
        沒有持有租約（模型沒耗盡、不是探測）時不開執行緒。
        """
        if not self.holds_lease(model, owner):
            yield
            return
        stop = threading.Event()
        interval = max(lease_sec / 3, 0.05)

        def beat() -> None:
            while not stop.wait(interval):
                self.renew(model, owner, lease_sec)

        thread = threading.Thread(
            target=beat, name="probe-lease-heartbeat", daemon=True
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1.0)

    def release(self, model: str, owner: object) -> None:
        """``owner`` 放棄這個模型（改用其他模型）：收回它的探測租約並重新計時。

        只有同一個模型的 RPM／503 重試才該保留租約；放棄模型時不收回的話，同一個呼叫的後續批次
        會每批都重入租約再探測一次，而不是等下一個探測週期。
        """
        now = self._clock()
        with self._lock:
            lease = self._lease.get(model)
            if lease is not None and lease[0] is owner:
                del self._lease[model]
                if model in self._until:
                    self._next_probe[model] = now + self._probe_interval

    def release_owner(self, owner: object) -> None:
        """收回 ``owner`` 持有的所有探測租約並重新計時（探測沒有成功也沒有被判 RPD 的收尾）。"""
        now = self._clock()
        with self._lock:
            for model, (holder, _expires) in list(self._lease.items()):
                if holder is owner:
                    del self._lease[model]
                    if model in self._until:
                        self._next_probe[model] = now + self._probe_interval

    def seconds_remaining(self, model: str) -> float:
        with self._lock:
            until = self._until.get(model)
        if until is None:
            return 0.0
        return max(0.0, until - self._clock())

    def snapshot(self, models: Sequence[str]) -> list[ModelQuotaHealth]:
        """依傳入順序回傳「紀錄仍有效」的模型（沒耗盡的不列出）；與實際門控一致。

        不確定視窗內的模型（已過最早可能的重置時間、還沒到最晚時間）也會列出並標示 ``uncertain``。
        """
        now = self._clock()
        with self._lock:
            until = dict(self._until)
            hard_until = dict(self._hard_until)
        return [
            ModelQuotaHealth(
                model,
                max(0.0, until[model] - now),
                until[model],
                uncertain=until[model] <= now,
            )
            for model in models
            if hard_until.get(model, 0.0) > now
        ]

    def soonest_reset_in(self, models: Sequence[str]) -> float | None:
        """指定模型中最早恢復還要幾秒；沒有任何耗盡紀錄時回傳 None。"""
        remaining = [r for m in models if (r := self.seconds_remaining(m)) > 0]
        return min(remaining) if remaining else None

    def clear(self) -> None:
        with self._lock:
            self._until.clear()
            self._hard_until.clear()
            self._marked_at.clear()
            self._next_probe.clear()
            self._lease.clear()


_MODEL_QUOTA_REGISTRY = ModelQuotaRegistry()


def get_model_quota_registry() -> ModelQuotaRegistry:
    """全程式共用的模型每日配額狀態。"""
    return _MODEL_QUOTA_REGISTRY


def reset_key_health() -> None:
    """清除全部紀錄（測試用）。"""
    _REGISTRY.clear()
    _MODEL_QUOTA_REGISTRY.clear()
