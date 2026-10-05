"""分片檔案（shard JSON）的讀取與記憶（#114：不得在 event loop 上反覆解析大型 JSON）。

分片頁原本每次渲染都把「所有」分片檔完整 ``json.loads`` 一遍只為了數 key 數量。
這裡以「檔案簽名（路徑、mtime_ns、大小）」為鍵記住解析後的摘要（key 數量與 key 清單）：
檔案沒變就不會再讀；檔案被改寫（簽名變了）才重新讀。

``warm_shard_summaries`` 供背景執行緒預熱（載入總覽、執行快取動作之後），
所以 event loop 上的渲染只剩記憶命中（只做 ``stat``）。
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_SUMMARY_MEMO: dict[tuple[str, int, int], tuple[int, list[str]]] = {}
_RAW_LRU: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
_RAW_LRU_MAX = 2  # 單筆內容查詢用；只留最近用過的少數幾個分片，避免常駐大量資料


def _signature(fp: Path) -> tuple[str, int, int] | None:
    try:
        st = fp.stat()
    except OSError:
        return None
    return (str(fp), st.st_mtime_ns, st.st_size)


def _summarize(raw: Any) -> tuple[int, list[str]]:
    if isinstance(raw, dict):
        return len(raw), sorted(str(k) for k in raw)
    if isinstance(raw, list):
        keys = []
        for idx, item in enumerate(raw):
            if isinstance(item, dict) and item.get("key"):
                keys.append(str(item.get("key")))
            else:
                keys.append(f"[{idx}]")
        return len(raw), keys
    return 0, []


def shard_summary(fp: Path) -> tuple[int, list[str]]:
    """回傳 ``(key 數量, key 清單)``；讀不到或不是 dict/list 時為 ``(0, [])``。"""
    sig = _signature(fp)
    if sig is None:
        return 0, []
    with _LOCK:
        hit = _SUMMARY_MEMO.get(sig)
    if hit is not None:
        return hit
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    summary = _summarize(raw)
    with _LOCK:
        # 同一路徑的舊簽名不再有用：清掉，避免記憶無限成長
        for old in [k for k in _SUMMARY_MEMO if k[0] == sig[0] and k != sig]:
            del _SUMMARY_MEMO[old]
        _SUMMARY_MEMO[sig] = summary
    return summary


def shard_raw(fp: Path) -> Any:
    """讀取整個分片（單筆內容查詢的 fallback）；最近用過的少數個會留在 LRU。"""
    sig = _signature(fp)
    if sig is None:
        return None
    with _LOCK:
        if sig in _RAW_LRU:
            _RAW_LRU.move_to_end(sig)
            return _RAW_LRU[sig]
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _LOCK:
        _RAW_LRU[sig] = raw
        while len(_RAW_LRU) > _RAW_LRU_MAX:
            _RAW_LRU.popitem(last=False)
    return raw


def clear_memo() -> None:
    """清空所有記憶（測試用）。"""
    with _LOCK:
        _SUMMARY_MEMO.clear()
        _RAW_LRU.clear()


def _sort_key(path: Path):
    stem = path.stem
    m = re.search(r"(\d+)$", stem)
    return (int(m.group(1)) if m else -1, stem.lower())


def list_shard_files(root: str, cache_type: str) -> list[Path]:
    """列出某類型資料夾內的分片檔（不含 ``<type>_cache_main.json``），依序號由新到舊。"""
    type_dir = Path(root) / cache_type
    if not type_dir.exists():
        return []
    skip = f"{cache_type.lower()}_cache_main.json"
    files = [fp for fp in type_dir.glob("*.json") if fp.name.lower() != skip]
    return sorted(files, key=_sort_key, reverse=True)


def shard_rows(
    root: str, cache_type: str, active_shard_id: str, shard_capacity: int
) -> list[dict]:
    """分片列表（檔名、key 數量、是否為使用中分片、容量）。"""
    active_filename = (
        f"{cache_type}_{active_shard_id!s}.json"
        if str(active_shard_id or "").strip()
        else ""
    )
    return [
        {
            "filename": fp.name,
            "key_count": shard_summary(fp)[0],
            "is_active": fp.name == active_filename,
            "capacity": shard_capacity,
        }
        for fp in list_shard_files(root, cache_type)
    ]


def warm_shard_summaries(overview: dict | None) -> int:
    """（背景執行緒）預熱所有類型的分片摘要；回傳處理的分片檔數。"""
    data = overview or {}
    root = str(data.get("cache_root", "") or "").strip()
    if not root:
        return 0
    count = 0
    for cache_type in data.get("types") or {}:
        for fp in list_shard_files(root, str(cache_type)):
            shard_summary(fp)
            count += 1
    return count
