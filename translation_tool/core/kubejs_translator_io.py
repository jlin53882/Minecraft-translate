"""translation_tool/core/kubejs_translator_io.py 模組。

用途：KubeJS 翻譯的檔案讀寫功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson


def read_json_dict_orjson_impl(path: Path) -> dict:
    """使用 orjson 讀取 JSON 檔並容忍 BOM / trailing comma。"""
    if not path or not path.is_file():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
        raw = raw.lstrip("\ufeff")
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        data = orjson.loads(raw.encode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def write_json_orjson_impl(path: Path, data: dict) -> None:
    """使用 orjson pretty-print 寫回 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    def _normalize_json_keys(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {str(k): _normalize_json_keys(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_normalize_json_keys(v) for v in obj]
        return obj

    normalized = _normalize_json_keys(data)
    b = orjson.dumps(normalized, option=orjson.OPT_INDENT_2)
    path.write_bytes(b)
