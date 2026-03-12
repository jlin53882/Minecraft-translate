from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Callable

from translation_tool.core.lm_translator_shared import _get_default_batch_size

_LANG_MODE_LABELS = {
    "non_cjk_only": "僅抽取非中文（non_cjk_only）",
    "cjk_only": "僅抽取中文（cjk_only）",
    "all": "抽取全部（all）",
}


def normalize_lang_mode(lang_mode: str) -> str:
    mode = (lang_mode or "").strip().lower()
    if mode in _LANG_MODE_LABELS:
        return mode
    return "non_cjk_only"


def count_json_files(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*.json") if p.is_file())


def count_md_pending_docs(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    count = 0
    for p in root.rglob("*.json"):
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("schema") == "md_pending_blocks_v1":
            count += 1
    return count


def log_md_step2_stats(step2_res: Dict[str, Any], *, log_info_fn: Callable[..., None]) -> None:
    if not isinstance(step2_res, dict):
        return

    if step2_res.get("skipped"):
        log_info_fn("[MD] [2/3] 已略過翻譯：%s", step2_res.get("reason"))
        return

    is_dry = bool(step2_res.get("dry_run"))
    files = step2_res.get("files", step2_res.get("written_files"))
    total_blocks = step2_res.get("total_blocks")
    unique_blocks = step2_res.get("unique_blocks")
    duplicate_blocks = step2_res.get("duplicate_blocks")
    cache_hit = step2_res.get("cache_hit")
    cache_miss = step2_res.get("cache_miss")
    already_zh_skipped = step2_res.get("already_zh_skipped")
    missing_hash = step2_res.get("missing_hash")
    out_dir = step2_res.get("out_dir")
    batch_size = _get_default_batch_size("md", None)
    avg_batch_sec = step2_res.get("avg_batch_sec")
    est_sec_per_batch = avg_batch_sec
    est_batches = math.ceil(cache_miss / batch_size) if isinstance(cache_miss, int) and batch_size > 0 else None

    log_info_fn(
        "[MD] [2/3] 統計 %s | files=%s | total=%s | unique=%s | dup=%s",
        "DRY-RUN" if is_dry else "翻譯",
        files,
        total_blocks,
        unique_blocks,
        duplicate_blocks,
    )
    log_info_fn(
        "[MD] [2/3] cache_hit=%s | cache_miss=%s | already_zh_skipped=%s",
        cache_hit,
        cache_miss,
        already_zh_skipped,
    )
    if missing_hash is not None:
        log_info_fn("[MD] [2/3] missing_hash=%s", missing_hash)
    if out_dir:
        log_info_fn("[MD] [2/3] out_dir=%s", out_dir)
    if est_batches is not None:
        log_info_fn("[MD] [2/3] 預估批次：%s (batch_size=%s)", est_batches, batch_size)
    if avg_batch_sec:
        log_info_fn("[MD] [2/3] 平均每批耗時(本次)：%.2fs", avg_batch_sec)
    if est_batches is not None and est_sec_per_batch:
        total_sec = int(est_batches * est_sec_per_batch)
        m, s = divmod(total_sec, 60)
        h, m = divmod(m, 60)
        eta_txt = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
        log_info_fn("[MD] [2/3] 預估總耗時：%s", eta_txt)
