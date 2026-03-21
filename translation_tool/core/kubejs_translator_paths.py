"""translation_tool/core/kubejs_translator_paths.py 模組。

用途：KubeJS 路徑解析功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path

def resolve_kubejs_root_impl(input_dir: str, *, max_depth: int = 4) -> Path:
    """實作：自動搜尋並解析 KubeJS 根目錄。
    
    優先傳回直接命名為 kubejs 的目錄；若有多個候選，則優先選擇包含 client_scripts 的目錄。
    
    Args:
        input_dir: 起始搜尋目錄（可為 modpack 根目錄）。
        max_depth: 最大搜尋深度（預設 4）。
    Returns:
        Path: 偵測到的 KubeJS 目錄路徑；若找不到則回傳 input_dir 本身。
    """
    base = Path(input_dir).resolve()

    if base.is_dir() and base.name.lower() == "kubejs":
        return base

    direct = base / "kubejs"
    candidates: list[Path] = []
    if direct.is_dir():
        candidates.append(direct)

    base_parts = len(base.parts)
    for p in base.rglob("*"):
        if not p.is_dir():
            continue
        depth = len(p.parts) - base_parts
        if depth > max_depth:
            continue
        if p.name.lower() == "kubejs":
            candidates.append(p)

    if not candidates:
        return base

    def score(p: Path) -> tuple[int, int]:
        has_client = (p / "client_scripts").is_dir()
        depth = len(p.parts) - base_parts
        return (0 if has_client else 1, depth)

    candidates.sort(key=score)
    return candidates[0]
