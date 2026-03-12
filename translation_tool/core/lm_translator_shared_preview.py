from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import json


@dataclass
class TouchSet:
    """收集本輪被寫入的 file id。"""

    touched: Set[str] = field(default_factory=set)

    def touch(self, file_id: str) -> None:
        if file_id:
            self.touched.add(str(file_id))

    def flush(self, writer_fn: Callable[[str], Any]) -> None:
        for fid in list(self.touched):
            writer_fn(fid)
        self.touched.clear()


def write_dry_run_preview(
    out_dir: str | Path,
    items: List[Dict[str, Any]],
    *,
    filename: str = "_dry_run_preview.json",
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """寫出 dry-run preview 檔。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / filename

    payload = {
        "meta": meta or {},
        "count": len(items),
        "items": items,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_cache_hit_preview(
    out_dir: str | Path,
    cached_items: List[Dict[str, Any]],
    *,
    filename: str = "_dry_run_cache_hit_preview.json",
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """寫出 cache-hit preview 檔。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / filename

    rows = []
    for it in cached_items:
        rows.append(
            {
                "file": it.get("file"),
                "path": it.get("path"),
                "source_text": it.get("source_text"),
                "text": it.get("text"),
                "cache_type": it.get("cache_type"),
            }
        )

    payload = {
        "meta": meta or {},
        "count": len(rows),
        "items": rows,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
