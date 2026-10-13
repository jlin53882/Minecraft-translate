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


_ANY_MINOR = (
    0x7FFFFFFF  # max_format 寫成整數時，涵蓋該主版本的所有次版本（遊戲定義的上限）
)


def _version_bound(value: Any, *, upper: bool) -> tuple[int, int]:
    """min_format／max_format 的比較值（主版本, 次版本）。

    整數（或單一元素的陣列）只指定主版本：當下限等於次版本 0，當上限則涵蓋該主版本的
    所有次版本。
    """
    if isinstance(value, list) and len(value) > 1:
        return value[0], value[1]
    major = value[0] if isinstance(value, list) else value
    return major, _ANY_MINOR if upper else 0


def _drop_inconsistent_formats(pack: dict[str, Any], notes: list[str]) -> None:
    """型別修好後檢查欄位之間的版本關係；矛盾的欄位移除，supported_formats 依 pack_format 重設。"""
    if ("min_format" in pack) != ("max_format" in pack):
        lone = "min_format" if "min_format" in pack else "max_format"
        notes.append(f"{lone} 沒有搭配另一個上下限欄位，已移除")
        del pack[lone]
    if "min_format" in pack and _version_bound(
        pack["max_format"], upper=True
    ) < _version_bound(pack["min_format"], upper=False):
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


def _major(value: Any) -> int:
    return value[0] if isinstance(value, list) else value


def _sync_old_fields_with_new(pack: dict[str, Any], notes: list[str]) -> None:
    """新舊兩組欄位的範圍不一致時，以 ``min_format``／``max_format`` 為準校正舊欄位。

    只在新欄位真的涵蓋 65 以上時才處理（此時舊欄位只是給舊版遊戲看的對應範圍）：
    新範圍從 65 起，舊欄位移除；否則 ``supported_formats`` 必須等於新範圍截到 64 的部分，
    ``pack_format`` 必須落在其中。
    """
    if "min_format" not in pack or _major(pack["max_format"]) < MIN_MAX_FORMAT_SINCE:
        return
    low = _major(pack["min_format"])
    old_keys = [k for k in ("pack_format", "supported_formats") if k in pack]
    if not old_keys:
        return
    if low >= MIN_MAX_FORMAT_SINCE:
        for key in old_keys:
            del pack[key]
        notes.append(
            f"min_format／max_format 從 {low} 起，與 {'／'.join(old_keys)} 的範圍不一致，"
            f"已移除 {'／'.join(old_keys)}"
        )
        return
    expected = (low, SUPPORTED_FORMATS_LAST)
    if (
        "supported_formats" in pack
        and _format_range(pack["supported_formats"]) != expected
    ):
        pack["supported_formats"] = {
            "min_inclusive": expected[0],
            "max_inclusive": expected[1],
        }
        notes.append(
            f"supported_formats 與 min_format／max_format 的範圍不一致，已改成 {expected[0]}–{expected[1]}"
        )
    if "pack_format" in pack and not expected[0] <= pack["pack_format"] <= expected[1]:
        pack["pack_format"] = low
        notes.append(f"pack_format 不在 min_format 起的範圍內，已改成 {low}")


def _normalize_format_fields(pack: dict[str, Any], notes: list[str]) -> None:
    """依實際支援的版本範圍，讓新舊兩組欄位互相搭配（1.21.9 起的規則）。

    - 只支援 65 以上：不能保留 ``pack_format``／``supported_formats``。
    - 只支援 64 以下：保留來源已有的 ``min_format``／``max_format``，不主動補上。
    - 跨越 64／65：新舊兩組欄位都要有，缺的依範圍補上。
    """
    lows: list[int] = []
    highs: list[int] = []
    if "supported_formats" in pack:
        low, high = _format_range(pack["supported_formats"])
        lows.append(low)
        highs.append(high)
    elif "pack_format" in pack:
        lows.append(pack["pack_format"])
        highs.append(pack["pack_format"])
    if "pack_format" in pack and "supported_formats" in pack:
        lows.append(pack["pack_format"])
        highs.append(pack["pack_format"])
    if "min_format" in pack:
        lows.append(_major(pack["min_format"]))
        highs.append(_major(pack["max_format"]))
    if not lows:
        return
    low, high = min(lows), max(highs)

    if low >= MIN_MAX_FORMAT_SINCE:
        for key in ("pack_format", "supported_formats"):
            if pack.pop(key, None) is not None:
                notes.append(f"只支援 {MIN_MAX_FORMAT_SINCE} 以上的版本，已移除 {key}")
    else:
        if "pack_format" not in pack:
            pack["pack_format"] = low
            notes.append(f"缺少 pack_format，已補上 {low}")
        if (
            low >= SUPPORTED_FORMATS_SINCE
            and high >= MIN_MAX_FORMAT_SINCE
            and "supported_formats" not in pack
        ):
            pack["supported_formats"] = {
                "min_inclusive": low,
                "max_inclusive": min(high, SUPPORTED_FORMATS_LAST),
            }
            notes.append("缺少 supported_formats，已依支援範圍補上")
    if high >= MIN_MAX_FORMAT_SINCE and "min_format" not in pack:
        pack["min_format"] = low
        pack["max_format"] = high if high != low else default_max_format(high)
        notes.append("缺少 min_format／max_format，已依支援範圍補上")


