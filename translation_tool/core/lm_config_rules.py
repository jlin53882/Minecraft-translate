"""translation_tool/core/lm_config_rules.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import re
import threading
from collections.abc import Collection
from typing import Any

from ..utils.config_manager import get_models_config, load_config, load_config_shared
from ..utils.config_schema import LEGACY_API_KEY_PLACEHOLDERS
from ..utils.log_unit import log_debug, log_error, log_info, log_warning
from .lm_key_health import (
    DEFAULT_COOLDOWN_SEC,
    RECORDED_REASONS,
    KeyHealth,
    ModelQuotaHealth,
    get_key_health_registry,
    get_model_quota_registry,
    mask_key,
)

_API_KEY_PATTERN = re.compile(r"(?:AIza|AQ\.)[a-zA-Z0-9_-]+")
_API_KEY_PREFIXES = ("AIza", "AQ.")

# =========================
# 1. 執行緒安全的 API Key 索引追蹤器
# =========================


class KeyIndexTracker:
    """
    執行緒安全的 API Key 索引追蹤器。

    用於解決多執行緒環境下全域變數 _current_key_index 的 race condition 問題。
    透過 threading.Lock 確保並發存取的安全性。
    """

    def __init__(self, key_count: int = 0):
        self._index = 0
        self._key_count = key_count
        self._lock = threading.Lock()

    def get_current(self) -> int:
        """取得目前索引（執行緒安全）。"""
        with self._lock:
            return self._index

    def next(self) -> int:
        """
        輪替至下一個索引（執行緒安全，自動環繞）。

        當索引超過 key 數量時，會自動環繞回 0。
        """
        with self._lock:
            self._index += 1
            if self._key_count > 0:
                self._index = self._index % self._key_count
            return self._index

    def set_key_count(self, count: int):
        """設定 API Key 總數（用於 modulo 計算）。"""
        with self._lock:
            self._key_count = count

    def reset(self) -> None:
        """重置索引為 0（執行緒安全）。"""
        with self._lock:
            self._index = 0


# 模組級單例
_key_tracker = KeyIndexTracker()


# 向後相容：保留舊 API（內部呼叫 _key_tracker）
def get_current_key_index() -> int:
    """取得目前索引（向後相容用）。"""
    return _key_tracker.get_current()


def rotate_key_index() -> int:
    """輪替至下一個索引（向後相容用）。"""
    return _key_tracker.next()


def reset_key_index() -> None:
    """重置索引（向後相容用）。"""
    return _key_tracker.reset()


# =========================
# 2. 提示詞與配置
# =========================


def _get_all_keys() -> list[str]:
    """
    私有輔助函式：統一代理從設定檔讀取並清理金鑰列表。
    """
    config = load_config()
    keys = []
    for key in config.get("lm_translator", {}).get("keys", []):
        if not isinstance(key, str):
            continue
        normalized = key.strip()
        if normalized and normalized not in LEGACY_API_KEY_PLACEHOLDERS:
            keys.append(normalized)
    return keys


def get_api_key_count() -> int:
    """目前設定檔中有效 API Key 的數量。"""
    return len(_get_all_keys())


def get_translation_provider() -> str:
    """Return the configured provider, keeping old config files on Gemini."""
    provider = load_config().get("lm_translator", {}).get("provider", "gemini")
    if provider not in {"gemini", "chatgpt"}:
        raise RuntimeError(f"❌ 不支援的翻譯服務供應商：{provider}")
    return provider


def validate_translation_credentials() -> None:
    """Fail early when the selected provider lacks usable credentials or model."""
    config = load_config().get("lm_translator", {})
    provider = config.get("provider", "gemini")
    if provider == "chatgpt":
        if not str(config.get("chatgpt_model") or "").strip():
            raise RuntimeError("❌ 尚未選擇 ChatGPT 模型，請先登入並更新模型清單。")
        from .codex_oauth import get_chatgpt_access_token

        get_chatgpt_access_token()
        return
    if provider != "gemini":
        raise RuntimeError(f"❌ 不支援的翻譯服務供應商：{provider}")
    if not _get_all_keys():
        raise RuntimeError("❌ 設定檔中沒有找到任何 API Key，請先設定金鑰。")


def get_key_failure_cooldown_sec() -> float:
    """已確定 403 無權限的 key 要冷卻多久（秒）；0 = 不記憶（issue #113）。

    同專案模式下 RPD 耗盡記在「模型」上（``ModelQuotaRegistry``），不使用這個設定。
    """
    raw = load_config().get("lm_translator", {}).get("key_failure_cooldown_sec")
    if raw is None or isinstance(raw, bool):
        return DEFAULT_COOLDOWN_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_SEC
    return value if value >= 0 else DEFAULT_COOLDOWN_SEC


def get_key_health_snapshot() -> list[KeyHealth]:
    """依設定檔順序回傳每把 key 的健康狀態（給 UI 顯示）。同時清掉已不在設定檔內的 key 紀錄。"""
    keys = _get_all_keys()
    registry = get_key_health_registry()
    registry.prune(keys)
    return registry.snapshot(keys)


def get_model_quota_snapshot() -> list[ModelQuotaHealth]:
    """今日每日配額（RPD）已用盡的啟用模型（給 UI 顯示）；依設定檔的模型順序。"""
    models = [
        name
        for name, cfg in get_models_config(load_config()).items()
        if cfg.get("enabled", False)
    ]
    return get_model_quota_registry().snapshot(models)


def claim_api_key(exclude: Collection[int] = ()) -> tuple[int, str] | None:
    """
    原子地「領取」一把 API Key，回傳 (index, key)。

    **Key rotation contract（務必先讀）**

    - 這是 atomic get-and-advance：回傳指定的 key 後，tracker 立即前進到「下一把」。
      因此 tracker 的 index 代表「下一次預計使用哪一把」，**不是**「剛才 request 實際用了哪一把」。
      這樣高並發時不同執行緒會分散到不同 key（ATK-009）。
    - 「剛才用了哪一把」由回傳的 index 提供；呼叫端自己記住它（見 ApiKeyCycle），
      不可以用 get_current_key_index() 去猜剛才失敗的是哪一把。
    - exclude：本輪已實際失敗的 key index。從 tracker 目前位置起，領取第一把不在 exclude 的 key，
      並讓 tracker 前進到它的下一把。exclude 為空時行為與舊的 get_current_api_key 完全相同。

    回傳 None 的情況：沒有任何有效 key，或所有 key 都在 exclude 內。
    """
    keys = _get_all_keys()
    if not keys:
        log_error("❌ 設定檔中沒有找到任何有效的 API Key")
        return None

    count = len(keys)
    # 更新 key_count 以便正確環繞
    _key_tracker.set_key_count(count)

    # 執行緒安全：在同一把鎖內完成「挑選 + 前進」。
    with _key_tracker._lock:
        start = min(_key_tracker._index, count - 1)
        for offset in range(count):
            index = (start + offset) % count
            if index in exclude:
                continue
            _key_tracker._index = (index + 1) % count
            return index, keys[index]
    return None


def get_current_api_key() -> str:
    """
    領取下一把 API Key 並回傳其字串（atomic get-and-advance，見 claim_api_key）。

    注意：呼叫後 tracker 已前進到下一把，所以 get_current_key_index() 之後回傳的是
    「下一把」而不是剛才使用的這把。需要知道剛才用了哪一把，或要判斷「是否所有 key 都已
    實際失敗」時，請使用 claim_api_key / ApiKeyCycle。

    回傳:
        str: Gemini API 金鑰字串；沒有任何有效 key 時回傳空字串。
    """
    claim = claim_api_key()
    return claim[1] if claim else ""


class ApiKeyCycle:
    """
    一個 retry cycle 的 API Key 使用狀態。

    每次翻譯呼叫（以及其中對同一批資料的重試）各自建立一份，不跨執行緒共用。

    - claim()：領取下一把「本輪尚未實際失敗」的 key，並記住它的 index。
    - mark_failed()：把「剛才實際使用的那把」標記為失敗，回傳是否還有尚未嘗試的 key。
      回傳 False 才代表「所有可用 key 都已在本輪實際嘗試且都不可用」（耗盡）。
    - record_overload() / clear_overload()：503 overload 的**逐把 key** 計數。
      claim() 是 get-and-advance，連續的請求會輪流用不同的 key，所以 overload 次數必須綁定
      「實際使用的那把 key」；不同 key 的 overload 不互相累加，只有同一把 key 自己累積到門檻，
      呼叫端才應該對它 mark_failed()。
    - reset()：本輪成功後呼叫，開始新的 cycle（同時清除失敗紀錄與 overload 計數）。

    **跨批次的失敗記憶（issue #113）**

    本類別每個 cycle 都是新的，但「已確定 403 無權限」的 key 會記在 ``lm_key_health`` 的共用
    registry（冷卻 ``key_failure_cooldown_sec``）。

    注意（同專案模式）：**每日配額（RPD）不再記在 key 上**——Gemini 的 RPD 算在「專案 × 模型」，
    換 key 拿不到額度，所以 RPD 耗盡改記在模型上（``ModelQuotaRegistry``，由
    ``lm_translator_main`` 處理並換模型）。``mark_failed(reason="rpd")`` 只是 registry 保留的通用
    能力，現行翻譯流程不會呼叫它；請勿把 RPD 接回 key 輪替。

    - claim() 會先跳過冷卻中的 key，所以已耗盡的 key 不會在每個批次都被再請求一次。
    - 沒有任何健康的 key 時，每個 cycle 最多「試探」冷卻最快到期的**那一把**；試探失敗才算耗盡
      （mark_failed() 回傳 False → 呼叫端回報 ALL_KEYS_EXHAUSTED），成功則清除紀錄。
      試探 key 若回 429 RPM 這類暫時性錯誤，同一個 cycle 重試時仍只用這把，不會改領其他冷卻中的 key。
    - 只有 mark_failed(reason=...) 帶了 "rpd" / "forbidden" 才會記錄；429 RPM、503 overload
      與未帶原因的 mark_failed() 不會長期排除任何 key。
    - 成功時呼叫 record_success()（= 清除該把 key 的紀錄 + reset()）。

    耗盡只依據「實際嘗試過並失敗的 key」，不依賴 tracker index 恰好在最後一格。
    """

    # 沒有任何有效 key 時，overload 計數使用的 key
    _NO_KEY = -1

    def __init__(self) -> None:
        self._failed: set[int] = set()
        self._overload_counts: dict[int, int] = {}
        self.current_index: int | None = None
        self._current_key: str = ""
        self._probed = False  # 這個 cycle 是否已經試探過冷卻中的 key
        self._probe_key = ""  # 本 cycle 的試探 key（暫時性錯誤時繼續使用同一把）

    @property
    def failed_indexes(self) -> frozenset[int]:
        """本輪已實際失敗的 key index。"""
        return frozenset(self._failed)

    def _cooling_indexes(self, keys: list[str]) -> set[int]:
        registry = get_key_health_registry()
        return {i for i, key in enumerate(keys) if registry.is_cooling(key)}

    def claim(self) -> str:
        """領取下一把本輪尚未失敗、且不在冷卻中的 key。

        分成四種情況（不要靠模糊的 fallback 繞過冷卻）：

        1. 有健康的候選 key：直接領取（不會碰冷卻中的 key）。
        2. 沒有健康 key、本 cycle 還沒試探過：試探冷卻最快到期的那一把（每個 cycle 一次）。
        3. 沒有健康 key、本 cycle 已試探過：只能繼續用**同一把**試探 key（例如它剛回 429 RPM，
           那只是暫時性的，沒有證明它不可用）；**絕不**領取其他冷卻中的 key。
           試探 key 已確定失敗（或已不在設定檔）時回傳 ""，呼叫端的 mark_failed() 早已回報耗盡。
        4. 完全沒有冷卻中的 key（舊流程）：全部都失敗過時退回一般輪替（呼叫端應已先中止）。
        """
        keys = _get_all_keys()
        cooling = self._cooling_indexes(keys)
        claim = claim_api_key(self._failed | cooling)  # 1. 健康的 key
        if claim is None and cooling:
            claim = self._claim_probe(keys, cooling)  # 2 / 3. 只能動試探 key
        elif claim is None:
            claim = claim_api_key(self._failed) or claim_api_key()  # 4. 舊流程
        if claim is None:  # 沒有任何可領取的 key
            self.current_index = None
            self._current_key = ""
            return ""
        self.current_index, key = claim
        self._current_key = key
        return key

    def _claim_probe(
        self, keys: list[str], cooling: set[int]
    ) -> tuple[int, str] | None:
        """沒有健康 key 時：領取（或繼續使用）本 cycle 唯一的試探 key。"""
        registry = get_key_health_registry()
        if not self._probed:
            candidates = [i for i in cooling if i not in self._failed]
            if not candidates:
                return None
            probe = min(candidates, key=lambda i: registry.seconds_remaining(keys[i]))
            self._probed = True
            self._probe_key = keys[probe]
            log_info(
                f"[🔎] 沒有健康的 API Key，試探冷卻最快到期的 Key {probe}"
                f"（{mask_key(keys[probe])}）"
            )
        elif self._probe_key in keys:
            probe = keys.index(self._probe_key)
            if probe in self._failed:  # 試探已確定失敗：不再碰任何冷卻中的 key
                return None
        else:  # 試探 key 已從設定檔移除
            return None
        return claim_api_key(set(range(len(keys))) - {probe})

    def record_overload(self) -> int:
        """記錄「剛才實際使用的那把 key」遇到一次 503 overload，回傳該把 key 目前的累計次數。"""
        key = self._NO_KEY if self.current_index is None else self.current_index
        self._overload_counts[key] = self._overload_counts.get(key, 0) + 1
        return self._overload_counts[key]

    def overload_count(self, index: int | None = None) -> int:
        """指定 key（預設為剛才實際使用的那把）目前的 overload 累計次數。"""
        if index is None:
            index = self._NO_KEY if self.current_index is None else self.current_index
        return self._overload_counts.get(index, 0)

    def clear_overload(self) -> None:
        """中斷所有 key 的 overload 連續紀錄（遇到成功、截斷或非 503 的錯誤時）。"""
        self._overload_counts.clear()

    def mark_failed(self, reason: str | None = None) -> bool:
        """標記剛才實際使用的 key 為失敗。回傳 True = 還有尚未嘗試的 key 可用。

        reason 為 "forbidden"（403）時，另外把這把 key 記進共用的 key 健康狀態，之後的批次會
        跳過它直到冷卻到期（issue #113）。"rpd" 是 registry 的通用能力，現行流程不使用（RPD 記在
        模型上，見 ``ModelQuotaRegistry``）。其他情況不記錄。
        """
        if self.current_index is not None:
            self._failed.add(self.current_index)
            self._overload_counts.pop(self.current_index, None)
            if reason in RECORDED_REASONS and self._current_key:
                cooldown = get_key_failure_cooldown_sec()
                registry = get_key_health_registry()
                if registry.mark_failed(self._current_key, reason, cooldown):
                    log_warning(
                        f"[🧊] Key {self.current_index}（{mask_key(self._current_key)}）"
                        f"{'今日配額用盡 (RPD)' if reason == 'rpd' else '無權限 (403)'}，"
                        f"約 {cooldown / 60:.0f} 分鐘內不再使用，到期後會再試一次"
                    )
        keys = _get_all_keys()
        total = len(keys)
        cooling = self._cooling_indexes(keys)
        unfailed = [i for i in range(total) if i not in self._failed]
        if any(i not in cooling for i in unfailed):
            return True  # 還有健康、尚未嘗試的 key
        # 剩下的都在冷卻：這個 cycle 還沒試探過的話，還有一次機會
        return bool(unfailed) and not self._probed

    def record_success(self) -> None:
        """本輪成功：這把 key 確定可用（清除它的失敗紀錄），並開始新的 cycle。"""
        if self._current_key:
            get_key_health_registry().mark_ok(self._current_key)
        self.reset()

    def has_alternative_key(self) -> bool:
        """是否有一把以上的 key（沒有的話，換 key 沒有意義）。"""
        return get_api_key_count() > 1

    def reset(self) -> None:
        """本輪成功：清除失敗紀錄與 overload 計數，開始新的 cycle。

        不會清除共用的 key 健康狀態（冷卻中的 key 仍在冷卻）；成功時請用 record_success()。
        """
        self._failed.clear()
        self._overload_counts.clear()
        self._probed = False
        self._probe_key = ""


def rotate_api_key():
    """
    【舊 API，請勿用於翻譯請求流程】

    以 tracker 目前的 index 判斷是否還有下一把並前進。這套語意把 tracker 當成
    「剛才使用的 key」，與 get_current_api_key / claim_api_key 的 get-and-advance
    （tracker = 下一把）互相衝突：例如 2 把 key，領取 key0 後 tracker 已指向 key1，
    此時呼叫本函式會回傳 False，彷彿「所有 key 都用完」，但 key1 其實還沒被用過。
    翻譯流程已改用 ApiKeyCycle 判斷耗盡；本函式僅為向後相容而保留。

    切換至下一個可用的 API Key。

    使用時機：
    - 當遇到「配額 / 速率」相關錯誤時（例如 429 RESOURCE_EXHAUSTED）
    - 當遇到「單一 Key 暫時不可用，但仍有備援 Key」的情況

    ⚠️ 不適用於：
    - 400 INVALID_ARGUMENT（payload / schema 錯誤，換 Key 無效）
    - 邏輯錯誤或程式 bug
    - API Key 格式本身錯誤（例如 "token" 這種假 key）

    行為說明：
    - 內部透過 KeyIndexTracker (_key_tracker) 執行緒安全地切換
    - 已經沒有下一個 Key 時**不會拋出例外**，而是記錄錯誤並回傳 False；
      呼叫端必須檢查回傳值，並決定要中止（例如回傳 ALL_KEYS_EXHAUSTED / PARTIAL）
      或改用其他策略。忽略回傳值會在所有 Key 都不可用時繼續使用同一把 Key。

    Returns:
        bool:
            True  → 已切換到下一個 Key
            False → 已無可用 Key（所有 Key 都已嘗試過，例如配額 RPD / RPM 用盡）
    """
    keys = _get_all_keys()

    # 更新 key_count 以便正確環繞
    _key_tracker.set_key_count(len(keys))

    # 檢查是否還有下一個 Key 可以切換
    current_index = _key_tracker.get_current()
    if current_index + 1 >= len(keys):
        log_error("❌ 所有 API Key 已用盡（RPD exhausted）")
        return False

    # 切換至下一個 API Key（執行緒安全）
    new_index = _key_tracker.next()
    log_info(f"🔁 切換 API Key → index {new_index}")
    return True


def validate_api_keys():
    """
    驗證 API 金鑰格式。
    這應該在程式啟動或開始翻譯前呼叫一次。
    """
    if get_translation_provider() == "chatgpt":
        validate_translation_credentials()
        log_info("✅ ChatGPT OAuth 登入狀態可用。")
        return

    # 統一使用輔助函式獲取金鑰清單
    keys = _get_all_keys()

    if not keys:
        raise RuntimeError("❌ 設定檔中沒有找到任何 API Key，請先設定金鑰。")

    for k in keys:
        # 1. 接受標準 API keys (AIza) 與新的 Gemini authorization keys (AQ.)
        if not k.startswith(_API_KEY_PREFIXES):
            log_error(f"❌ 偵測到無效格式金鑰: {mask_key(k)}")
            raise RuntimeError(
                f"❌ 無效的 API Key 格式：{mask_key(k)}\n"
                "Gemini API Key 應以 'AIza' 或 'AQ.' 開頭，請檢查您的設定檔。"
            )
        # 2. 檢查金鑰長度（保留既有最低長度檢查）
        if len(k) < 35:
            log_error(f"❌ 偵測到過短的 API 金鑰: {mask_key(k)} (長度={len(k)})")
            raise RuntimeError(
                f"❌ API Key 長度異常：{mask_key(k)}\n"
                f"長度為 {len(k)}，API Key 應至少包含 35 個字元，請檢查是否輸入正確。"
            )
        # 3. 除支援前綴外，key body 僅允許英數字、dash 與 underscore。
        if not _API_KEY_PATTERN.fullmatch(k):
            log_error(f"❌ 偵測到包含無效字元的 API 金鑰: {mask_key(k)}")
            raise RuntimeError(
                f"❌ API Key 包含無效字元：{mask_key(k)}\n"
                "僅允許 'AIza' 或 'AQ.' 開頭後接英文字母、數字、 dash(-) 或 underscore(_)。"
            )

    log_info(f"✅ 金鑰格式驗證通過，共載入 {len(keys)} 組金鑰。")


def validate_api_keys_from_ui(keys: list[str]):  # ui 專用
    """驗證 API Key 格式（UI 專用）。

    參數：
        keys: API Key 列表
    """
    for k in keys:
        if not k:
            raise RuntimeError("❌ API Key 不得為空，請輸入有效的 Gemini API Key。")
        if not k.startswith(_API_KEY_PREFIXES):
            raise RuntimeError(
                f"❌ 無效的 API Key 格式：{mask_key(k)}\n"
                "請使用 Google AI Studio 產生的 Gemini API Key，"
                "應以 'AIza' 或 'AQ.' 字樣開頭。"
            )
        if len(k) < 35:
            raise RuntimeError(
                f"❌ API Key 長度異常：{mask_key(k)}\n"
                f"長度為 {len(k)}，API Key 應至少包含 35 個字元，請檢查是否輸入正確。"
            )
        if not _API_KEY_PATTERN.fullmatch(k):
            raise RuntimeError(
                f"❌ API Key 包含無效字元：{mask_key(k)}\n"
                "僅允許 'AIza' 或 'AQ.' 開頭後接英文字母、數字、 dash(-) 或 underscore(_)。"
            )


# =========================
# 2. Regex 規則定義
# =========================
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

# ✅ 純羅馬數字（I, II, III, IV, V, ... / 允許前後空白）
ROMAN_NUMERAL_PATTERN = re.compile(
    r"^\s*M{0,4}(CM|CD|D?C{0,3})"
    r"(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\s*$",
    re.IGNORECASE,
)

# ✅ 新增：純數字模式 (包含整數、浮點數、正負號與千分位逗號)
DIGIT_PATTERN = re.compile(r"^\s*[+-]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?\s*$")

TECH_PATTERN = re.compile(
    r"""
    ^[a-z0-9_\-.]+:[a-z0-9_\-./]+$ |   # minecraft:diamond
    ^[a-z0-9_\-.]+(\.[a-z0-9_\-.]+)+$ | # some.mod.key.path
    """,
    re.VERBOSE,
)

# 例如 booklet.section.entry 之類的 lang key 引用
LANG_KEY_REF_PATTERN = re.compile(
    r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$",
    re.IGNORECASE,  # 不區分大小寫
)
# 完整  $(...)  token
TOKEN_PATTERN = re.compile(r"\$\([^)]+\)")

# 需要跳過翻譯的文字（你指定的類型）
HASH_PREFIX_PATTERN = re.compile(r"^\s*#")  # 任何 # 開頭（含前置空白）


def needs_translation_text(s: str) -> bool:
    """判斷文字是否仍需翻譯（非空、非中文、非純數字、非 § / $( token）。"""
    if not s or not isinstance(s, str):
        return False

    # 已經是中文
    if contains_cjk(s):
        return False

    # 純數字 / 符號
    if s.strip().isdigit():
        return False

    # 常見不該翻的 token；其餘（還有英文）→ 需要翻
    return not s.startswith(("§", "$("))


def value_fully_translated(value) -> bool:
    """
    判斷一個值是否「已完全翻譯完成」。

    主要用途：
    - 用於翻譯快取（cache）命中判斷
    - 決定某一個 key / 欄位是否可以「直接使用 cache」
      而不需要再次送 API 翻譯

    判斷邏輯說明（**不判斷語系**，只判斷快取內容是否有值）：
    1. 若 value 是字串（str）：
       - 非空字串 → 視為已翻譯（即使內容是英文或特殊標記，也會直接命中快取）
       - 空字串 → 視為尚未翻譯，需重新送 API

    2. 若 value 是字串列表（list[str]）：
       - 只要其中任一元素是空字串，就判定整個 list 尚未完全翻譯（一票否決）
       - 其餘元素不檢查內容

    3. 其他型別（例如 dict / int / None）：
       - 不屬於翻譯目標
       - 視為已完成翻譯，直接回傳 True

    為什麼不判斷語系：
    - 是否「需要翻譯」由送進翻譯流程前的 needs_translation_text() 決定；
      快取只保存「已經處理過」的結果，命中時不再重複做語系判斷。
    - 代價：快取中若存有未翻譯的英文原文，也會被視為命中（空字串則不會）。

    Returns:
        bool:
            True  → 該值可安全視為「已翻譯完成」
            False → 尚有未翻譯內容，需送 API 翻譯
    """

    # ---------- 情況一：單一字串 ----------
    if isinstance(value, str):
        # ⭐ 不再判斷語系。只要字串不是空的，就代表這筆快取「已經被處理過了」
        # 即使內容是 [0x00] 或英文，也會直接命中快取

        return value is not None and value != ""
    # ---------- 情況二：字串列表 ----------
    if isinstance(value, list):
        for v in value:
            # 只要 list 裡的字串不是空的，就視為已翻譯
            if isinstance(v, str) and v == "":
                return False  # ⭐ 一票否決制
        return True

    # ---------- 情況三：其他型別 ----------
    # 非翻譯目標（dict / int / bool / None 等）
    # 直接視為已完成翻譯
    return True


def contains_cjk(s: str) -> bool:
    """
    檢查字串中是否包含 CJK（中 / 日 / 韓）文字。

    主要用途：
    - 判斷一段文字是否「已經翻譯過」
    - 作為 needs_translation_text / is_value_translatable
      的早期快速過濾條件
    - 避免將已含中文、日文、韓文的內容再次送 API 翻譯

    判斷範圍：
    - \u4e00-\u9fff  ：CJK Unified Ideographs（常用中文字）
    - \u3040-\u30ff：日文平假名 / 片假名
    - \uac00-\ud7af：韓文 Hangul

    行為說明：
    - 只要字串中「任一位置」出現上述字元
      即視為已包含 CJK
    - 不要求全文都是 CJK（混合語言也會被判定）

    為什麼要這樣設計：
    - 翻譯流程採取「保守策略」
    - 只要已出現中文，就假設該段已人工或先前處理過
    - 避免重複翻譯造成品質退化

    Args:
        s (str): 要檢查的字串

    Returns:
        bool:
            True  → 字串中包含 CJK 字元
            False → 不包含任何 CJK 字元
    """
    return isinstance(s, str) and CJK_RE.search(s) is not None


def build_skip_terms_pattern(terms: list[str]) -> re.Pattern:
    """
    將「需跳過翻譯的關鍵字清單」轉換為單一正規表達式（regex）。

    主要用途：
    - 避免翻譯特定技術或導向性文字
      （例如 API 文件、社群連結、官方網站等）
    - 統一管理「不應被翻譯的關鍵字名單」
    - 讓新增 / 移除關鍵字只需修改清單本身

    實作說明：
    1. 先對每個關鍵字進行 re.escape()
       - 確保關鍵字中的特殊符號不會影響 regex 語意
    2. 使用 OR（|）合併為單一 pattern
    3. 外層加上 \\b（word boundary）
       - 避免誤判單字片段（例如 "discordant" 不應命中 "discord"）
    4. 使用 re.IGNORECASE
       - 不區分大小寫（Discord / discord / DISCORD 都會命中）

    範例：
        terms = ["api documentation", "discord", "github"]
        產生的 pattern 等效於：
        r"\\b(api\\ documentation|discord|github)\\b"

    為什麼要這樣設計：
    - 將「規則」與「資料」分離（邏輯穩定、名單可擴充）
    - 比在多處硬編碼 if "xxx" in s 更好維護
    - regex 編譯一次，多次重複使用，效能較佳

    Args:
        terms (list[str]):
            需跳過翻譯的關鍵字清單

    Returns:
        re.Pattern:
            編譯完成的 regex pattern，
            可直接用於 pattern.search(text)
    """

    # 將每個關鍵字進行 escape，避免 regex 特殊字元造成誤判
    escaped = [re.escape(t) for t in terms]

    # 使用 OR (|) 合併所有關鍵字，並加上單字邊界
    # pattern = r"\b(" + "|".join(escaped) + r")\b"
    pattern = r"^\s*(?:" + "|".join(escaped) + r")\s*$"

    # 編譯為不區分大小寫的正規表達式
    return re.compile(pattern, re.IGNORECASE)


# =========================
# 值是否值得翻譯（核心判斷）
_RULES_CACHE: dict = {"config": None, "rules": None}


def _short_text_skip_len() -> int:
    return _translator_rules()[2]


def _translator_rules() -> tuple[re.Pattern, tuple[str, ...], int]:
    """回傳 (skip_terms pattern, translatable_keywords, short_text_skip_len)。

    每筆資料都會呼叫；設定未變動時（load_config_shared 回傳同一物件）
    直接使用已編譯的 pattern，不再逐筆讀設定檔與重新編譯 regex。
    """
    config = load_config_shared()
    cached = _RULES_CACHE
    if cached["config"] is config and cached["rules"] is not None:
        return cached["rules"]
    tr_cfg = config.get("lm_translator", {}).get("translator", {})
    try:
        short_len = max(0, int(tr_cfg.get("short_text_skip_len", 3)))
    except (TypeError, ValueError):
        short_len = 3
    rules = (
        build_skip_terms_pattern(tr_cfg.get("skip_terms", [])),
        tuple(tr_cfg.get("translatable_keywords", [])),
        short_len,
    )
    _RULES_CACHE.update(config=config, rules=rules)
    return rules


def is_value_translatable(value: Any, *, is_lang: bool = False) -> bool:
    """判斷值是否應送翻譯（排除中文、token、技術 ID、短字串、skip_terms 等）。"""
    if not isinstance(value, str):
        return False

    s = value.strip()
    if not s:
        return False

    # 已翻譯（含中日韓）
    if contains_cjk(s):
        return False

    # lang key 引用（例如 booklet.xxx.yyy）
    if LANG_KEY_REF_PATTERN.fullmatch(s):
        return False

    # 完整 token（$(...)）
    if TOKEN_PATTERN.fullmatch(s):
        return False

    # 技術 key / ID（minecraft:xxx / a.b.c）
    if TECH_PATTERN.fullmatch(s):
        return False

    # 太短且無空白，通常不是顯示文字（長度門檻可在設定調整；0 = 不略過，
    # 例如 Axe / Ore / Rod 這類短名稱也會送翻譯）
    if is_lang and len(s) <= _short_text_skip_len() and " " not in s:
        return False

        # 避開 #...（#heading、#title）
    if HASH_PREFIX_PATTERN.match(s):
        return False

    # 需要跳過翻譯的關鍵字（可在設定頁擴充；pattern 依設定版本快取）
    SKIP_TERMS_PATTERN, _, _ = _translator_rules()

    # 避開指定關鍵字（API documentation / Discord）
    if (
        is_lang and SKIP_TERMS_PATTERN.search(s) and len(s) <= 5  # ⭐ 關鍵
    ):
        log_debug("SKIP[skip_terms] len=%d text=%r", len(s), s)
        return False

    # 避開羅馬數字
    if is_lang and ROMAN_NUMERAL_PATTERN.fullmatch(s):
        return False

    # 避開純數字
    return not (is_lang and DIGIT_PATTERN.fullmatch(s))


# =========================
# 可翻譯欄位判斷
# =========================
def is_translatable_field(key: str) -> bool:
    """
    只要欄位名稱包含任一文字關鍵字，就視為可翻譯欄位
    """
    key_lower = key.lower()
    # 允許翻譯的欄位（包含你自訂的各種文字欄位） 關鍵字版本
    _, TRANSLATABLE_KEYWORDS, _ = _translator_rules()
    return any(keyword in key_lower for keyword in TRANSLATABLE_KEYWORDS)
