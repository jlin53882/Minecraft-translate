from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from translation_tool.utils.log_unit import log_warning

# --- 效能／執行緒契約（#114）-------------------------------------------------
# 歷史紀錄從 UI handler 呼叫，不能在 event loop 上反覆掃描／改寫大檔：
# - 讀取：每個 jsonl 檔解析成 {key: [events]} 並以檔案簽名（mtime_ns、大小）記住，
#   ``history_load_recent`` 只做目錄列表與 stat；``history_append_event`` 會就地更新記憶，
#   所以「套用 → 讀歷史」不會重新解析整個檔案。背景預熱用 ``warm_history_index``。
# - 寫入：jsonl 只做 O(1) 的附加；整份 json 鏡像（最多 10000 筆、每次整個重寫）交給
#   單一背景執行緒依序處理（``history_flush`` 可等待全部完成）。
_INDEX_LOCK = threading.Lock()
_INDEX_MEMO: dict[str, tuple[tuple[int, int], dict[str, list[dict]]]] = {}
_MIRROR_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="history-mirror"
)


def _stat_sig(fp: Path) -> tuple[int, int] | None:
    try:
        st = fp.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _parse_index(fp: Path) -> dict[str, list[dict]]:
    by_key: dict[str, list[dict]] = {}
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # 讀不到的檔案視為沒有紀錄（與原行為一致）
        return by_key
    for lineno, ln in enumerate(lines, start=1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except ValueError as exc:  # 損毀的歷史行略過，其餘行照常讀取（與原行為一致）
            log_warning(f"略過損毀的快取歷史記錄（第 {lineno} 行）：{exc}")
            continue
        by_key.setdefault(str(ev.get("key", "")), []).append(ev)
    return by_key


def _file_index(fp: Path) -> dict[str, list[dict]]:
    """回傳 jsonl 檔的 {key: [events（檔案順序）]}；簽名沒變就直接用記憶。"""
    sig = _stat_sig(fp)
    if sig is None:
        return {}
    name = str(fp)
    with _INDEX_LOCK:
        hit = _INDEX_MEMO.get(name)
        if hit is not None and hit[0] == sig:
            return hit[1]
    by_key = _parse_index(fp)
    with _INDEX_LOCK:
        _INDEX_MEMO[name] = (sig, by_key)
    return by_key


def warm_history_index(cache_root: str, cache_types) -> int:
    """（背景執行緒）預熱各類型歷史索引；回傳解析的檔案數。"""
    count = 0
    root = str(cache_root or "").strip()
    if not root:
        return 0
    for cache_type in cache_types:
        jsonl_dir = Path(root) / "cache_history" / str(cache_type) / "jsonl"
        if not jsonl_dir.exists():
            continue
        for fp in jsonl_dir.glob(f"{cache_type}_h*.jsonl"):
            _file_index(fp)
            count += 1
    return count


def history_flush(timeout: float | None = 10.0) -> None:
    """等待背景鏡像寫入全部完成（測試／結束前使用）。"""
    _MIRROR_EXECUTOR.submit(lambda: None).result(timeout=timeout)


def clear_history_memo() -> None:
    """清空讀取記憶（測試用）。"""
    with _INDEX_LOCK:
        _INDEX_MEMO.clear()


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
        active_path.write_text(
            json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return active, active_path, jsonl_dir, json_dir

    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if not isinstance(active, dict):
            raise ValueError("active format error")  # noqa: TRY004
    except Exception as e:  # noqa: BLE001
        log_warning(f"載入 active 失敗，使用預設值: {e}")
        active = history_active_default(cache_type)
        active_path.write_text(
            json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    active.setdefault("current_file", f"{cache_type}_h000001.jsonl")
    active.setdefault("current_count", 0)
    active.setdefault("next_seq", 2)
    active.setdefault("max_per_file", 10000)
    return active, active_path, jsonl_dir, json_dir


def history_save_active(active_path: Path, active: dict):
    """將活動狀態寫入 .history.active 檔案（JSON 格式）。"""
    active_path.write_text(
        json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def history_append_event(cache_root: str, cache_type: str, event: dict):
    """將事件寫入 cache_type 的歷史記錄（自動分檔、寫入 jsonl 與 json）。"""
    active, active_path, jsonl_dir, json_dir = history_load_active(
        cache_root, cache_type
    )
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
    sig_before = _stat_sig(jsonl_path)
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    # 就地更新讀取記憶（簽名與寫入前一致才更新；否則留給下次讀取重新解析）
    sig_after = _stat_sig(jsonl_path)
    with _INDEX_LOCK:
        memo = _INDEX_MEMO.get(str(jsonl_path))
        if memo is not None and sig_after is not None and memo[0] == sig_before:
            memo[1].setdefault(str(event.get("key", "")), []).append(event)
            _INDEX_MEMO[str(jsonl_path)] = (sig_after, memo[1])

    active["current_count"] = int(active.get("current_count", 0) or 0) + 1
    history_save_active(active_path, active)

    # 整份 json 鏡像（讀整個陣列、附加、整個重寫）交給背景執行緒，依序執行
    json_path = json_dir / current_file.replace(".jsonl", ".json")
    _MIRROR_EXECUTOR.submit(_append_mirror, json_path, event, max_per_file)


def _append_mirror(json_path: Path, event: dict, max_per_file: int) -> None:
    """（背景執行緒）把事件附加到 json 鏡像並截斷到 max_per_file 筆。"""
    try:
        arr: list = []
        if json_path.exists():
            try:
                raw = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    arr = raw
            except Exception as e:  # noqa: BLE001
                log_warning(f"載入歷史記錄失敗，使用空陣列: {e}")
                arr = []
        arr.append(event)
        if len(arr) > max_per_file:
            arr = arr[-max_per_file:]
        json_path.write_text(
            json.dumps(arr, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001 - 鏡像是衍生資料，失敗只記錄
        log_warning(f"寫入歷史 json 鏡像失敗: {e}")


def history_load_recent(
    cache_root: str, cache_type: str, key: str, limit: int = 20
) -> list[dict]:
    """根據 key 取得最近 limit 筆歷史事件（從最新的檔案往前掃）。"""
    _base, jsonl_dir, _json_dir = history_dirs(cache_root, cache_type)
    if jsonl_dir is None:
        return []

    files = sorted(jsonl_dir.glob(f"{cache_type}_h*.jsonl"), reverse=True)
    out: list[dict] = []
    for fp in files:
        events = _file_index(fp).get(key, [])
        for ev in reversed(events):  # 檔案內由新到舊
            out.append(ev)
            if len(out) >= limit:
                return out
    return out