_OVERLAY_KEYS = (
    ("supported_formats", "formats"),
    ("min_format", "min_format"),
    ("max_format", "max_format"),
)


def _overlay_view(
    entry: Any, index: int, notes: list[str]
) -> tuple[dict[str, Any] | None, str | None]:
    """overlay 項目的版本欄位（``formats`` 以 ``supported_formats`` 的名稱放進 view），
    已修正型別與前後矛盾；無法安全修復時回傳 (None, 原因)。"""
    where = f"overlays.entries[{index}]"
    if not isinstance(entry, dict):
        return None, f"{where} 不是物件"
    view: dict[str, Any] = {}
    for key, source_key in _OVERLAY_KEYS:
        if source_key not in entry:
            continue
        fixed = _repair_format(key, entry[source_key])
        if fixed is None:
            return None, f"{where} 的 {source_key} 值 {entry[source_key]!r} 無法使用"
        if fixed != entry[source_key]:
            notes.append(f"{where}：{source_key} 已改成 {json.dumps(fixed)}")
        view[key] = fixed
    local: list[str] = []
    _drop_inconsistent_formats(view, local)
    if "min_format" in view and _major(view["min_format"]) < MIN_MAX_FORMAT_SINCE:
        _sync_old_fields_with_new(
            view, local
        )  # 新範圍從 65 起時 formats 是否保留由整份清單決定
    notes.extend(f"{where}：{n}" for n in local)
    if not view:
        return None, f"{where} 沒有任何版本欄位"
    return view, None


def _overlay_range(view: dict[str, Any]) -> tuple[int, int]:
    """overlay 項目涵蓋的 (最小, 最大) 主版本；min／max_format 優先，否則取 formats。"""
    if "min_format" in view:
        return _major(view["min_format"]), _major(view["max_format"])
    return _format_range(view["supported_formats"])


def _repair_overlays(data: dict[str, Any], notes: list[str]) -> str | None:
    """``overlays.entries`` 的版本欄位（1.21.9 起的規則）；回傳不為 None 表示無法安全修復。

    - 每個項目都要有 ``min_format``／``max_format``，缺少時由 ``formats`` 推得。
    - 只要有任何項目涵蓋 65 以下的版本，所有項目都要有 ``formats``；沒有項目涵蓋時
      ``formats`` 必須移除。
    - 項目的版本欄位無法使用、範圍反向或完全沒有版本欄位時不猜測，整份來源改用 UI 設定。
    其他欄位（``directory`` 等）原樣保留。
    """
    overlays = data.get("overlays")
    entries = overlays.get("entries") if isinstance(overlays, dict) else None
    if not isinstance(entries, list):
        return None
    views = []
    for index, entry in enumerate(entries):
        view, problem = _overlay_view(entry, index, notes)
        if view is None:
            return problem
        views.append(view)
    ranges = [_overlay_range(view) for view in views]
    any_old = any(low < MIN_MAX_FORMAT_SINCE for low, _high in ranges)
    for index, (entry, view, (low, high)) in enumerate(zip(entries, views, ranges)):
        where = f"overlays.entries[{index}]"
        if "min_format" not in view:
            view["min_format"], view["max_format"] = low, high
            notes.append(f"{where}：缺少 min_format／max_format，已依 formats 補上")
        if any_old and "supported_formats" not in view:
            view["supported_formats"] = {
                "min_inclusive": low,
                "max_inclusive": min(high, SUPPORTED_FORMATS_LAST)
                if low < MIN_MAX_FORMAT_SINCE
                else high,
            }
            notes.append(f"{where}：清單中有項目涵蓋舊版，已補上 formats")
        elif not any_old and "supported_formats" in view:
            del view["supported_formats"]
            notes.append(f"{where}：沒有項目涵蓋舊版，已移除 formats")
        for key, source_key in _OVERLAY_KEYS:
            entry.pop(source_key, None)
            if key in view:
                entry[source_key] = view[key]
    return None


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
    _sync_old_fields_with_new(pack, notes)
    _normalize_format_fields(pack, notes)
    problem = _repair_overlays(data, notes)
    if problem is not None:
        return None, [f"{problem}，無法安全修復"]
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
