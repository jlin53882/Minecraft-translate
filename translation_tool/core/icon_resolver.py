"""translation_tool/core/icon_resolver.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from pathlib import Path
from functools import lru_cache
from .icon_classifier import classify_no_icon_reason
from .icon_reason import IconResult

@lru_cache(maxsize=128)
def _build_icon_index(mod_textures_root: Path) -> dict[str, Path]:
    """
    建立 icon 索引：
    - key: png 檔名（不含 .png）
    - value: 實際完整路徑
    """
    index: dict[str, Path] = {}

    if not mod_textures_root.exists():
        return index

    for png in mod_textures_root.rglob("*.png"):
        name = png.stem  # atomic_reconstructor_top
        # 若重名，保留第一個（通常已足夠）
        index.setdefault(name, png)

    return index


def resolve_icon_for_lang_key(lang_key: str, assets_root: Path) -> Path | None:
    """
    深層 icon resolver（不假設目錄結構）

    支援：
    - textures/item/**/*
    - textures/block/**/*
    - 任意子資料夾（unused, machines, etc.）
    """

    # 嘗試從 lang key 取出最後一段
    # e.g. item.actuallyadditions.atomic_reconstructor
    key_tail = lang_key.split(".")[-1]

    # assets/<modid>/textures
    try:
        modid = lang_key.split(".")[1]
    except IndexError:
        return None

    textures_root = assets_root / modid / "textures"
    if not textures_root.exists():
        return None

    index = _build_icon_index(textures_root)

    # 直接以檔名比對
    return index.get(key_tail)


def resolve_icon_with_reason(lang_key: str, assets_root):
    """resolve_icon_with_reason 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    icon = resolve_icon_for_lang_key(lang_key, assets_root)

    if icon:
        return IconResult(
            icon_path=icon,
            reason="",
            risk=None,
        )

    reason, risk = classify_no_icon_reason(lang_key)
    return IconResult(
        icon_path=None,
        reason=reason,
        risk=risk,
    )
