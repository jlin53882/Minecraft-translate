"""translation_tool/core/lm_config_rules.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import re
import json
from pathlib import Path
from typing import Any
from ..utils.config_manager import load_config
import logging
logger = logging.getLogger(__name__)




# =========================
# 1. 提示詞與配置
# =========================
    
# 目前使用的 API Key 索引
_current_key_index = 0 

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

def get_current_api_key() -> str:
    """
    從金鑰池中取得目前正在使用的 API 金鑰。
    
    此函式依賴於全域索引變數 `_current_key_index`，
    確保在執行翻譯請求或進行輪替（Rotate）時，始終能獲取到當前設定的金鑰。
    
    回傳:
        str: 目前指向的 Gemini API 金鑰字串。
    """
    keys = _get_all_keys()
    if not keys:
        logger.error("❌ 設定檔中沒有找到任何有效的 API Key")
        return ""
    
    # 加上一個防護：避免 index 越界
    safe_index = min(_current_key_index, len(keys) - 1)
    return keys[safe_index]

def rotate_api_key():
    """
    切換至下一個可用的 API Key。

    使用時機：
    - 當遇到「配額 / 速率」相關錯誤時（例如 429 RESOURCE_EXHAUSTED）
    - 當遇到「單一 Key 暫時不可用，但仍有備援 Key」的情況

    ⚠️ 不適用於：
    - 400 INVALID_ARGUMENT（payload / schema 錯誤，換 Key 無效）
    - 邏輯錯誤或程式 bug
    - API Key 格式本身錯誤（例如 "token" 這種假 key）

    行為說明：
    - 內部透過 _current_key_index 指向下一個 Key
    - 若已經沒有下一個 Key，直接丟出 RuntimeError
      表示「所有 Key 都不可用，流程必須中止」

    Raises:
        RuntimeError:
            當所有 API Key 都已嘗試過，且無法再切換時拋出。
            這通常代表：
            - 所有 Key 的配額都已用盡（RPD / RPM exhausted），或
            - 程式被錯誤地要求在「不該換 Key 的情況」下換 Key
    切換至下一個可用的 API Key。
    回傳: True (切換成功) / False (已無可用 Key)
    """
    global _current_key_index
    keys = _get_all_keys()

    # 檢查是否還有下一個 Key 可以切換
    if _current_key_index + 1 >= len(keys):
        logger.error("❌ 所有 API Key 已用盡（RPD exhausted）")
        return False

    # 切換至下一個 API Key
    _current_key_index += 1
    logger.info(f"🔁 切換 API Key → index {_current_key_index}")
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
            logger.error(f"❌ 偵測到無效格式金鑰: {k!r}")
            raise RuntimeError(
                f"❌ 無效的 API Key 格式：{k!r}\n"
                "Gemini API Key 應以 'AIza' 開頭，請檢查您的設定檔。"
            )
    
    logger.info(f"✅ 金鑰格式驗證通過，共載入 {len(keys)} 組金鑰。")

def validate_api_keys_from_ui(keys: list[str]): #ui 專用
    """處理此函式的工作（細節以程式碼為準）。
    
    回傳：None
    """
    for k in keys:
        if not k or not k.startswith("AIza"):
            raise RuntimeError(
                f"❌ 無效的 API Key 格式：{k!r}\n"
                "請使用 Google AI Studio 產生的 Gemini API Key，"
                "通常應以 'AIza' 字樣開頭。"
            )

# =========================
# 2. Regex 規則定義
# =========================
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

# ✅ 純羅馬數字（I, II, III, IV, V, ... / 允許前後空白）
ROMAN_NUMERAL_PATTERN = re.compile(
    r"^\s*M{0,4}(CM|CD|D?C{0,3})"
    r"(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\s*$",
    re.IGNORECASE
)

# ✅ 新增：純數字模式 (包含整數、浮點數、正負號與千分位逗號)
DIGIT_PATTERN = re.compile(
    r"^\s*[+-]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?\s*$"
)

TECH_PATTERN = re.compile(
    r"""
    ^[a-z0-9_\-.]+:[a-z0-9_\-./]+$ |   # minecraft:diamond
    ^[a-z0-9_\-.]+(\.[a-z0-9_\-.]+)+$ | # some.mod.key.path
    """,
    re.VERBOSE
)

# 例如 booklet.section.entry 之類的 lang key 引用
LANG_KEY_REF_PATTERN = re.compile(
    r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$",
    re.IGNORECASE # 不區分大小寫
)
# 完整  $(...)  token
TOKEN_PATTERN = re.compile(r"\$\([^)]+\)")

# 需要跳過翻譯的文字（你指定的類型）
HASH_PREFIX_PATTERN = re.compile(r"^\s*#")  # 任何 # 開頭（含前置空白）

def needs_translation_text(s: str) -> bool:
    """處理此函式的工作（細節以程式碼為準）。
    
    回傳：依函式內 return path。
    """
    if not s or not isinstance(s, str):
        return False

    # 已經是中文
    if contains_cjk(s):
        return False

    # 純數字 / 符號
    if s.strip().isdigit():
        return False

    # 常見不該翻的 token
    if s.startswith("§") or s.startswith("$("):
        return False

    # 還有英文 → 需要翻
    return True

def value_fully_translated(value) -> bool:
    """
    判斷一個值是否「已完全翻譯完成」。

    主要用途：
    - 用於翻譯快取（cache）命中判斷
    - 決定某一個 key / 欄位是否可以「直接使用 cache」
      而不需要再次送 API 翻譯

    判斷邏輯說明：
    1. 若 value 是字串（str）：
       - 呼叫 needs_translation_text()
       - 若該字串「不需要翻譯」，表示已是中文或應保留原文 → 視為已翻譯

    2. 若 value 是字串列表（list[str]）：
       - 逐一檢查每個元素
       - 只要其中「任一字串仍需要翻譯」
         就判定整個 list 尚未完全翻譯
       - 這是「保守策略」，避免 list 中出現中英混雜的情況

    3. 其他型別（例如 dict / int / None）：
       - 不屬於翻譯目標
       - 視為已完成翻譯，直接回傳 True

    為什麼要這樣設計：
    - Cache 命中必須「100% 安全」
    - 寧願少命中、重新翻譯
      也不要誤判為已翻譯而留下英文殘留

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
                return False   # ⭐ 一票否決制
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
    #pattern = r"\b(" + "|".join(escaped) + r")\b"
    pattern = r"^\s*(?:" + "|".join(escaped) + r")\s*$"

    # 編譯為不區分大小寫的正規表達式
    return re.compile(pattern, re.IGNORECASE)


