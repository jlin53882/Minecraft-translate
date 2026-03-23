"""translation_tool/plugins/shared/rich_text_shield.py 模組。

用途：Rich Text 保護層 — 在翻譯前將「不應翻譯」的 KubeJS 格式片段抽出，
翻譯完成後再還原回去。支援：物品ID、彩色碼、超連結、逸出 &、圖片、特殊事件 JSON。

維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# =============================================================================
# Patterns（保持為模組層級常數，供外部引用）
# =============================================================================

# 物品ID（#namespace:item 或 #namespace/item — 兩種都支援）
ITEM_ID_PATTERN = re.compile(r"#[a-z0-9_.\-]+[:/][a-z0-9_.\-]+", re.IGNORECASE)

# 標準彩色碼：&a ~ &o（不含 k 的 16 進位格式碼）
COLOR_CODE_PATTERN = re.compile(r"&[a-f0-9k-o]", re.IGNORECASE)

# &#RRGGBB 十六進位顏色
HEX_COLOR_PATTERN = re.compile(r"&#[0-9A-Fa-f]{6}", re.IGNORECASE)

# 超連結 URL
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

# 圖片副檔名（純值跳過）
IMAGE_PATTERN = re.compile(r"\.(?:jpe?g|png|gif|bmp|webp|svg|ico)$", re.IGNORECASE)

# 特殊事件 JSON 片段：{\" 開頭
EVENT_JSON_PATTERN = re.compile(r'^\{\\"')

# 翻頁/點擊事件：{@...}
PAGEBREAK_PATTERN = re.compile(r"\{@[a-zA-Z_][a-zA-Z0-9_]*\}")

# 逸出 &（\\\\&  → PPP）
# 正則：反斜線 + &（反斜線本身可能被轉義過）
ESCAPED_AND_PATTERN = re.compile(r"\\&")


# =============================================================================
# ShieldPiece & ShieldedText
# =============================================================================


@dataclass
class ShieldPiece:
    """單一保護片段。

    Attributes:
        placeholder: 佔位符（原文被取代後的標記）。
        original: 原始文字片段。
        category: 類別（"color", "hex_color", "item_id", "url", "escaped_and"）。
    """

    placeholder: str
    original: str
    category: str


@dataclass
class ShieldedText:
    """經過 shield 處理的文字對象。

    Attributes:
        clean: 供翻譯引擎處理的乾淨文字。
        shields: 保護片段對照表，key 為佔位符，value 為原始片段。
        skip_reason: 如果此文字應該跳過翻譯，記錄原因（"image", "url", "event", "pagebreak", "empty"）。
        had_color: 是否包含彩色碼（用於日誌記錄）。
        had_item_ref: 是否包含物品ID引用。
    """

    clean: str
    shields: list[ShieldPiece] = field(default_factory=list)
    skip_reason: Optional[str] = None  # None = 需要翻譯
    had_color: bool = False
    had_item_ref: bool = False


# =============================================================================
# Pre-process（shield）
# =============================================================================

_counter_color: int = 0
_counter_item: int = 0
_counter_escaped: int = 0


def _next_color_placeholder() -> str:
    global _counter_color
    ph = f"$C{_counter_color}$"
    _counter_color += 1
    return ph


def _next_item_placeholder() -> str:
    global _counter_item
    ph = f"$P{_counter_item}$"
    _counter_item += 1
    return ph


def _next_escaped_placeholder() -> str:
    global _counter_escaped
    ph = f"$E{_counter_escaped}$"
    _counter_escaped += 1
    return ph


def _reset_counters() -> None:
    """重置計數器（主要用於測試）。"""
    global _counter_color, _counter_item, _counter_escaped
    _counter_color = 0
    _counter_item = 0
    _counter_escaped = 0


def shield_text(text: str) -> ShieldedText:
    """
    掃描 text 中的「不應翻譯」區段，用 Placeholder 取代後回傳乾淨文本。

    支援：物品ID (#namespace/item)、彩色碼 (&a~f / &#RRGGBB)、
          超連結、逸出 \\&、圖片副檔名、特殊事件 JSON、翻頁事件。

    處理順序（對應 FTBQL 7 情景）：
      1. 圖片副檔名 → skip_reason="image"
      2. 超連結 URL → skip_reason="url"
      3. 特殊事件 JSON {\"} → skip_reason="event"
      4. 翻頁/點擊事件 {@...} → skip_reason="pagebreak"
      5. 逸出 \\& → PPP 保護
      6. &#RRGGBB 十六進位顏色 → $C{N}$ 保護
      7. &a~f 彩色碼 → $C{N}$ 保護
      8. 物品ID #namespace/item → $P{N}$ 保護

    Args:
        text: 原始 KubeJS 字串值。

    Returns:
        ShieldedText（含 clean / shields / skip_reason / had_color / had_item_ref）。
    """
    global _counter_color, _counter_item, _counter_escaped

    if not isinstance(text, str):
        return ShieldedText(clean=str(text), shields=[], skip_reason=None)

    # ── 0. 空白文字 → skip ──────────────────────────────────────────────────
    stripped = text.strip()
    if not stripped:
        return ShieldedText(clean=text, shields=[], skip_reason="empty")

    # ── 1. 圖片副檔名 → 整段跳過 ──────────────────────────────────────────────
    if IMAGE_PATTERN.search(text):
        return ShieldedText(clean=text, shields=[], skip_reason="image")

    # ── 2. 超連結 URL → 整段跳過 ──────────────────────────────────────────────
    if URL_PATTERN.search(text):
        return ShieldedText(clean=text, shields=[], skip_reason="url")

    # ── 3. 特殊事件 JSON {\"} → 跳過 ─────────────────────────────────────────
    if EVENT_JSON_PATTERN.match(text):
        return ShieldedText(clean=text, shields=[], skip_reason="event")

    # ── 4. 翻頁/點擊事件 {@...} → 跳過 ────────────────────────────────────────
    if PAGEBREAK_PATTERN.search(text):
        return ShieldedText(clean=text, shields=[], skip_reason="pagebreak")

    # ── 處理型：開始掃描各類保護片段 ─────────────────────────────────────────
    shields: list[ShieldPiece] = []
    result = text

    # 5. 逸出 \\& → PPP
    for match in reversed(list(ESCAPED_AND_PATTERN.finditer(result))):
        ph = _next_escaped_placeholder()
        original = match.group()
        shields.append(
            ShieldPiece(placeholder=ph, original=original, category="escaped_and")
        )
        result = result[: match.start()] + ph + result[match.end() :]

    # 6. &#RRGGBB 十六進位顏色
    for match in reversed(list(HEX_COLOR_PATTERN.finditer(result))):
        ph = _next_color_placeholder()
        original = match.group()
        shields.append(
            ShieldPiece(placeholder=ph, original=original, category="hex_color")
        )
        result = result[: match.start()] + ph + result[match.end() :]

    # 7. &a~f 標準彩色碼
    for match in reversed(list(COLOR_CODE_PATTERN.finditer(result))):
        ph = _next_color_placeholder()
        original = match.group()
        shields.append(ShieldPiece(placeholder=ph, original=original, category="color"))
        result = result[: match.start()] + ph + result[match.end() :]

    # 8. 物品ID #namespace/item
    for match in reversed(list(ITEM_ID_PATTERN.finditer(result))):
        ph = _next_item_placeholder()
        original = match.group()
        shields.append(
            ShieldPiece(placeholder=ph, original=original, category="item_id")
        )
        result = result[: match.start()] + ph + result[match.end() :]

    had_color = any(sp.category in ("color", "hex_color") for sp in shields)
    had_item_ref = any(sp.category == "item_id" for sp in shields)

    return ShieldedText(
        clean=result,
        shields=shields,
        skip_reason=None,
        had_color=had_color,
        had_item_ref=had_item_ref,
    )


# =============================================================================
# Post-process（unshield）
# =============================================================================


def unshield_text(clean_translated: str, shield_pieces: list[ShieldPiece]) -> str:
    """
    將已翻譯的文本中的 Placeholder 還原為原始不應翻譯的內容。

    Args:
        clean_translated: 翻譯引擎處理過的文字（可能含 $C{N}$ / $P{N}$ / $E{N}$ 等佔位符）。
        shield_pieces: shield_text() 產出的 shields 列表（需按順序還原）。

    Returns:
        還原後的最終文字。
    """
    if not shield_pieces:
        return clean_translated

    # 按 placeholder 長度降序還原（避免部分匹配問題，如 $C0$ 包含在 $C10$ 中）
    sorted_pieces = sorted(
        shield_pieces, key=lambda sp: len(sp.placeholder), reverse=True
    )

    result = clean_translated
    for sp in sorted_pieces:
        result = result.replace(sp.placeholder, sp.original)

    return result


# =============================================================================
# JSON Escape 修補（移植自 FTBQL add_escape_quotes）
# =============================================================================


def add_escape_quotes(text: str) -> str:
    """
    修補缺失的轉義符。

    將未轉義的 " 改為 \\"（讓 JSON 值保持正確格式）。
    特殊處理：\\\\" → \\\"（BAIDU API 會把 \\\" 還原成 \\"，需修正過度轉義）。

    Args:
        text: 可能含未轉義引號的文字。

    Returns:
        修補後的文字。
    """
    # 將不以 \ 開頭的 " 改為 \"
    pattern = r'(?<!\\)"'  # 不以 \ 開頭的 "
    result = re.sub(pattern, r'\\"', text)
    # 修正過度轉義：\\\\" → \\\"
    result = result.replace('\\\\"', '\\"')
    return result
