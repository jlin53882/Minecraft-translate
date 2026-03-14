"""translation_tool/plugins/shared/json_io.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

def read_json_dict(path: Path) -> Dict[str, Any]:
    """讀取 JSON 檔案並回傳頂層物件（字典）。"""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON must be an object/dict: {path}")
    return data

def write_json_dict(path: Path, data: Dict[str, str]) -> None:
    """將字典寫入 JSON 檔案（UTF-8 編碼）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as wf:
        json.dump(data, wf, ensure_ascii=False, indent=2)

def collect_json_files(input_dir: Path) -> List[Path]:
    """收集輸入目錄下所有 JSON 檔案（遞迴）。"""
    return sorted(input_dir.rglob("*.json"))
