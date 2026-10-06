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
from collections.abc import Callable, Sequence
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


def _quota_reset_tzinfo() -> tzinfo:
    try:
        return ZoneInfo(QUOTA_RESET_TZ)
    except ZoneInfoNotFoundError:
        # 沒有時區資料庫（例如沒裝 tzdata 的 Windows）：退回固定的 UTC-7（夏令）。
        # 冬令時會比實際重置早 1 小時重新探測一次；探測失敗只會再被記錄一次，不影響正確性。
        return timezone(timedelta(hours=-7))


def next_quota_reset(now: float) -> float:
    """``now``（epoch 秒）之後的下一個太平洋時間午夜 00:00（epoch 秒），夏令時間自動處理。"""
    tz = _quota_reset_tzinfo()
    today = datetime.fromtimestamp(now, tz).date()
    midnight = datetime.combine(today + timedelta(days=1), dt_time.min, tzinfo=tz)
    return midnight.timestamp()


class ModelQuotaRegistry:
    """已確定每日配額（RPD）用盡的模型（執行緒安全）。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}

    def mark_exhausted(self, model: str, until: float | None = None) -> float:
        """記錄模型今日配額用盡；預設持續到下一個太平洋午夜。回傳到期時間（epoch 秒）。"""
        if until is None:
            until = next_quota_reset(self._clock())
        with self._lock:
            self._until[model] = until
        return until

    def mark_ok(self, model: str) -> bool:
        """這個模型成功了：清除耗盡紀錄。回傳是否真的清掉了紀錄。"""
        with self._lock:
            return self._until.pop(model, None) is not None

    def is_exhausted(self, model: str) -> bool:
        return self.seconds_remaining(model) > 0

    def seconds_remaining(self, model: str) -> float:
        with self._lock:
            until = self._until.get(model)
        if until is None:
            return 0.0
        return max(0.0, until - self._clock())

    def soonest_reset_in(self, models: Sequence[str]) -> float | None:
        """指定模型中最早恢復還要幾秒；沒有任何耗盡紀錄時回傳 None。"""
        remaining = [r for m in models if (r := self.seconds_remaining(m)) > 0]
        return min(remaining) if remaining else None

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


_MODEL_QUOTA_REGISTRY = ModelQuotaRegistry()


def get_model_quota_registry() -> ModelQuotaRegistry:
    """全程式共用的模型每日配額狀態。"""
    return _MODEL_QUOTA_REGISTRY


def reset_key_health() -> None:
    """清除全部紀錄（測試用）。"""
    _REGISTRY.clear()
    _MODEL_QUOTA_REGISTRY.clear()
