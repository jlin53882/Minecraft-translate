"""圖示預覽的 L2 磁碟快取（掃描結果 entries，每個模組一個分片檔）。"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.icon_runtime import get_runtime_asset_paths
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.log_unit import log_warning


def _get_cache_dir() -> Path:
    """取得 L2 快取目錄（專案根目錄）。"""
    return get_data_root() / ".icon_cache"


_SOURCE_ENTRY_FIELDS = ("modid", "key", "en", "source_jar", "icon_path")
# Version 6：快取改為 index.json + 每個模組一個欄位式分片；舊版單檔快取一律失效。
_CACHE_VERSION = 6
_INDEX_NAME = "index.json"


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
    source_root = Path(source_root).resolve()
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
    elif mode == "extracted_folder":
        language_files = []
        for language_path in sorted(
            source_root.rglob("en_us.json"), key=lambda path: path.as_posix()
        ):
            try:
                stat = language_path.stat()
                language_files.append(
                    {
                        "path": language_path.relative_to(source_root).as_posix(),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
            except OSError:
                # A disappearing/unreadable language file must invalidate L1.
                language_files.append(
                    {
                        "path": language_path.relative_to(source_root).as_posix(),
                        "unavailable": True,
                    }
                )
        identity["language_files"] = language_files
    return identity


def _identity_digest(identity: dict) -> str:
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_cache_key(source_root: Path) -> str:
    """計算 JAR 來源快取 key（路徑、檔名、大小與 mtime_ns）。"""
    return _cache_key(_compute_source_identity(source_root, "jar_directory"))


def _cache_key(identity: dict) -> str:
    return _identity_digest(identity)[:16]


class ShardSource:
    """L2 快取的某個來源：``index.json`` 列出模組，每個模組的項目在自己的分片檔。"""

    def __init__(self, cache_dir: Path, identity: dict, mods: list):
        self.identity = identity
        self._dir = cache_dir
        self._files = {modid: name for modid, _count, name in mods}
        self.modids = [modid for modid, _count, _name in mods]
        self.entry_count = sum(count for _modid, count, _name in mods)

    def load(self, modid: str) -> list[dict]:
        """讀取單一模組的分片並還原成來源項目 dict；分片遺失或損壞時回傳空清單。"""
        name = self._files.get(modid)
        if name is None:
            return []
        try:
            with open(self._dir / name, encoding="utf-8") as f:
                shard = json.load(f)
            return _decode_shard(modid, shard)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log_warning(f"[IconPreview] 快取分片讀取失敗 {modid}: {exc!r}")
            return []


def _encode_shard(rows: list[dict]) -> dict:
    """把同一模組的項目轉成欄位式（key／en／jar 索引／圖示），避免每筆重複欄位名稱。"""
    jars: list[str] = []
    jar_ids: dict[str, int] = {}
    jar_col = []
    for row in rows:
        jar = row.get("source_jar")
        if jar is None:
            jar_col.append(-1)
            continue
        if jar not in jar_ids:
            jar_ids[jar] = len(jars)
            jars.append(jar)
        jar_col.append(jar_ids[jar])
    return {
        "key": [row.get("key") for row in rows],
        "en": [row.get("en") for row in rows],
        "jars": jars,
        "jar": jar_col,
        "icon": [row.get("icon_path") for row in rows],
    }


def _decode_shard(modid: str, shard: dict) -> list[dict]:
    jars = shard["jars"]
    rows = []
    for key, en, jar_id, icon in zip(
        shard["key"], shard["en"], shard["jar"], shard["icon"], strict=True
    ):
        row = {"modid": modid, "key": key, "en": en}
        if jar_id >= 0:
            row["source_jar"] = jars[jar_id]
        if icon is not None:
            row["icon_path"] = icon
        rows.append(row)
    return rows


def open_entries_cache_l2(
    source_root: Path, identity: dict | None = None
) -> ShardSource | None:
    """開啟 L2 磁碟快取（只讀 index.json）。回傳 None 表示快取失效。

    失效條件：快取不存在、index 無法解析、version 不符、source identity 不符。
    各模組的項目在第一次被使用時才由 ``ShardSource.load`` 讀取。
    """
    if identity is None:
        identity = _compute_source_identity(source_root, "jar_directory")
    cache_dir = _get_cache_dir() / _cache_key(identity)
    try:
        with open(cache_dir / _INDEX_NAME, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None  # 不存在或損壞的快取視為失效
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return None
    if data.get("source_identity") != identity:
        return None
    mods = data.get("mods")
    if not isinstance(mods, list):
        return None
    return ShardSource(cache_dir, identity, mods)


def _save_entries_cache_l2(
    source_root: Path, entries: list, identity: dict | None = None
):
    """寫入 L2 磁碟快取（先寫暫存目錄再整個換上）。"""
    if identity is None:
        identity = _compute_source_identity(source_root, "jar_directory")
    cache_root = _get_cache_dir()
    cache_root.mkdir(parents=True, exist_ok=True)
    key = _cache_key(identity)
    target = cache_root / key
    tmp = cache_root / f"{key}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()

    # Translation data is intentionally omitted: it belongs to review_root.
    by_mod: dict[str, list[dict]] = {}
    for entry in entries:
        row = _source_entry_data(entry)
        by_mod.setdefault(str(row.get("modid", "")), []).append(row)

    mods = []
    for number, modid in enumerate(sorted(by_mod)):
        name = f"{number}.json"
        (tmp / name).write_text(
            json.dumps(
                _encode_shard(by_mod[modid]), ensure_ascii=False, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        mods.append([modid, len(by_mod[modid]), name])
    index = {
        "version": _CACHE_VERSION,
        "source_identity": identity,
        "mods": mods,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (tmp / _INDEX_NAME).write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    shutil.rmtree(target, ignore_errors=True)
    tmp.replace(target)
    # 舊版單檔快取不會再被讀取，順手移除。
    (cache_root / f"{key}.json").unlink(missing_ok=True)