# =========================
# 值是否值得翻譯（核心判斷）
def is_value_translatable(value: Any, *, is_lang: bool = False) -> bool:
    """判斷此函式的工作（細節以程式碼為準）。
    
    - 主要包裝：`strip`, `build_skip_terms_pattern`
    
    回傳：依函式內 return path。
    """
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

    # 太短且無空白，通常不是顯示文字
    if is_lang and len(s) <= 3 and " " not in s:
        return False
    
        # 避開 #...（#heading、#title）
    if HASH_PREFIX_PATTERN.match(s):
        return False
    
    # 需要跳過翻譯的關鍵字（可自由擴充）
    SKIP_TERMS=load_config().get("lm_translator", {}).get("translator", {}).get("skip_terms", [])
    #print("config skip_terms:",SKIP_TERMS)
    # 生成跳過關鍵字的 regex pattern
    SKIP_TERMS_PATTERN = build_skip_terms_pattern(SKIP_TERMS)

    # 避開指定關鍵字（API documentation / Discord）
    if (
        is_lang
        and SKIP_TERMS_PATTERN.search(s)
        and len(s) <= 5        # ⭐ 關鍵
    ):
        logger.debug("SKIP[skip_terms] len=%d text=%r", len(s), s)
        return False
    
    # 避開羅馬數字
    if is_lang and ROMAN_NUMERAL_PATTERN.fullmatch(s):
        return False
    
    # 避開純數字
    if is_lang and DIGIT_PATTERN.fullmatch(s):
        return False



    return True

# =========================
# 可翻譯欄位判斷
# =========================
def is_translatable_field(key: str) -> bool:
    """
    只要欄位名稱包含任一文字關鍵字，就視為可翻譯欄位
    """
    key_lower = key.lower()
    # 允許翻譯的欄位（包含你自訂的各種文字欄位） 關鍵字版本
    TRANSLATABLE_KEYWORDS=load_config().get("lm_translator", {}).get("translator", {}).get("translatable_keywords", [])
    return any(keyword in key_lower for keyword in TRANSLATABLE_KEYWORDS)
