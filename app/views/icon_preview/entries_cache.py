"""圖示預覽的 L2 磁碟快取（掃描結果 entries）。"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.icon_runtime import get_runtime_asset_paths
from translation_tool.utils.app_paths import get_data_root


def _get_cache_dir() -> Path:
    """取得 L2 快取目錄（專案根目錄）。"""
    return get_data_root() / ".icon_cache"


_SOURCE_ENTRY_FIELDS = ("modid", "key", "en", "source_jar", "icon_path")
# Version 4 refreshes cached icon paths after runtime-library model resolution.
_CACHE_VERSION = 4


def _source_entry_data(entry) -> dict:
    """Return only data derived from the selected source files.

    ``zh_tw`` belongs to the currently selected review root, so it must never
    become part of either the in-memory or on-disk source cache.
    """
    if isinstance(entry, dict):
        data = entry
    elif hasattr(entry, "__dict__"):
        data = vars(entry)
    else:
        data = {
            field: getattr(entry, field)
            for field in _SOURCE_ENTRY_FIELDS
            if hasattr(entry, field)
        }
    return {field: data[field] for field in _SOURCE_ENTRY_FIELDS if field in data}


def _compute_source_identity(source_root: Path, mode: str) -> dict:
    """Describe source inputs whose changes require rescanning entries."""
    source_root = Path(source_root)
    identity = {
        "source_root": str(source_root.resolve()),
        "mode": mode,
    }
    if mode == "jar_directory":
        jars = []
        for jar_path in sorted(source_root.glob("*.jar"), key=lambda path: path.name):
            try:
                stat = jar_path.stat()
                jars.append(
                    {
                        "name": jar_path.name,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
            except OSError:
                # A disappearing/unreadable JAR must not accidentally validate
                # a previous cache entry for the same directory.
                jars.append({"name": jar_path.name, "unavailable": True})
        identity["jars"] = jars
        runtime_assets = []
        for asset_path in get_runtime_asset_paths(source_root):
            try:
                stat = asset_path.stat()
                runtime_assets.append(
                    {
                        "path": str(asset_path.resolve()),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
            except OSError:
                runtime_assets.append(
                    {"path": str(asset_path.resolve()), "unavailable": True}
                )
        identity["runtime_assets"] = runtime_assets
    return identity


def _identity_digest(identity: dict) -> str:
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_cache_key(source_root: Path) -> str:
    """計算 JAR 來源快取 key（路徑、檔名、大小與 mtime_ns）。"""
    return _identity_digest(_compute_source_identity(source_root, "jar_directory"))[:16]


def _load_entries_cache_l2(source_root: Path) -> list | None:
    """讀取 L2 磁碟快取。回傳 None 表示快取失效。

    失效條件：
    - 快取檔案不存在
    - JSON 解析失敗
    - version 不為目前版本（舊版快取直接失效）
    - source identity 不符
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

    # Version 1 persisted review-root translations and cannot be trusted.
    if data.get("version") != _CACHE_VERSION:
        return None

    identity = _compute_source_identity(source_root, "jar_directory")
    if data.get("source_identity") != identity:
        return None

    entries = data.get("entries")
    if not isinstance(entries, list):
        return None
    return [_source_entry_data(entry) for entry in entries if isinstance(entry, dict)]


def _save_entries_cache_l2(source_root: Path, entries: list):
    """寫入 L2 磁碟快取（atomic write）。"""

    cache_dir = _get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_compute_cache_key(source_root)}.json"

    # Atomic write：用 tmp 檔再 rename
    tmp = cache_dir / f"{cache_file.stem}.tmp"
    # Translation data is intentionally omitted: it belongs to review_root.
    identity = _compute_source_identity(source_root, "jar_directory")
    serializable_entries = [_source_entry_data(entry) for entry in entries]

    data = {
        "version": _CACHE_VERSION,
        "source_identity": identity,
        "entries": serializable_entries,
        "created_at": datetime.now(UTC).isoformat(),
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_file)  # 跨平台 atomic replace（Python 3.3+，自動覆蓋目標檔案）
