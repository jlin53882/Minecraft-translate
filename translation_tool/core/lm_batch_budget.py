"""translation_tool/core/lm_batch_budget.py 模組。

用途：以 token 預算決定一個翻譯批次要包含幾個項目，取代「只看項目數」的切批（issue #108）。

設計重點：
- **本地估算，不多打 API**：預設用 `tiktoken` 的 `o200k_base` 作為 provider tokenizer
  代理，再套用實測校正；套件不可用時退回字元係數。不用 `countTokens` API，避免多吃 RPM / RPD。
- **切批規則**：`character` 模式由前往後線性累加；`tiktoken_calibrated` 模式先以
  指數探測找出超出預算的前綴，再用二分搜尋找出最大可用前綴。兩者都以項目數上限、
  輸出 token 預算、輸入 token 預算中先到者為準，一律只取「輸入的前綴」，呼叫端依位置
  對應結果，不會錯位。
- **記住學到的預算**：依 profile（lang / ftb / kubejs / md / patch）保存。
  因 token 截斷而失敗 → 輸出預算減半；之後連續成功 → 緩慢回升，不會永遠停在保守值。
- **係數校正**：預期輸出 ≈ 輸入 × 係數。係數預設保守，成功回應後依實際
  ``candidatesTokenCount`` 以指數移動平均校正。

維護注意：本模組只做估算與狀態管理，不呼叫 API、不讀寫檔案（設定透過 BudgetConfig 傳入）。
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised only in minimal installs
    tiktoken = None

from translation_tool.core.lm_format_policy import (
    build_translation_payload,
    estimate_format_spec_tokens,
)
from translation_tool.utils.log_unit import log_debug, log_info

# 這些是舊版相容常數。新的估算器把 system prompt 與 item payload 分開校正，
# 並把 input / output 的 JSON 包裝成本拆開；保留名稱可避免外部工具與舊測試失效。
JSON_ITEM_OVERHEAD_TOKENS = 12.0
ASCII_TOKENS_PER_CHAR = 0.22
CJK_TOKENS_PER_CHAR = 0.62
OTHER_TOKENS_PER_CHAR = 1.5  # 非 ASCII 也非 CJK（重音字母、符號、emoji…）


@dataclass(frozen=True)
class TokenEstimatorConfig:
    """本地 token 估算器的可調參數。

    Gemini 的 ``countTokens`` 測試顯示，system prompt 與 item payload 的
    token 密度不同；把兩者分開比單一字元係數更穩定。參數可放在
    ``lm_translator.token_estimator``，缺漏時使用這組保守預設。
    """

    prompt_ascii_tokens_per_char: float = 0.10
    prompt_cjk_tokens_per_char: float = 0.85
    prompt_other_tokens_per_char: float = 1.50
    item_ascii_tokens_per_char: float = ASCII_TOKENS_PER_CHAR
    item_cjk_tokens_per_char: float = CJK_TOKENS_PER_CHAR
    item_other_tokens_per_char: float = OTHER_TOKENS_PER_CHAR
    input_item_overhead_tokens: float = 38.0
    output_item_overhead_tokens: float = JSON_ITEM_OVERHEAD_TOKENS
    algorithm: str = "character"
    tiktoken_encoding: str = "o200k_base"
    tiktoken_prompt_scale: float = 1.38027
    tiktoken_payload_scale: float = 0.92983
    tiktoken_request_bias: float = -451.55
    tiktoken_item_overhead_tokens: float = 0.39633

    @classmethod
    def from_config(cls, lm_cfg: dict[str, Any] | None) -> TokenEstimatorConfig:
        """從 ``lm_translator.token_estimator`` 讀取數值，錯誤值退回預設。"""
        raw = (lm_cfg or {}).get("token_estimator")
        values = raw if isinstance(raw, dict) else {}
        defaults = cls()

        def _num(key: str, default: float, *, minimum: float = 0.0) -> float:
            value = values.get(key)
            if value is None or isinstance(value, bool):
                return default
            try:
                value = float(value)
            except (TypeError, ValueError):
                return default
            return value if math.isfinite(value) and value >= minimum else default

        algorithm = values.get("algorithm", defaults.algorithm)
        if algorithm not in {"character", "tiktoken_calibrated"}:
            algorithm = defaults.algorithm
        encoding = values.get("tiktoken_encoding", defaults.tiktoken_encoding)
        if not isinstance(encoding, str) or not encoding.strip():
            encoding = defaults.tiktoken_encoding

        def _finite_num(
            key: str,
            default: float,
            *,
            minimum: float | None = None,
            strictly_positive: bool = False,
        ):
            value = values.get(key)
            if value is None or isinstance(value, bool):
                return default
            try:
                value = float(value)
            except (TypeError, ValueError):
                return default
            if not math.isfinite(value):
                return default
            if minimum is not None and value < minimum:
                return default
            if strictly_positive and value <= 0:
                return default
            return value

        return cls(
            prompt_ascii_tokens_per_char=_num(
                "prompt_ascii_tokens_per_char",
                defaults.prompt_ascii_tokens_per_char,
            ),
            prompt_cjk_tokens_per_char=_num(
                "prompt_cjk_tokens_per_char", defaults.prompt_cjk_tokens_per_char
            ),
            prompt_other_tokens_per_char=_num(
                "prompt_other_tokens_per_char", defaults.prompt_other_tokens_per_char
            ),
            item_ascii_tokens_per_char=_num(
                "item_ascii_tokens_per_char", defaults.item_ascii_tokens_per_char
            ),
            item_cjk_tokens_per_char=_num(
                "item_cjk_tokens_per_char", defaults.item_cjk_tokens_per_char
            ),
            item_other_tokens_per_char=_num(
                "item_other_tokens_per_char", defaults.item_other_tokens_per_char
            ),
            input_item_overhead_tokens=_num(
                "input_item_overhead_tokens", defaults.input_item_overhead_tokens
            ),
            output_item_overhead_tokens=_num(
                "output_item_overhead_tokens", defaults.output_item_overhead_tokens
            ),
            algorithm=algorithm,
            tiktoken_encoding=encoding.strip(),
            tiktoken_prompt_scale=_finite_num(
                "tiktoken_prompt_scale",
                defaults.tiktoken_prompt_scale,
                strictly_positive=True,
            ),
            tiktoken_payload_scale=_finite_num(
                "tiktoken_payload_scale",
                defaults.tiktoken_payload_scale,
                strictly_positive=True,
            ),
            tiktoken_request_bias=_finite_num(
                "tiktoken_request_bias", defaults.tiktoken_request_bias
            ),
            tiktoken_item_overhead_tokens=_finite_num(
                "tiktoken_item_overhead_tokens",
                defaults.tiktoken_item_overhead_tokens,
                minimum=0.0,
            ),
        )


DEFAULT_TOKEN_ESTIMATOR = TokenEstimatorConfig()


@lru_cache(maxsize=4)
def _get_tiktoken_encoding(name: str):
    """取得 tokenizer；套件或 encoding 不可用時讓呼叫端回到字元估算。"""
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(name)
    except Exception:  # noqa: BLE001 - optional fallback must never block translation
        return None


def warm_token_encoder(estimator: TokenEstimatorConfig | None = None) -> bool:
    """預先載入 tokenizer，回傳是否可用。

    第一次載入要解析數十萬筆詞表，並在 C 擴充內持有 GIL 約 0.1 秒以上；若發生在使用者操作
    的當下（例如第一次預覽批次機翻），整個事件迴圈會跟著停住。App 啟動後在背景先載入，
    之後的批次規劃直接使用快取的 tokenizer。
    """
    config = estimator or DEFAULT_TOKEN_ESTIMATOR
    return _get_tiktoken_encoding(config.tiktoken_encoding) is not None


def _tiktoken_count(text: str, estimator: TokenEstimatorConfig) -> int | None:
    encoding = _get_tiktoken_encoding(estimator.tiktoken_encoding)
    if encoding is None:
        return None
    try:
        return len(encoding.encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 - malformed provider text uses local fallback
        return None


def _uses_tiktoken(estimator: TokenEstimatorConfig) -> bool:
    return estimator.algorithm == "tiktoken_calibrated" and (
        _get_tiktoken_encoding(estimator.tiktoken_encoding) is not None
    )


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
LANG_PROMPT_PROFILES = frozenset({"lang", "kubejs"})


def profile_for_cache_type(cache_type: str | None) -> str:
    """把 cache_type 轉成 profile 名稱；未知或空值視為 lang（與共用迴圈的預設一致）。"""
    return PROFILE_BY_CACHE_TYPE.get(str(cache_type or "").lower(), "lang")


def uses_lang_prompt(profile: str | None) -> bool:
    """Return whether a batch profile uses the language-file prompt."""
    return str(profile or "").lower() in LANG_PROMPT_PROFILES


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetConfig:
    """token 預算相關設定（來自 config 的 ``lm_translator`` 區段）。"""

    enabled: bool = True
    max_output_token_budget: int = 65_536
    max_input_token_budget: int = 256_000
    max_output_tokens: int = 65_536
    output_token_factor: float = 1.5
    min_scale: float = 0.0625
    recover_after: int = 3
    recover_factor: float = 1.5
    token_estimator: TokenEstimatorConfig = field(
        default_factory=lambda: DEFAULT_TOKEN_ESTIMATOR
    )

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
            token_estimator=TokenEstimatorConfig.from_config(lm_cfg),
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


def estimate_text_tokens(
    text: str,
    *,
    estimator: TokenEstimatorConfig | None = None,
    component: str = "item",
) -> float:
    """估算文字 token 數；``component=prompt`` 使用提示詞係數。"""
    if not text:
        return 0.0
    estimator = estimator or DEFAULT_TOKEN_ESTIMATOR
    if component == "prompt":
        ascii_rate = estimator.prompt_ascii_tokens_per_char
        cjk_rate = estimator.prompt_cjk_tokens_per_char
        other_rate = estimator.prompt_other_tokens_per_char
    else:
        ascii_rate = estimator.item_ascii_tokens_per_char
        cjk_rate = estimator.item_cjk_tokens_per_char
        other_rate = estimator.item_other_tokens_per_char
    total = 0.0
    for ch in text:
        if ch.isascii():
            total += ascii_rate
        elif _is_cjk(ch):
            total += cjk_rate
        else:
            total += other_rate
    return total


def estimate_prompt_tokens(
    text: str,
    *,
    estimator: TokenEstimatorConfig | None = None,
    include_request_bias: bool = True,
) -> float:
    """使用 system prompt 係數估算固定提示詞 token。"""
    estimator = estimator or DEFAULT_TOKEN_ESTIMATOR
    if _uses_tiktoken(estimator):
        raw = _tiktoken_count(text, estimator)
        if raw is not None:
            bias = estimator.tiktoken_request_bias if include_request_bias else 0.0
            return max(0.0, raw * estimator.tiktoken_prompt_scale + bias)
    return estimate_text_tokens(text, estimator=estimator, component="prompt")


def estimate_payload_tokens(
    payload: dict[str, Any], *, estimator: TokenEstimatorConfig | None = None
) -> float:
    """估算已序列化 provider payload 的輸入 token。

    Character fallback 的請求還有每項固定輸入開銷；與批次規劃及重試
    共用這個函式時，不能只計算 JSON 文字本身。
    """
    estimator = estimator or DEFAULT_TOKEN_ESTIMATOR
    serialized = json.dumps(payload, ensure_ascii=False)
    items = payload.get("items")
    item_count = len(items) if isinstance(items, list) else 0
    if _uses_tiktoken(estimator):
        raw = _tiktoken_count(serialized, estimator)
        if raw is not None:
            return max(
                0.0,
                raw * estimator.tiktoken_payload_scale
                + item_count * estimator.tiktoken_item_overhead_tokens,
            )
    return (
        estimate_text_tokens(serialized, estimator=estimator)
        + item_count * estimator.input_item_overhead_tokens
    )


def estimate_value_tokens(
    item: dict[str, Any], *, estimator: TokenEstimatorConfig | None = None
) -> float:
    """估算單一項目「要翻譯的文字」的 token 數（送出的 ``value``）。"""
    return estimate_text_tokens(str(item.get("text") or ""), estimator=estimator)


def estimate_input_item_tokens(
    item: dict[str, Any], *, estimator: TokenEstimatorConfig | None = None
) -> float:
    """Estimate the translated value plus request-only format metadata."""
    estimator = estimator or DEFAULT_TOKEN_ESTIMATOR
    if _uses_tiktoken(estimator):
        return estimate_payload_tokens(
            build_translation_payload([item], item_ids=["0"]), estimator=estimator
        )
    estimate_item_text = lambda text: estimate_text_tokens(text, estimator=estimator)
    return estimate_value_tokens(
        item, estimator=estimator
    ) + estimate_format_spec_tokens(item, estimate_item_text)


def _resolve_item_ids(item_count: int, item_ids: Sequence[str] | None) -> list[str]:
    """Resolve the IDs used by the actual provider payload for a prefix."""
    if item_ids is None:
        return [str(index) for index in range(item_count)]
    resolved = [str(item_id) for item_id in item_ids]
    if len(resolved) != item_count:
        raise ValueError("payload item IDs and items must have the same length")
    return resolved


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

    def planning_signature(self, cfg: BudgetConfig) -> tuple[bool, float, float, float]:
        """Return the immutable budget inputs used to decide batch boundaries."""
        if not cfg.enabled:
            return False, 0.0, 0.0, 0.0
        with self._lock:
            scale = max(self._scale, cfg.min_scale)
            output_factor = (
                self._output_factor
                if self._output_factor is not None
                else cfg.output_token_factor
            )
            return (
                cfg.enabled,
                cfg.max_output_token_budget * scale,
                float(cfg.max_input_token_budget),
                output_factor,
            )

    # -- 切批 --------------------------------------------------------------

    def estimate(
        self,
        items: Sequence[dict[str, Any]],
        cfg: BudgetConfig,
        fixed_input_tokens: float = 0.0,
        *,
        item_ids: Sequence[str] | None = None,
    ) -> BatchEstimate:
        """估算「把這些項目整批送出」的輸入 / 輸出 token。"""
        factor = self.output_factor(cfg)
        estimator = cfg.token_estimator
        values = list(items)
        resolved_item_ids = _resolve_item_ids(len(values), item_ids)
        value = sum(estimate_value_tokens(it, estimator=estimator) for it in values)
        n = len(values)
        if _uses_tiktoken(estimator):
            input_items = estimate_payload_tokens(
                build_translation_payload(values, item_ids=resolved_item_ids),
                estimator=estimator,
            )
        else:
            input_items = (
                sum(
                    estimate_input_item_tokens(item, estimator=estimator)
                    for item in values
                )
                + n * estimator.input_item_overhead_tokens
            )
        return BatchEstimate(
            count=n,
            value_tokens=value,
            input_tokens=fixed_input_tokens + input_items,
            output_tokens=value * factor + n * estimator.output_item_overhead_tokens,
        )

    def _fit_character_prefix(
        self,
        items: Sequence[dict[str, Any]],
        cfg: BudgetConfig,
        fixed_input_tokens: float,
        out_budget: float,
        in_budget: float,
    ) -> int:
        """Fit an additive character estimate in one forward pass."""
        estimator = cfg.token_estimator
        factor = self.output_factor(cfg)
        value_tokens = 0.0
        input_tokens = float(fixed_input_tokens)
        taken = 0
        for candidate_count, item in enumerate(items, start=1):
            value_tokens += estimate_value_tokens(item, estimator=estimator)
            input_tokens += (
                estimate_input_item_tokens(item, estimator=estimator)
                + estimator.input_item_overhead_tokens
            )
            output_tokens = (
                value_tokens * factor
                + candidate_count * estimator.output_item_overhead_tokens
            )
            if candidate_count > 1 and (
                input_tokens > in_budget or output_tokens > out_budget
            ):
                break
            taken = candidate_count
        return max(taken, 1)

    def _estimate_prefix(
        self,
        items: Sequence[dict[str, Any]],
        candidate_count: int,
        cfg: BudgetConfig,
        fixed_input_tokens: float,
        item_ids: Sequence[str],
        estimates: dict[int, BatchEstimate],
    ) -> BatchEstimate:
        """Estimate and cache one exact tiktoken payload prefix."""
        cached = estimates.get(candidate_count)
        if cached is None:
            cached = self.estimate(
                items[:candidate_count],
                cfg,
                fixed_input_tokens,
                item_ids=item_ids[:candidate_count],
            )
            estimates[candidate_count] = cached
        return cached

    def _fit_tiktoken_prefix(
        self,
        items: Sequence[dict[str, Any]],
        cap: int,
        cfg: BudgetConfig,
        fixed_input_tokens: float,
        out_budget: float,
        in_budget: float,
        item_ids: Sequence[str],
    ) -> int:
        """Fit exact payload estimates with logarithmic prefix probing."""
        estimates: dict[int, BatchEstimate] = {}
        lower = 0
        probe = 1
        while True:
            candidate_estimate = self._estimate_prefix(
                items,
                probe,
                cfg,
                fixed_input_tokens,
                item_ids,
                estimates,
            )
            if (
                candidate_estimate.input_tokens > in_budget
                or candidate_estimate.output_tokens > out_budget
            ):
                upper = probe
                break
            lower = probe
            if probe == cap:
                return cap
            probe = min(probe * 2, cap)

        while lower + 1 < upper:
            middle = (lower + upper) // 2
            candidate_estimate = self._estimate_prefix(
                items,
                middle,
                cfg,
                fixed_input_tokens,
                item_ids,
                estimates,
            )
            if (
                candidate_estimate.input_tokens <= in_budget
                and candidate_estimate.output_tokens <= out_budget
            ):
                lower = middle
            else:
                upper = middle

        # Keep the final decision tied to the exact selected payload and IDs.
        if lower > 0:
            self._estimate_prefix(
                items,
                lower,
                cfg,
                fixed_input_tokens,
                item_ids,
                estimates,
            )
        return max(lower, 1)

    def fit(
        self,
        items: Sequence[dict[str, Any]],
        count_cap: int,
        cfg: BudgetConfig,
        fixed_input_tokens: float = 0.0,
        *,
        item_ids: Sequence[str] | None = None,
    ) -> int:
        """回傳要取的「前綴」長度：項目數上限 / 輸出預算 / 輸入預算，先到者為準。

        至少回傳 1（單一項目本身就超過預算時仍要送出，由截斷流程處理）；空清單回傳 0。
        """
        total = len(items)
        if total == 0:
            return 0
        cap = total if count_cap <= 0 else min(count_cap, total)
        out_budget, in_budget = self.effective_budgets(cfg)

        if item_ids is not None and len(item_ids) != total:
            raise ValueError("payload item IDs and items must have the same length")
        limited_items = items[:cap]
        limited_item_ids = item_ids[:cap] if item_ids is not None else None
        resolved_item_ids = _resolve_item_ids(cap, limited_item_ids)

        estimator = cfg.token_estimator
        if _uses_tiktoken(estimator):
            return self._fit_tiktoken_prefix(
                limited_items,
                cap,
                cfg,
                fixed_input_tokens,
                out_budget,
                in_budget,
                resolved_item_ids,
            )
        return self._fit_character_prefix(
            limited_items,
            cfg,
            fixed_input_tokens,
            out_budget,
            in_budget,
        )

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
                    actual_output_tokens
                    - item_count * cfg.token_estimator.output_item_overhead_tokens
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
    item_ids: Sequence[str] | None = None,
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
    return get_tracker(profile).fit(
        items,
        cap,
        cfg,
        fixed_input_tokens,
        item_ids=item_ids,
    )


def is_item_oversized(
    item: dict[str, Any],
    lm_cfg: dict[str, Any] | None,
) -> bool:
    """Return whether one item cannot fit even as a single-item batch.

    只用設定的硬上限比較「項目本身」：提示詞與 session 歷史（會隨批次成長）、
    以及由截斷學到的輸出預算縮放／倍率都不算在內，否則前一批的失敗會讓後面
    正常大小的項目被永久略過。

    ``BatchBudgetTracker.fit`` intentionally returns one for this case so the
    caller can decide how to handle the item.  Mod DB needs a different
    policy from the generic translators: preserve the source, skip API/cache/
    DB writes, and continue with the following item.
    """
    cfg = BudgetConfig.from_config(lm_cfg)
    if not cfg.enabled:
        return False
    estimator = cfg.token_estimator
    input_tokens = estimate_input_item_tokens(item, estimator=estimator)
    if not _uses_tiktoken(estimator):
        input_tokens += estimator.input_item_overhead_tokens
    output_tokens = (
        estimate_value_tokens(item, estimator=estimator) * cfg.output_token_factor
        + estimator.output_item_overhead_tokens
    )
    return (
        input_tokens > cfg.max_input_token_budget
        or output_tokens > cfg.max_output_token_budget
    )
