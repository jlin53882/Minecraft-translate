"""translation_tool/utils/safe_json_loader.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any


_CANDIDATE_ENCODINGS = [
    "utf-8",
    "utf-8-sig",  # 處理 BOM
    "cp1252",     # 常見 Windows 編碼
    "latin-1",    # 最後保底
]


def load_json_auto_encoding(path: Path) -> Optional[Dict[str, Any]]:
    """
    嘗試用多種編碼讀取 JSON。
    成功 → dict
    失敗 → None（不中斷流程）
    """
    for enc in _CANDIDATE_ENCODINGS:
        try:
            text = path.read_text(encoding=enc).strip()
            if not text:
                return None
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None
