"""translation_tool/core/lm_config_rules.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import re
import threading
from collections.abc import Collection
from typing import Any

from ..utils.config_manager import load_config, load_config_shared
from ..utils.log_unit import log_debug, log_error, log_info

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
    return [
        key.strip()
        for key in config.get("lm_translator", {}).get("keys", [])
        if isinstance(key, str) and key.strip()
    ]


def get_api_key_count() -> int:
    """目前設定檔中有效 API Key 的數量。"""
    return len(_get_all_keys())


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
    - reset()：本輪成功後呼叫，開始新的 cycle。

    耗盡只依據「實際嘗試過並失敗的 key」，不依賴 tracker index 恰好在最後一格。
    """

    def __init__(self) -> None:
        self._failed: set[int] = set()
        self.current_index: int | None = None

    @property
    def failed_indexes(self) -> frozenset[int]:
        """本輪已實際失敗的 key index。"""
        return frozenset(self._failed)

    def claim(self) -> str:
        """領取下一把本輪尚未失敗的 key；全部都失敗過時退回一般輪替（呼叫端應已先中止）。"""
        claim = claim_api_key(self._failed) or claim_api_key()
        if claim is None:  # 沒有任何有效 key
            self.current_index = None
            return ""
        self.current_index, key = claim
        return key

    def mark_failed(self) -> bool:
        """標記剛才實際使用的 key 為失敗。回傳 True = 還有尚未嘗試的 key 可用。"""
        if self.current_index is not None:
            self._failed.add(self.current_index)
        total = get_api_key_count()
        failed_in_range = sum(1 for index in self._failed if index < total)
        return failed_in_range < total

    def has_alternative_key(self) -> bool:
        """是否有一把以上的 key（沒有的話，換 key 沒有意義）。"""
        return get_api_key_count() > 1

    def reset(self) -> None:
        """本輪成功：清除失敗紀錄，開始新的 cycle。"""
        self._failed.clear()


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
    # 統一使用輔助函式獲取金鑰清單
    keys = _get_all_keys()

    if not keys:
        raise RuntimeError("❌ 設定檔中沒有找到任何 API Key，請先設定金鑰。")

    for k in keys:
        # 1. 檢查金鑰是否符合 Google API Key 的標準前綴 "AIza"
        if not k.startswith("AIza"):
            log_error(f"❌ 偵測到無效格式金鑰: {k!r}")
            raise RuntimeError(
                f"❌ 無效的 API Key 格式：{k!r}\n"
                "Gemini API Key 應以 'AIza' 開頭，請檢查您的設定檔。"
            )
        # 2. 檢查金鑰長度（Google API Key 通常為 39-40 個字元）
        if len(k) < 35:
            log_error(f"❌ 偵測到過短的 API 金鑰: {k!r} (長度={len(k)})")
            raise RuntimeError(
                f"❌ API Key 長度異常：{k!r}\n"
                f"長度為 {len(k)}，正常應為 35-45 個字元，請檢查是否輸入正確。"
            )
        # 3. 檢查金鑰字元是否僅包含允許的字元（AIza + 英數字/ dash / underscore）
        if not re.match(r"^AIza[a-zA-Z0-9_-]+$", k):
            log_error(f"❌ 偵測到包含無效字元的 API 金鑰: {k!r}")
            raise RuntimeError(
                f"❌ API Key 包含無效字元：{k!r}\n"
                "僅允許 'AIza' 開頭後接英文字母、數字、 dash(-) 或 underscore(_)。"
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
        if not k.startswith("AIza"):
            raise RuntimeError(
                f"❌ 無效的 API Key 格式：{k!r}\n"
                "請使用 Google AI Studio 產生的 Gemini API Key，"
                "通常應以 'AIza' 字樣開頭。"
            )
        if len(k) < 35:
            raise RuntimeError(
                f"❌ API Key 長度異常：{k!r}\n"
                f"長度為 {len(k)}，正常應為 35-45 個字元，請檢查是否輸入正確。"
            )
        if not re.match(r"^AIza[a-zA-Z0-9_-]+$", k):
            raise RuntimeError(
                f"❌ API Key 包含無效字元：{k!r}\n"
                "僅允許 'AIza' 開頭後接英文字母、數字、 dash(-) 或 underscore(_)。"
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
