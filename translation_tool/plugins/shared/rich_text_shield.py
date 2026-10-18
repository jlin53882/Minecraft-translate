"""translation_tool/plugins/shared/rich_text_shield.py 模組。

用途：Rich Text 保護層 — 在翻譯前將「不應翻譯」的 KubeJS 格式片段抽出，
翻譯完成後再還原回去。支援：物品ID、彩色碼、超連結、逸出 &、圖片、特殊事件 JSON。

維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from dataclasses import dataclass, field

# =============================================================================
# Patterns（保持為模組層級常數，供外部引用）
# =============================================================================

# 物品ID（#namespace:item 或 #namespace/item — 兩種都支援）
ITEM_ID_PATTERN = re.compile(r"#[a-z0-9_.\-]+[:/][a-z0-9_.\-]+", re.IGNORECASE)

# 標準彩色碼：支援 Minecraft 格式化代碼 0-9, a-f, k-o, r
COLOR_CODE_PATTERN = re.compile(r"&[0-9a-fk-or]", re.IGNORECASE)

# &#RRGGBB 十六進位顏色
HEX_COLOR_PATTERN = re.compile(r"&#[0-9A-Fa-f]{6}", re.IGNORECASE)

# 超連結 URL
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

# 圖片副檔名（純值跳過）
IMAGE_PATTERN = re.compile(r"\.(?:jpe?g|png|gif|bmp|webp|svg|ico)$", re.IGNORECASE)

# 特殊事件 JSON 片段：{\" 開頭
EVENT_JSON_PATTERN = re.compile(r'^\{\\"')

# Shield 佔位符只由本模組產生；品質閘門用它確認模型沒有遺失或重複
# 受保護的 Rich Text 片段。一般文字中的 ``$`` 不會被這個模式誤判。
SHIELD_PLACEHOLDER_PATTERN = re.compile(r"\$(?:C|P|E)\d+\$")

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
    skip_reason: str | None = None  # None = 需要翻譯
    had_color: bool = False
    had_item_ref: bool = False


def shield_placeholder_issues(
    clean_translated: str, shield_pieces: list[ShieldPiece]
) -> list[str]:
    """檢查翻譯結果是否完整保留 Shield 佔位符。

    ``unshield_text`` 對缺少的佔位符採取寬容行為，會直接留下缺少格式的
    譯文。品質閘門必須在還原前比較精確數量，才能攔住顏色碼、物品 ID 等
    不會被一般 Minecraft token 掃描器辨識的 Rich Text 格式。
    """
    if not shield_pieces:
        return []

    expected = Counter(
        piece.placeholder
        for piece in shield_pieces
        if isinstance(getattr(piece, "placeholder", None), str) and piece.placeholder
    )
    if not expected:
        return []

    actual = Counter(SHIELD_PLACEHOLDER_PATTERN.findall(clean_translated or ""))
    issues: list[str] = []
    originals = {
        piece.placeholder: piece.original
        for piece in shield_pieces
        if isinstance(getattr(piece, "placeholder", None), str) and piece.placeholder
    }
    for placeholder, expected_count in expected.items():
        actual_count = actual.get(placeholder, 0)
        if actual_count < expected_count:
            issues.append(
                f"少了 {expected_count - actual_count} 個保護格式佔位符"
                f"「{placeholder}」（原文：{originals[placeholder]}）"
            )
        elif actual_count > expected_count:
            issues.append(
                f"多了 {actual_count - expected_count} 個保護格式佔位符"
                f"「{placeholder}」"
            )

    for placeholder, actual_count in actual.items():
        if placeholder not in expected:
            issues.append(f"多了 {actual_count} 個未知保護格式佔位符「{placeholder}」")
    return issues


def _ordered_placeholders(
    shielded_or_pieces: ShieldedText | list[ShieldPiece],
) -> list[str]:
    """取得依原文位置排序的目前 placeholder；舊快取相容需要這個順序。"""
    clean = getattr(shielded_or_pieces, "clean", "")
    if isinstance(clean, str):
        ordered = SHIELD_PLACEHOLDER_PATTERN.findall(clean)
        if ordered:
            return ordered
    pieces = getattr(shielded_or_pieces, "shields", shielded_or_pieces)
    return [
        piece.placeholder
        for piece in pieces
        if isinstance(getattr(piece, "placeholder", None), str) and piece.placeholder
    ]


def normalize_cached_shield_text(
    clean_translated: str,
    shielded_or_pieces: ShieldedText | list[ShieldPiece],
) -> str:
    """將舊快取中的暫時 placeholder 對齊本次掃描結果。

    新快取保存還原後的正式文字，因此沒有 placeholder 時直接回傳；舊快取
    仍可能保存全域計數器產生的 ``$C0$``／``$P1$``，則依類別與原文位置
    重新對應目前 placeholder，避免既有快取因掃描時機不同而失效。
    """
    if not isinstance(clean_translated, str):
        return str(clean_translated or "")

    actual = SHIELD_PLACEHOLDER_PATTERN.findall(clean_translated)
    expected = _ordered_placeholders(shielded_or_pieces)
    if not actual or not expected:
        return clean_translated

    expected_by_kind: dict[str, list[str]] = {"C": [], "P": [], "E": []}
    for placeholder in expected:
        expected_by_kind[placeholder[1]].append(placeholder)
    actual_by_kind: dict[str, list[str]] = {"C": [], "P": [], "E": []}
    for placeholder in actual:
        actual_by_kind[placeholder[1]].append(placeholder)

    if any(
        len(actual_by_kind[kind]) != len(expected_by_kind[kind])
        for kind in expected_by_kind
    ) or any(
        actual_by_kind[kind] for kind in set(actual_by_kind) - set(expected_by_kind)
    ):
        return clean_translated

    replacements: dict[str, list[str]] = {
        kind: list(values) for kind, values in expected_by_kind.items()
    }
    indexes = {kind: 0 for kind in expected_by_kind}

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        kind = placeholder[1]
        index = indexes[kind]
        indexes[kind] += 1
        return replacements[kind][index]

    return SHIELD_PLACEHOLDER_PATTERN.sub(replace, clean_translated)


def raw_shield_issues(
    translated_text: str, shield_pieces: list[ShieldPiece]
) -> list[str]:
    """檢查已還原快取文字是否仍保留原文 Rich Text 片段。"""
    if not shield_pieces:
        return []

    expected = Counter(
        (piece.category, piece.original)
        for piece in shield_pieces
        if isinstance(getattr(piece, "original", None), str) and piece.original
    )
    actual_shielded = shield_text(translated_text)
    actual = Counter(
        (piece.category, piece.original) for piece in actual_shielded.shields
    )
    issues: list[str] = []
    for token, expected_count in expected.items():
        actual_count = actual.get(token, 0)
        if actual_count < expected_count:
            issues.append(
                f"少了 {expected_count - actual_count} 個保護格式「{token[1]}」"
            )
        elif actual_count > expected_count:
            issues.append(
                f"多了 {actual_count - expected_count} 個保護格式「{token[1]}」"
            )
    for token, actual_count in actual.items():
        if token not in expected:
            issues.append(f"多了 {actual_count} 個未預期保護格式「{token[1]}」")
    return issues


# =============================================================================
# Pre-process（shield）
# =============================================================================

_counter_color: int = 0
_counter_item: int = 0
_counter_escaped: int = 0
_counter_lock = threading.Lock()


def _next_color_placeholder() -> str:
    global _counter_color
    with _counter_lock:
        ph = f"$C{_counter_color}$"
        _counter_color += 1
    return ph


def _next_item_placeholder() -> str:
    global _counter_item
    with _counter_lock:
        ph = f"$P{_counter_item}$"
        _counter_item += 1
    return ph


def _next_escaped_placeholder() -> str:
    global _counter_escaped
    with _counter_lock:
        ph = f"$E{_counter_escaped}$"
        _counter_escaped += 1
    return ph


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
