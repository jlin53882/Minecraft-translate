"""資源包 ``pack.mcmeta`` 的內容規則（打包頁、流水線與一鍵流程共用）。

所有 format 數值一律是整數，不加引號。依 ``pack_format`` 分三段：

- 1～17（1.20.1 以前）：只有單一 ``pack_format``。
- 18～64（1.20.2～1.21.8）：``pack_format`` 加 ``supported_formats``
  （``min_inclusive``／``max_inclusive``）。
- 65 以上（1.21.9 起）：``supported_formats`` 已移除，改用必填的 ``min_format``／
  ``max_format``；不再寫 ``pack_format``。
- 最大版本跨過 64（pack_format 18～64 卻要支援 65 以上）：同時寫 ``pack_format``、
  ``supported_formats`` 與 ``min_format``／``max_format``（新舊版遊戲各讀自己認得的欄位）。
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_PACK_FORMAT = 34  # 1.21～1.21.1
SUPPORTED_FORMATS_SINCE = 18  # 1.20.2：從此版本起才有 supported_formats
MIN_MAX_FORMAT_SINCE = 65  # 1.21.9：supported_formats 移除，改用 min_format／max_format
SUPPORTED_FORMATS_CEILING = 46  # 18～46 的 max_inclusive（1.21.4）
SUPPORTED_FORMATS_LAST = (
    64  # 47～64 的 max_inclusive（1.21.8，supported_formats 的最後版本）
)
MAX_FORMAT_CEILING = 9999  # 65 以上的 max_format：涵蓋之後的版本
MAX_CUSTOM_PACK_FORMAT = 9999


def parse_pack_format(text: object) -> int | None:
    """解析使用者輸入的 pack_format；空白回傳 None，非正整數拋出 ValueError。"""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("pack_format 必須是正整數")
    value = int(raw)
    if not 1 <= value <= MAX_CUSTOM_PACK_FORMAT:
        raise ValueError(f"pack_format 必須介於 1 與 {MAX_CUSTOM_PACK_FORMAT}")
    return value


def has_max_format(pack_format: int) -> bool:
    """該 pack_format 是否會寫出「最大版本」欄位（supported_formats 或 max_format）。"""
    return int(pack_format) >= SUPPORTED_FORMATS_SINCE


def default_max_format(pack_format: int) -> int:
    """未指定最大版本時的預設值（供 supported_formats.max_inclusive 或 max_format）。"""
    value = int(pack_format)
    if value >= MIN_MAX_FORMAT_SINCE:
        return MAX_FORMAT_CEILING
    if value <= SUPPORTED_FORMATS_CEILING:
        return SUPPORTED_FORMATS_CEILING
    return SUPPORTED_FORMATS_LAST


def build_pack_mcmeta(
    description: str, pack_format: int, max_pack_format: int | None = None
) -> dict[str, Any]:
    """組出 ``pack.mcmeta`` 內容（皆為整數，不加引號）。

    ``max_pack_format`` 是使用者指定的最大使用版本；``None`` 用預設值。
    小於 ``pack_format`` 時拋出 ValueError。1.20.1 以前沒有最大版本欄位，會忽略。
    """
    value = int(pack_format)
    if max_pack_format is not None and max_pack_format < value:
        raise ValueError(f"最大版本 {max_pack_format} 不能小於 pack_format {value}")
    ceiling = default_max_format(value) if max_pack_format is None else max_pack_format
    if value >= MIN_MAX_FORMAT_SINCE:
        return {
            "pack": {
                "description": description,
                "min_format": value,
                "max_format": ceiling,
            }
        }
    pack: dict[str, Any] = {"pack_format": value, "description": description}
    if has_max_format(value):
        pack["supported_formats"] = {
            "min_inclusive": value,
            "max_inclusive": max(value, ceiling),
        }
        if ceiling >= MIN_MAX_FORMAT_SINCE:
            pack["min_format"] = value
            pack["max_format"] = ceiling
    return {"pack": pack}


def describe_pack_format(pack_format: int, max_pack_format: int | None = None) -> str:
    """給使用者看的一行說明，例如「pack_format 34，supported_formats 34–46」。"""
    pack = build_pack_mcmeta("", pack_format, max_pack_format)["pack"]
    parts = []
    if "pack_format" in pack:
        parts.append(f"pack_format {pack['pack_format']}")
    supported = pack.get("supported_formats")
    if supported:
        parts.append(
            f"supported_formats {supported['min_inclusive']}"
            f"–{supported['max_inclusive']}"
        )
    if "min_format" in pack:
        parts.append(
            f"min_format {pack['min_format']}，max_format {pack['max_format']}"
        )
    return "，".join(parts)


_VERSION_KEYS = ("pack_format", "supported_formats", "min_format", "max_format")


def _as_int(value: Any) -> int | None:
    """非負整數（含全是 ASCII 數字的字串）轉成整數；其他回傳 None。bool 不算整數。"""
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def _repair_int_list(value: Any, max_len: int) -> list[int] | None:
    """1～``max_len`` 個整數（或整數字串）的陣列；不合格回傳 None。"""
    if not isinstance(value, list) or not 1 <= len(value) <= max_len:
        return None
    ints = [_as_int(item) for item in value]
    return None if any(item is None for item in ints) else ints


def _repair_format(key: str, value: Any) -> Any | None:
    """修正單一 format 欄位的型別；無法修正回傳 None（呼叫端會丟掉該欄位）。"""
    if key == "pack_format":
        return _as_int(value)
    if key in ("min_format", "max_format"):  # 1.21.9 起可寫整數或 [主版本, 次版本]
        single = _as_int(value)
        return single if single is not None else _repair_int_list(value, 2)
    # supported_formats：整數、[最小, 最大]，或 {min_inclusive, max_inclusive}
    if isinstance(value, dict):
        low = _as_int(value.get("min_inclusive"))
        high = _as_int(value.get("max_inclusive"))
        if low is None or high is None or high < low:
            return None
        return {**value, "min_inclusive": low, "max_inclusive": high}
    single = _as_int(value)
    if single is not None:
        return single
    pair = _repair_int_list(value, 2)
    return pair if pair is not None and (len(pair) == 1 or pair[0] <= pair[1]) else None


def pack_description_text(mcmeta_text: str) -> str | None:
    """``pack.mcmeta`` 文字中 ``pack.description`` 的純文字（供預覽）；讀不出來回傳 None。

    description 可以是字串，或文字元件（物件／陣列）；元件只取其中的 ``text`` 內容。
    """
    try:
        pack = json.loads(mcmeta_text)["pack"]
        description = pack["description"]
    except (ValueError, KeyError, TypeError):
        return None

    def flatten(node: Any) -> str:
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return "".join(flatten(item) for item in node)
        if isinstance(node, dict):
            return flatten(node.get("text", "")) + flatten(node.get("extra", []))
        return ""

    return flatten(description)


def _format_range(value: Any) -> tuple[Any, Any] | None:
    """supported_formats 的 (最小, 最大)；整數視為單一版本。"""
    if isinstance(value, dict):
        return value["min_inclusive"], value["max_inclusive"]
    if isinstance(value, list):
        return value[0], value[-1]
    return value, value


def _version_tuple(value: Any) -> tuple[int, int]:
    """min_format／max_format 的比較值；整數視為 [主版本, 0]。"""
    if isinstance(value, list):
        return value[0], value[1] if len(value) > 1 else 0
    return value, 0


def _drop_inconsistent_formats(pack: dict[str, Any], notes: list[str]) -> None:
    """型別修好後檢查欄位之間的版本關係；矛盾的欄位移除，supported_formats 依 pack_format 重設。"""
    if (
        "min_format" in pack
        and "max_format" in pack
        and _version_tuple(pack["max_format"]) < _version_tuple(pack["min_format"])
    ):
        notes.append(
            f"max_format {json.dumps(pack['max_format'])} 小於 min_format "
            f"{json.dumps(pack['min_format'])}，已移除 min_format／max_format"
        )
        del pack["min_format"], pack["max_format"]
    if "pack_format" in pack and "supported_formats" in pack:
        low, high = _format_range(pack["supported_formats"])
        if not low <= pack["pack_format"] <= high:
            notes.append(
                f"supported_formats 不包含 pack_format {pack['pack_format']}，"
                "已依 pack_format 重設 supported_formats"
            )
            del pack["supported_formats"]
            version = pack["pack_format"]
            if SUPPORTED_FORMATS_SINCE <= version < MIN_MAX_FORMAT_SINCE:
                pack["supported_formats"] = build_pack_mcmeta("", version)["pack"][
                    "supported_formats"
                ]


def repair_source_pack_mcmeta(
    text: str, generated: dict[str, Any]
) -> tuple[str | None, list[str]]:
    """檢查並修正來源資料夾裡既有的 ``pack.mcmeta`` 文字。

    回傳 ``(內容, 說明)``：
    - 內容為 ``None``：JSON 無法解析或沒有 ``pack`` 物件，說明只有一項原因；呼叫端改用
      ``generated``（UI 設定產生的內容）。
    - 沒有任何問題：內容是原文（逐字不變），說明為空。
    - format 欄位寫成字串、範圍反向等問題：把數字字串改成整數、丟掉無法修正的欄位；
      缺少可用的版本欄位或 ``description`` 時，用 ``generated`` 的補上。來源檔其他欄位
      （overlays、filter 等自訂設定）原樣保留。
    """
    try:
        data = json.loads(text)
    except ValueError as ex:
        return None, [f"不是有效的 JSON：{ex}"]
    pack = data.get("pack") if isinstance(data, dict) else None
    if not isinstance(pack, dict):
        return None, ["缺少 pack 物件"]

    notes: list[str] = []
    for key in _VERSION_KEYS:
        if key not in pack:
            continue
        original = pack[key]
        fixed = _repair_format(key, original)
        if fixed is None:
            del pack[key]
            notes.append(f"{key} 的值 {original!r} 無法使用，已移除")
        elif fixed != pack[key]:
            notes.append(f"{key} 已改成 {json.dumps(fixed)}")
            pack[key] = fixed

    _drop_inconsistent_formats(pack, notes)
    if "pack_format" not in pack and not (
        "min_format" in pack and "max_format" in pack
    ):
        for key in _VERSION_KEYS:
            pack.pop(key, None)
        pack.update({k: v for k, v in generated["pack"].items() if k in _VERSION_KEYS})
        notes.append("缺少可用的版本欄位，已用 UI 設定補上")
    if not isinstance(pack.get("description"), (str, dict, list)):
        pack["description"] = generated["pack"]["description"]
        notes.append("缺少 description，已用 UI 設定補上")
    if not notes:
        return text, []
    return json.dumps(data, ensure_ascii=False, indent=2), notes
