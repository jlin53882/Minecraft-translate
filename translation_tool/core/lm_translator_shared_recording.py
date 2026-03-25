"""translation_tool/core/lm_translator_shared_recording.py 模組。

用途：翻譯紀錄的收集與匯出功能。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import csv
import json

@dataclass
class TranslationRecorder:
    """收集翻譯紀錄並輸出 JSON/CSV。"""

    rows: List[Dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        cache_type: str,
        file_id: Optional[str],
        path: str,
        src: str,
        dst: str,
        cache_hit: bool,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.rows.append(
            {
                "cache_type": cache_type,
                "file_id": file_id or "",
                "path": path,
                "src": src,
                "dst": dst,
                "cache_hit": bool(cache_hit),
                **(extra or {}),
            }
        )

    def export_json(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self.rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out_path

    def export_csv(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cols = ["cache_type", "file_id", "path", "src", "dst", "cache_hit"]
        extra_cols = sorted({k for r in self.rows for k in r.keys()} - set(cols))
        cols = cols + extra_cols

        with out_path.open("w", encoding="utf-8", newline="") as f:
            # ⭐ Use QUOTE_ALL to prevent CSV injection attacks
            # All fields are quoted, preventing malicious values like
            # "=cmd|'/C calc'!A0" or values with embedded newlines
            w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL)
            w.writeheader()
            for r in self.rows:
                w.writerow({k: r.get(k, "") for k in cols})
        return out_path
