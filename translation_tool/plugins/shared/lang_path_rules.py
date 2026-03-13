"""translation_tool/plugins/shared/lang_path_rules.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from pathlib import Path

def should_rename_to_zh_tw(src_path: Path, rename_langs: set[str]) -> bool:
    """Return True if filename itself is a language code that should become zh_tw.json."""
    name = src_path.name.lower()
    if not name.endswith(".json"):
        return False
    stem = name[:-5]
    if len(stem) == 5 and stem[2] == "_":
        return stem in rename_langs
    return False

def is_lang_code_segment(seg: str) -> bool:
    """Check whether path segment matches xx_xx language code format."""
    seg = seg.lower()
    return (
        len(seg) == 5
        and seg[2] == "_"
        and seg[:2].isalpha()
        and seg[3:].isalpha()
    )

def replace_lang_folder_with_zh_tw(rel: Path) -> Path:
    """Replace any language-code folder segment in rel path with zh_tw."""
    parts = list(rel.parts)
    new_parts = []
    for p in parts:
        if is_lang_code_segment(p):
            new_parts.append("zh_tw")
        else:
            new_parts.append(p)
    return Path(*new_parts)

def compute_output_path(src_path: Path, in_dir: Path, out_dir: Path, rename_langs: set[str]) -> Path:
    """Compute final output path with folder/filename language normalization."""
    rel = src_path.relative_to(in_dir)
    rel = replace_lang_folder_with_zh_tw(rel)
    if should_rename_to_zh_tw(src_path, rename_langs):
        return out_dir / rel.parent / "zh_tw.json"
    return out_dir / rel
