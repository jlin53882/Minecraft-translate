"""從模組包來源（資料夾或 zip）讀出 Minecraft 遊戲版本，並提供版本選項清單。

支援的模組包描述檔（只讀取、不執行任何內容）：
- ``manifest.json``（CurseForge：``minecraft.version``）
- ``modrinth.index.json``（Modrinth：``dependencies.minecraft``）
- ``mmc-pack.json``（MultiMC／Prism：``net.minecraft`` 元件的 ``version``）
- ``minecraftinstance.json``（CurseForge App：``baseModLoader.minecraftVersion``）
- ``instance.cfg``（Prism／MultiMC：``IntendedVersion``）

偵測只看來源本身與前兩層子資料夾／zip 內的同層檔案，不掃描整個模組包，確保成本有上限。
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from translation_tool.core.pack_format_lookup import load_table

_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
_DESCRIPTOR_NAMES = frozenset(
    {
        "manifest.json",
        "modrinth.index.json",
        "mmc-pack.json",
        "minecraftinstance.json",
        "instance.cfg",
    }
)
_MAX_DEPTH = 2
_MAX_ENTRIES = 5000
_MAX_DESCRIPTOR_BYTES = 5 * 1024 * 1024


def _valid(version: object) -> str | None:
    if isinstance(version, str) and _VERSION_RE.match(version.strip()):
        return version.strip()
    return None


def _parse_descriptor(name: str, text: str) -> str | None:
    """依描述檔名稱取出版本；格式不符或損毀時回傳 None（不拋出例外）。"""
    try:
        if name == "instance.cfg":
            for line in text.splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "IntendedVersion":
                    return _valid(value)
            return None
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if name == "manifest.json":
        return _valid((data.get("minecraft") or {}).get("version"))
    if name == "modrinth.index.json":
        return _valid((data.get("dependencies") or {}).get("minecraft"))
    if name == "mmc-pack.json":
        for component in data.get("components") or []:
            if isinstance(component, dict) and component.get("uid") == "net.minecraft":
                return _valid(component.get("version"))
        return None
    if name == "minecraftinstance.json":
        base = data.get("baseModLoader") or {}
        return _valid(base.get("minecraftVersion") or data.get("gameVersion"))
    return None


def _from_directory(root: Path) -> str | None:
    """依序檢查來源資料夾與前兩層子資料夾；先找到的描述檔優先。"""
    frontier = [root]
    scanned = 0
    for _depth in range(_MAX_DEPTH + 1):
        next_frontier: list[Path] = []
        for folder in frontier:
            try:
                children = list(folder.iterdir())
            except OSError:
                continue
            for child in children:
                scanned += 1
                if scanned > _MAX_ENTRIES:
                    return None
                if child.is_file() and child.name in _DESCRIPTOR_NAMES:
                    if child.stat().st_size > _MAX_DESCRIPTOR_BYTES:
                        continue
                    version = _parse_descriptor(
                        child.name, child.read_text(encoding="utf-8", errors="replace")
                    )
                    if version:
                        return version
                elif child.is_dir() and not child.is_symlink():
                    next_frontier.append(child)
        frontier = next_frontier
    return None


def _from_zip(path: Path) -> str | None:
    with zipfile.ZipFile(path) as archive:
        for index, info in enumerate(archive.infolist()):
            if index >= _MAX_ENTRIES:
                break
            parts = info.filename.strip("/").split("/")
            if len(parts) > _MAX_DEPTH + 1 or parts[-1] not in _DESCRIPTOR_NAMES:
                continue
            if info.file_size > _MAX_DESCRIPTOR_BYTES:
                continue
            text = archive.read(info).decode("utf-8", errors="replace")
            version = _parse_descriptor(parts[-1], text)
            if version:
                return version
    return None


def detect_minecraft_version(source: str | None) -> str | None:
    """回傳來源模組包的遊戲版本；無法判斷時回傳 None（由使用者手動輸入）。"""
    if not source or not source.strip():
        return None
    path = Path(source.strip())
    try:
        if path.is_dir():
            return _from_directory(path)
        if path.is_file() and path.suffix.lower() == ".zip":
            return _from_zip(path)
    except (OSError, zipfile.BadZipFile, ValueError):
        return None
    return None


def known_minecraft_versions() -> list[str]:
    """版本下拉選單的選項：對照表中所有區間端點（新版在前），供依輸入前綴過濾。"""
    found: set[tuple[int, ...]] = set()
    for row in load_table():
        found.add(row.low)
        found.add(row.high)
    return [
        ".".join(str(part) for part in parts) for parts in sorted(found, reverse=True)
    ]


__all__ = ["detect_minecraft_version", "known_minecraft_versions"]
