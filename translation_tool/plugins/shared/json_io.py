from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def read_json_dict(path: Path) -> Dict[str, Any]:
    """Read JSON file and require top-level object(dict)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON must be an object/dict: {path}")
    return data


def write_json_dict(path: Path, data: Dict[str, str]) -> None:
    """Write dict to JSON with UTF-8 and stable indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as wf:
        json.dump(data, wf, ensure_ascii=False, indent=2)


def collect_json_files(input_dir: Path) -> List[Path]:
    """Collect all JSON files recursively under input_dir (sorted)."""
    return sorted(input_dir.rglob("*.json"))
