from __future__ import annotations

from pathlib import Path


def resolve_kubejs_root_impl(input_dir: str, *, max_depth: int = 4) -> Path:
    """自動解析 KubeJS 根目錄。"""
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
