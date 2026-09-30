"""translation_tool/core/lm_batch_budget.py 模組。

用途：以 token 預算決定一個翻譯批次要包含幾個項目，取代「只看項目數」的切批（issue #108）。

設計重點：
- **本地估算，不多打 API**：英文約 0.25 token / 字元、CJK 約 1.2 token / 字元，
  每個項目另加約 12 token 的 JSON 包裝。不用 countTokens API（會多吃 RPM / RPD）。
- **切批規則**：由前往後累加，先到者為準 —— 項目數上限、輸出 token 預算、輸入 token 預算。
  一律只取「輸入的前綴」，呼叫端依位置對應結果，不會錯位。
- **記住學到的預算**：依 profile（lang / ftb / kubejs / md / patch）保存。
  因 token 截斷而失敗 → 輸出預算減半；之後連續成功 → 緩慢回升，不會永遠停在保守值。
- **係數校正**：預期輸出 ≈ 輸入 × 係數。係數預設保守，成功回應後依實際
  ``candidatesTokenCount`` 以指數移動平均校正。

維護注意：本模組只做估算與狀態管理，不呼叫 API、不讀寫檔案（設定透過 BudgetConfig 傳入）。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from translation_tool.utils.log_unit import log_debug, log_info

# 每個項目的 JSON 包裝（id、value 鍵、括號、逗號）約佔的 token。
JSON_ITEM_OVERHEAD_TOKENS = 12
ASCII_TOKENS_PER_CHAR = 0.25
CJK_TOKENS_PER_CHAR = 1.2
OTHER_TOKENS_PER_CHAR = 0.5  # 非 ASCII 也非 CJK（重音字母、符號、emoji…）

# 輸出係數校正（EMA）：新觀測值的權重與合理範圍
CALIBRATION_WEIGHT = 0.3
MIN_OUTPUT_FACTOR = 0.3
MAX_OUTPUT_FACTOR = 6.0

# 撞牆後學到的預算倍率：減半
TRUNCATION_SHRINK = 0.5

# cache_type → profile（與 lm_translator_main.detect_batch_profile 的名稱一致）
PROFILE_BY_CACHE_TYPE = {
    "lang": "lang",
    "ftbquests": "ftb",
    "kubejs": "kubejs",
    "md": "md",
    "patchouli": "patch",
}


def profile_for_cache_type(cache_type: str | None) -> str:
    """把 cache_type 轉成 profile 名稱；未知或空值視為 lang（與共用迴圈的預設一致）。"""
    return PROFILE_BY_CACHE_TYPE.get(str(cache_type or "").lower(), "lang")


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetConfig:
    """token 預算相關設定（來自 config 的 ``lm_translator`` 區段）。"""

    enabled: bool = True
    max_output_token_budget: int = 24_000
    max_input_token_budget: int = 60_000
    max_output_tokens: int = 32_768
    output_token_factor: float = 1.5
    min_scale: float = 0.0625
    recover_after: int = 3
    recover_factor: float = 1.5

    @classmethod
    def from_config(cls, lm_cfg: dict[str, Any] | None) -> BudgetConfig:
        """從 lm_translator 設定建立；缺漏或型別錯誤的欄位退回預設值（驗證在 config_manager）。"""
        lm_cfg = lm_cfg or {}
        d = cls()

        def _num(key: str, default, *, cast, minimum=None, maximum=None):
            raw = lm_cfg.get(key)
            if raw is None or isinstance(raw, bool):
                return default
            try:
                value = cast(raw)
            except (TypeError, ValueError):
                return default
            if minimum is not None and value < minimum:
                return default
            if maximum is not None and value > maximum:
                return default
            return value

        enabled = lm_cfg.get("token_budget_enabled", d.enabled)
        return cls(
            enabled=bool(enabled),
            max_output_token_budget=_num(
                "max_output_token_budget",
                d.max_output_token_budget,
                cast=int,
                minimum=1,
            ),
            max_input_token_budget=_num(
                "max_input_token_budget", d.max_input_token_budget, cast=int, minimum=1
            ),
            max_output_tokens=_num(
                "max_output_tokens", d.max_output_tokens, cast=int, minimum=0
            ),
            output_token_factor=_num(
                "output_token_factor",
                d.output_token_factor,
                cast=float,
                minimum=MIN_OUTPUT_FACTOR,
                maximum=MAX_OUTPUT_FACTOR,
            ),
            min_scale=_num(
                "budget_min_scale", d.min_scale, cast=float, minimum=1e-3, maximum=1.0
            ),
            recover_after=_num(
                "budget_recover_after", d.recover_after, cast=int, minimum=1
            ),
            recover_factor=_num(
                "budget_recover_factor",
                d.recover_factor,
                cast=float,
                minimum=1.0001,
                maximum=4.0,
            ),
        )


# ---------------------------------------------------------------------------
# 估算
# ---------------------------------------------------------------------------


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3000 <= cp <= 0x30FF  # CJK 標點、平假名、片假名
        or 0x3400 <= cp <= 0x4DBF  # 擴充 A
        or 0x4E00 <= cp <= 0x9FFF  # 基本區
        or 0xAC00 <= cp <= 0xD7AF  # 韓文音節
        or 0xF900 <= cp <= 0xFAFF  # 相容漢字
        or 0xFF00 <= cp <= 0xFFEF  # 全形
    )


def estimate_text_tokens(text: str) -> float:
    """估算一段文字的 token 數（純本地、偏保守）。"""
    if not text:
        return 0.0
    if text.isascii():
        return len(text) * ASCII_TOKENS_PER_CHAR
    total = 0.0
    for ch in text:
        if ch.isascii():
            total += ASCII_TOKENS_PER_CHAR
        elif _is_cjk(ch):
            total += CJK_TOKENS_PER_CHAR
        else:
            total += OTHER_TOKENS_PER_CHAR
    return total


def estimate_value_tokens(item: dict[str, Any]) -> float:
    """估算單一項目「要翻譯的文字」的 token 數（送出的 ``value``）。"""
    return estimate_text_tokens(str(item.get("text") or ""))


@dataclass(frozen=True)
class BatchEstimate:
    """一個批次的估算結果。"""

    count: int
    value_tokens: float  # 所有項目原文的 token 合計（不含包裝）
    input_tokens: float  # 含包裝與固定開銷
    output_tokens: float  # 以目前係數推估


# ---------------------------------------------------------------------------
# 學習式預算
# ---------------------------------------------------------------------------


class BatchBudgetTracker:
    """單一 profile 的預算狀態（執行緒安全）。

    - ``scale``：套用在輸出預算上的學習倍率（1.0 = 完全使用設定值）。
    - ``output_factor``：預期輸出 / 輸入的係數；None 代表尚未校正，使用設定值。
    """

    def __init__(self, profile: str) -> None:
        self.profile = profile
        self._lock = threading.Lock()
        self._scale = 1.0
        self._ok_streak = 0
        self._output_factor: float | None = None
        self.truncations = 0

    # -- 讀取狀態 ----------------------------------------------------------

    @property
    def scale(self) -> float:
        with self._lock:
            return self._scale

    def output_factor(self, cfg: BudgetConfig) -> float:
        with self._lock:
            return (
                self._output_factor
                if self._output_factor is not None
                else cfg.output_token_factor
            )

    def effective_budgets(self, cfg: BudgetConfig) -> tuple[float, float]:
        """(輸出預算, 輸入預算)。學到的倍率只作用在輸出預算。"""
        with self._lock:
            scale = max(self._scale, cfg.min_scale)
        return cfg.max_output_token_budget * scale, float(cfg.max_input_token_budget)

    # -- 切批 --------------------------------------------------------------

    def estimate(
        self,
        items: Sequence[dict[str, Any]],
        cfg: BudgetConfig,
        fixed_input_tokens: float = 0.0,
    ) -> BatchEstimate:
        """估算「把這些項目整批送出」的輸入 / 輸出 token。"""
        factor = self.output_factor(cfg)
        value = sum(estimate_value_tokens(it) for it in items)
        n = len(items)
        return BatchEstimate(
            count=n,
            value_tokens=value,
            input_tokens=fixed_input_tokens + value + n * JSON_ITEM_OVERHEAD_TOKENS,
            output_tokens=value * factor + n * JSON_ITEM_OVERHEAD_TOKENS,
        )

    def fit(
        self,
        items: Sequence[dict[str, Any]],
        count_cap: int,
        cfg: BudgetConfig,
        fixed_input_tokens: float = 0.0,
    ) -> int:
        """回傳要取的「前綴」長度：項目數上限 / 輸出預算 / 輸入預算，先到者為準。

        至少回傳 1（單一項目本身就超過預算時仍要送出，由截斷流程處理）；空清單回傳 0。
        """
        total = len(items)
        if total == 0:
            return 0
        cap = total if count_cap <= 0 else min(count_cap, total)
        out_budget, in_budget = self.effective_budgets(cfg)
        factor = self.output_factor(cfg)

        used_in = fixed_input_tokens
        used_out = 0.0
        taken = 0
        for item in items[:cap]:
            value = estimate_value_tokens(item)
            in_t = value + JSON_ITEM_OVERHEAD_TOKENS
            out_t = value * factor + JSON_ITEM_OVERHEAD_TOKENS
            if taken > 0 and (
                used_in + in_t > in_budget or used_out + out_t > out_budget
            ):
                break
            used_in += in_t
            used_out += out_t
            taken += 1
        return max(taken, 1)

    # -- 回饋 --------------------------------------------------------------

    def on_truncated(
        self, finish_reason: str | None, *, kind: str = "truncated"
    ) -> bool:
        """批次失敗後呼叫；回傳是否縮小了學到的輸出預算。

        依 ``finishReason`` 決定這是不是 token 上限造成的（階段 0 觀測的用途）：
        - kind="truncated"（回應 JSON 被截斷）：只有 ``finishReason == STOP`` 不縮 ——
          模型正常結束卻產出壞 JSON，不是大小問題，交給既有的項目數縮小流程。
          其他（MAX_TOKENS、或沒有 meta）一律視為 token 截斷。
        - kind="missing"（漏翻）：只有明確的 ``MAX_TOKENS`` 才縮。
        """
        reason = (finish_reason or "").upper()
        if kind == "missing":
            learn = reason == "MAX_TOKENS"
        else:
            learn = reason != "STOP"
        if not learn:
            return False
        with self._lock:
            self._ok_streak = 0
            self.truncations += 1
            new_scale = self._scale * TRUNCATION_SHRINK
            changed = new_scale < self._scale
            self._scale = new_scale
        if changed:
            log_info(
                f"[Budget:{self.profile}] 因截斷縮小輸出 token 預算倍率 → {self._scale:.3f}"
                f"（finishReason={finish_reason or '未知'}）"
            )
        return changed

    def on_success(
        self,
        cfg: BudgetConfig,
        *,
        value_tokens: float,
        item_count: int,
        actual_output_tokens: int | None = None,
    ) -> None:
        """批次成功後呼叫：累計連續成功以回升預算，並以實際用量校正輸出係數。"""
        with self._lock:
            self._ok_streak += 1
            if self._scale < 1.0 and self._ok_streak >= cfg.recover_after:
                self._scale = min(1.0, self._scale * cfg.recover_factor)
                self._ok_streak = 0
                log_debug(
                    f"[Budget:{self.profile}] 連續成功，輸出預算倍率回升 → {self._scale:.3f}"
                )
            if actual_output_tokens and actual_output_tokens > 0 and value_tokens > 0:
                observed = (
                    actual_output_tokens - item_count * JSON_ITEM_OVERHEAD_TOKENS
                ) / value_tokens
                observed = min(max(observed, MIN_OUTPUT_FACTOR), MAX_OUTPUT_FACTOR)
                current = (
                    self._output_factor
                    if self._output_factor is not None
                    else cfg.output_token_factor
                )
                self._output_factor = (
                    1 - CALIBRATION_WEIGHT
                ) * current + CALIBRATION_WEIGHT * observed

    def reset(self) -> None:
        """回到初始狀態（測試用，或使用者改了設定後想重新學習）。"""
        with self._lock:
            self._scale = 1.0
            self._ok_streak = 0
            self._output_factor = None
            self.truncations = 0


# ---------------------------------------------------------------------------
# 模組層級登錄（依 profile 保存，跨批次、跨呼叫保留）
# ---------------------------------------------------------------------------

_TRACKERS: dict[str, BatchBudgetTracker] = {}
_TRACKERS_LOCK = threading.Lock()


def get_tracker(profile: str) -> BatchBudgetTracker:
    """取得（必要時建立）該 profile 的預算狀態。"""
    with _TRACKERS_LOCK:
        tracker = _TRACKERS.get(profile)
        if tracker is None:
            tracker = _TRACKERS[profile] = BatchBudgetTracker(profile)
        return tracker


def reset_trackers() -> None:
    """清除所有 profile 的學習狀態（測試用）。"""
    with _TRACKERS_LOCK:
        _TRACKERS.clear()


def select_batch_size(
    items: Sequence[dict[str, Any]],
    profile: str,
    count_cap: int,
    lm_cfg: dict[str, Any] | None,
    *,
    fixed_input_tokens: float = 0.0,
) -> int:
    """共用的切批入口：三處切批（共用迴圈 / 目錄翻譯 / 智慧批次）都用它。

    停用 token 預算（``token_budget_enabled`` = false）時，行為等同舊版：只看項目數上限。
    """
    total = len(items)
    if total == 0:
        return 0
    cap = total if count_cap <= 0 else min(count_cap, total)
    cfg = BudgetConfig.from_config(lm_cfg)
    if not cfg.enabled:
        return cap
    return get_tracker(profile).fit(items, cap, cfg, fixed_input_tokens)
