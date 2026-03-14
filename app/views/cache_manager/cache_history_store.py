from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

def history_now_ts() -> str:
    """取得目前的 ISO 時間戳字串（時區Aware）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")

def history_dirs(cache_root: str, cache_type: str):
    """根據 cache_root 與 cache_type 取得並建立 cache_history 目錄結構。

    回傳：(base_path, jsonl_dir, json_dir)
    """
    root = str(cache_root or "").strip()
    if not root:
        return None, None, None
    base = Path(root) / "cache_history" / cache_type
    jsonl_dir = base / "jsonl"
    json_dir = base / "json"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    return base, jsonl_dir, json_dir

def history_active_default(cache_type: str) -> dict:
    """取得指定 cache_type 的預設活動狀態（用於新檔案）。"""
    return {
        "current_file": f"{cache_type}_h000001.jsonl",
        "current_count": 0,
        "next_seq": 2,
        "max_per_file": 10000,
    }

def history_load_active(cache_root: str, cache_type: str):
    """載入或初始化 cache_type 的活動狀態（.history.active 檔案）。

    回傳：(active_dict, active_path, jsonl_dir, json_dir)
    """
    _base, jsonl_dir, json_dir = history_dirs(cache_root, cache_type)
    if jsonl_dir is None:
        return None, None, None, None

    active_path = jsonl_dir / ".history.active"
    if not active_path.exists():
        active = history_active_default(cache_type)
        active_path.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")
        return active, active_path, jsonl_dir, json_dir

    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if not isinstance(active, dict):
            raise ValueError("active format error")
    except Exception:
        active = history_active_default(cache_type)
        active_path.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")

    active.setdefault("current_file", f"{cache_type}_h000001.jsonl")
    active.setdefault("current_count", 0)
    active.setdefault("next_seq", 2)
    active.setdefault("max_per_file", 10000)
    return active, active_path, jsonl_dir, json_dir

def history_save_active(active_path: Path, active: dict):
    """將活動狀態寫入 .history.active 檔案（JSON 格式）。"""
    active_path.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")

def history_append_event(cache_root: str, cache_type: str, event: dict):
    """將事件寫入 cache_type 的歷史記錄（自動分檔、寫入 jsonl 與 json）。"""
    active, active_path, jsonl_dir, json_dir = history_load_active(cache_root, cache_type)
    if not active:
        return

    max_per_file = int(active.get("max_per_file", 10000) or 10000)
    current_count = int(active.get("current_count", 0) or 0)

    if current_count >= max_per_file:
        seq = int(active.get("next_seq", 2) or 2)
        active["current_file"] = f"{cache_type}_h{seq:06d}.jsonl"
        active["current_count"] = 0
        active["next_seq"] = seq + 1

    current_file = str(active.get("current_file"))
    jsonl_path = jsonl_dir / current_file
    line = json.dumps(event, ensure_ascii=False)
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    active["current_count"] = int(active.get("current_count", 0) or 0) + 1
    history_save_active(active_path, active)

    json_path = json_dir / current_file.replace(".jsonl", ".json")
    arr = []
    if json_path.exists():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                arr = raw
        except Exception:
            arr = []

    arr.append(event)
    if len(arr) > max_per_file:
        arr = arr[-max_per_file:]
    json_path.write_text(json.dumps(arr, ensure_ascii=False, indent=2), encoding="utf-8")

def history_load_recent(cache_root: str, cache_type: str, key: str, limit: int = 20) -> list[dict]:
    """根據 key 取得最近 limit 筆歷史事件（從最新的檔案往前掃）。"""
    _base, jsonl_dir, _json_dir = history_dirs(cache_root, cache_type)
    if jsonl_dir is None:
        return []

    files = sorted(jsonl_dir.glob(f"{cache_type}_h*.jsonl"), reverse=True)
    out: list[dict] = []
    for fp in files:
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            if str(ev.get("key", "")) != key:
                continue
            out.append(ev)
            if len(out) >= limit:
                return out
    return out
