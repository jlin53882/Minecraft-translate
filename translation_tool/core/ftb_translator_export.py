"""translation_tool/core/ftb_translator_export.py 模組。

用途：解析 FTB Quests 設定檔根目錄（``config/ftbquests/quests``）。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path


def resolve_ftbquests_quests_root_impl(base_dir: str) -> str:
    """往下遞迴找 config/ftbquests/quests。"""
    base = Path(base_dir).expanduser().resolve()

    direct = base / "ftbquests" / "quests"
    if direct.is_dir():
        return str(direct)

    candidates = list(base.rglob("config/ftbquests/quests"))
    if not candidates:
        for p in base.rglob("*"):
            if p.is_dir():
                parts = [x.lower() for x in p.parts[-3:]]
                if (
                    parts == ["ftbquests", "quests"]
                    and len(p.parts) >= 3
                    and p.parts[-3].lower() == "config"
                ):
                    candidates.append(p)

    if not candidates:
        raise FileNotFoundError(f"找不到 config\\ftbquests\\quests (base_dir={base})")

    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    return str(candidates[0])
