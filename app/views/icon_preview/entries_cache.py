"""圖示預覽的 L2 磁碟快取（掃描結果 entries）。"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from translation_tool.utils.app_paths import get_data_root


def _get_cache_dir() -> Path:
    """取得 L2 快取目錄（專案根目錄）。"""
    return get_data_root() / ".icon_cache"


def _compute_cache_key(source_root: Path) -> str:
    """計算快取 key：只看 JAR 檔案名稱，不算內容。

    注意：key 只包含 JAR 的檔名。這樣：
    - 新增/移除 JAR → key 改變 → 快取失效
    - JAR 內容變了但檔名不變 → 不會自動失效（已知限制）
    """
    jar_files = sorted([j.name for j in source_root.glob("*.jar")])
    key_str = str(source_root.resolve()) + ":" + ",".join(jar_files)
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


def _load_entries_cache_l2(source_root: Path) -> list | None:
    """讀取 L2 磁碟快取。回傳 None 表示快取失效。

    失效條件：
    - 快取檔案不存在
    - JSON 解析失敗
    - version 不為 1
    - source_root 不符
    """
    cache_dir = _get_cache_dir()
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None  # 損壞的快取視為失效

    # 版本檢查
    if data.get("version") != 1:
        return None

    # 路徑檢查
    if data.get("source_root") != str(source_root):
        return None

    return data.get("entries", [])


def _save_entries_cache_l2(source_root: Path, entries: list):
    """寫入 L2 磁碟快取（atomic write）。"""

    cache_dir = _get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    # Atomic write：用 tmp 檔再 rename
    tmp = cache_dir / f"{cache_file.stem}.tmp"
    # 將 entries 轉為可序列化格式
    serializable_entries = []
    for e in entries:
        if hasattr(e, "__dict__"):
            serializable_entries.append(e.__dict__)
        elif isinstance(e, dict):
            serializable_entries.append(e)
        else:
            serializable_entries.append(
                {
                    "modid": str(e.modid),
                    "key": str(e.key),
                    "en": str(e.en),
                    "zh_tw": str(e.zh_tw),
                    "source_jar": getattr(e, "source_jar", ""),
                    "icon_path": getattr(e, "icon_path", None),  # [FIX] 加入 icon_path
                }
            )

    data = {
        "version": 1,
        "source_root": str(source_root),
        "entries": serializable_entries,
        "created_at": datetime.now(UTC).isoformat(),
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_file)  # 跨平台 atomic replace（Python 3.3+，自動覆蓋目標檔案）
