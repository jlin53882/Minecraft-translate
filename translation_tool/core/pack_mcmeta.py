"""資源包 ``pack.mcmeta`` 的內容規則（打包頁、流水線與一鍵流程共用）。

所有 format 數值一律是整數，不加引號。依 ``pack_format`` 分三段：

- 1～17（1.20.1 以前）：只有單一 ``pack_format``。
- 18～64（1.20.2～1.21.8）：``pack_format`` 加 ``supported_formats``
  （``min_inclusive``／``max_inclusive``）。
- 65 以上（1.21.9 起）：``supported_formats`` 已移除，改用必填的 ``min_format``／
  ``max_format``；不再寫 ``pack_format``。
"""

from __future__ import annotations

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
    return {"pack": pack}


def describe_pack_format(pack_format: int, max_pack_format: int | None = None) -> str:
    """給使用者看的一行說明，例如「pack_format 34，supported_formats 34–46」。"""
    pack = build_pack_mcmeta("", pack_format, max_pack_format)["pack"]
    if "min_format" in pack:
        return f"min_format {pack['min_format']}，max_format {pack['max_format']}"
    text = f"pack_format {pack['pack_format']}"
    supported = pack.get("supported_formats")
    if supported:
        text += (
            f"，supported_formats {supported['min_inclusive']}"
            f"–{supported['max_inclusive']}"
        )
    return text
