"""Modpack 資源目錄：唯讀索引 JAR 內的 model / blockstate / texture 位置，並快取已解析的 model。

單一責任：回答「某個資源路徑存在於哪些 archive」以及「該 model JSON 的 parent／textures／方塊面」。
模型到圖示的挑選規則在 ``app.icon_resolve``，圖片後處理在 ``app.icon_render``。
"""

from __future__ import annotations

import json
import threading
import zipfile
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import NamedTuple

from app.icon_runtime import get_runtime_asset_paths
from translation_tool.utils.cancellation import raise_if_cancelled
from translation_tool.utils.zip_safety import ZipReadBudget, read_limited

# 解析後的 model 以精簡結構快取；每個 JAR 的 key 會反覆走訪同一條 parent 鏈，
# 沒有快取時同一個 model JSON 會被解壓、解析上百次（並重複耗用 ZIP 讀取預算）。
_MODEL_MEMO_MAX = 8192


class ModelInfo(NamedTuple):
    """model JSON 中與挑選圖示有關的欄位。"""

    parent: str | None
    textures: dict[str, str]
    # 只有「單一完整方塊（from 0,0,0 到 16,16,16）」的 elements 才有值：face 名 -> texture 變數
    cube_faces: dict[str, str] | None
    # 面上帶 tintindex 的 texture 變數（草、葉等需要生物群系染色）
    tinted: frozenset[str]
    # 宣告了非 16x16 的 texture_size：貼圖是 3D 模型的 UV 展開圖，不適合當作圖示
    custom_uv: bool = False


_EMPTY_MODEL = ModelInfo(None, {}, None, frozenset())


def _is_full_cube(element: dict) -> bool:
    return element.get("from") == [0, 0, 0] and element.get("to") == [16, 16, 16]


def _parse_elements(elements) -> tuple[dict[str, str] | None, frozenset[str]]:
    if not isinstance(elements, list):
        return None, frozenset()
    tinted: set[str] = set()
    cube_faces: dict[str, str] | None = None
    for element in elements:
        faces = element.get("faces") if isinstance(element, dict) else None
        if not isinstance(faces, dict):
            continue
        for face in faces.values():
            if (
                isinstance(face, dict)
                and "tintindex" in face
                and isinstance(face.get("texture"), str)
            ):
                tinted.add(face["texture"].lstrip("#"))
    if len(elements) == 1 and isinstance(elements[0], dict):
        element = elements[0]
        faces = element.get("faces")
        if _is_full_cube(element) and isinstance(faces, dict):
            mapped = {
                name: face["texture"].lstrip("#")
                for name, face in faces.items()
                if isinstance(face, dict) and isinstance(face.get("texture"), str)
            }
            if {"up", "north", "east"} <= mapped.keys():
                cube_faces = mapped
    return cube_faces, frozenset(tinted)


def _has_custom_uv(texture_size) -> bool:
    return (
        isinstance(texture_size, list)
        and len(texture_size) == 2
        and texture_size != [16, 16]
    )


def parse_model(raw: bytes) -> ModelInfo:
    """把 model JSON bytes 轉成 ModelInfo；格式錯誤時回傳空 model。"""
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"), strict=False)
    except (ValueError, RecursionError):
        return _EMPTY_MODEL
    if not isinstance(data, dict):
        return _EMPTY_MODEL
    textures = data.get("textures")
    parent = data.get("parent")
    cube_faces, tinted = _parse_elements(data.get("elements"))
    return ModelInfo(
        parent if isinstance(parent, str) and parent else None,
        {str(k): v for k, v in textures.items() if isinstance(v, str)}
        if isinstance(textures, dict)
        else {},
        cube_faces,
        tinted,
        _has_custom_uv(data.get("texture_size")),
    )


class ModpackAssetCatalog:
    """唯讀索引 modpack 內的 model/blockstate/texture 資源位置。"""

    def __init__(
        self,
        archives: list[Path],
        *,
        current_archive: Path | None = None,
        client_archive: Path | None = None,
    ):
        self.archives = [Path(p).resolve() for p in archives]
        self.current_archive = (
            Path(current_archive).resolve() if current_archive is not None else None
        )
        self.client_archive = (
            Path(client_archive).resolve() if client_archive is not None else None
        )
        self.members: dict[Path, set[str]] = {}
        self.resources: dict[str, list[Path]] = defaultdict(list)
        self.texture_names: dict[str, list[str]] = defaultdict(list)
        self._handles: OrderedDict[Path, zipfile.ZipFile] = OrderedDict()
        self._budgets: dict[Path, ZipReadBudget] = {}
        self._models: OrderedDict[tuple[str, Path | None], ModelInfo | None] = (
            OrderedDict()
        )
        self._lock = threading.Lock()
        self._models_lock = threading.Lock()
        self._resolved_paths: dict[Path, Path] = {}
        for archive in self.archives:
            raise_if_cancelled()
            self._index_archive(archive)
        for paths in self.resources.values():
            raise_if_cancelled()
            paths.sort(key=lambda p: str(p).casefold())

    def _resolved(self, path: Path | None) -> Path | None:
        """``Path.resolve()`` 的快取版：每個 key 都會查詢同一個 JAR，不應每次都做 realpath 系統呼叫。"""
        if not path:
            return None
        cached = self._resolved_paths.get(path)
        if cached is None:
            cached = self._resolved_paths[path] = Path(path).resolve()
        return cached

    def _index_archive(self, archive: Path) -> None:
        try:
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
        except (OSError, zipfile.BadZipFile):
            return
        self.members[archive] = names
        for name in names:
            raise_if_cancelled()
            parts = name.split("/")
            if (
                len(parts) >= 4
                and parts[0] == "assets"
                and parts[2] in {"models", "blockstates", "textures"}
            ):
                self.resources[name].append(archive)
                if "/textures/" in name:
                    self.texture_names[name.rsplit("/", 1)[-1].casefold()].append(name)

    @classmethod
    def from_mods_directory(cls, source_root: Path) -> ModpackAssetCatalog:
        source_root = Path(source_root).resolve()
        archives = []
        for archive in source_root.glob("*.jar"):
            raise_if_cancelled()
            archives.append(archive)
        archives.sort(key=lambda p: p.name.casefold())
        runtime_assets = get_runtime_asset_paths(source_root)
        # A normal Minecraft installation keeps the client JAR beside the mods folder.
        client_jar = source_root.parent / f"{source_root.parent.name}.jar"
        if client_jar.is_file():
            archives.insert(0, client_jar)
        else:
            client_jar = None
        loader_archives = [
            path for path in runtime_assets if path.name.endswith("-universal.jar")
        ]
        archives = loader_archives + archives
        return cls(archives, client_archive=client_jar)

    @property
    def has_client_assets(self) -> bool:
        """是否找得到原版 client JAR（沒有時，引用原版貼圖的方塊無法顯示）。"""
        return self.client_archive is not None

    def has(self, resource_path: str) -> bool:
        return bool(self.resources.get(resource_path))

    def unique_texture_with_name(self, filename: str) -> str | None:
        """Return a texture path only when its basename is unambiguous in the pack."""
        matches = self.texture_names.get(filename.casefold(), [])
        return matches[0] if len(matches) == 1 else None

    def archive_for(
        self, resource_path: str, *, current_archive: Path | None = None
    ) -> Path | None:
        candidates = self.resources.get(resource_path, [])
        current = self._resolved(current_archive)
        namespace = (
            resource_path.split("/", 2)[1]
            if resource_path.startswith("assets/")
            else ""
        )
        if current in candidates:
            return current
        if namespace == "minecraft" and self.client_archive in candidates:
            return self.client_archive
        return candidates[0] if candidates else None

    def read(
        self,
        resource_path: str,
        *,
        current_archive: Path | None = None,
        current_zf: zipfile.ZipFile | None = None,
        budget: ZipReadBudget | None = None,
    ) -> bytes | None:
        candidates = self.resources.get(resource_path, [])
        current = self._resolved(current_archive)
        preferred = self.archive_for(resource_path, current_archive=current)
        ordered = ([preferred] if preferred is not None else []) + [
            path for path in candidates if path != preferred
        ]
        for archive in ordered:
            if archive == current and current_zf is not None:
                try:
                    return read_limited(current_zf, resource_path, budget=budget)
                except (KeyError, OSError, zipfile.BadZipFile):
                    continue
            zf = self._get_handle(archive)
            if zf is None:
                continue
            try:
                archive_budget = self._budgets.setdefault(
                    archive, ZipReadBudget.for_icon_scan(archive.name)
                )
                return read_limited(zf, resource_path, budget=archive_budget)
            except (KeyError, OSError, zipfile.BadZipFile):
                continue
        return None

    def read_model(
        self,
        resource_path: str,
        *,
        current_archive: Path | None = None,
        current_zf: zipfile.ZipFile | None = None,
        budget: ZipReadBudget | None = None,
    ) -> ModelInfo | None:
        """讀取並解析 model／blockstate 以外的 JSON model；結果快取，同一資源只解壓一次。

        找不到資源回傳 None（同樣被快取，避免對不存在的原版 parent 反覆查詢）。
        """
        preferred = self.archive_for(resource_path, current_archive=current_archive)
        memo_key = (resource_path, preferred)
        with self._models_lock:
            if memo_key in self._models:
                self._models.move_to_end(memo_key)
                return self._models[memo_key]
        raw = self.read(
            resource_path,
            current_archive=current_archive,
            current_zf=current_zf,
            budget=budget,
        )
        info = parse_model(raw) if raw is not None else None
        with self._models_lock:
            self._models[memo_key] = info
            while len(self._models) > _MODEL_MEMO_MAX:
                self._models.popitem(last=False)
        return info

    def _get_handle(self, archive: Path) -> zipfile.ZipFile | None:
        with self._lock:
            if archive in self._handles:
                self._handles.move_to_end(archive)
                return self._handles[archive]
            while len(self._handles) >= 16:
                _, old = self._handles.popitem(last=False)
                old.close()
            try:
                handle = zipfile.ZipFile(archive)
            except (OSError, zipfile.BadZipFile):
                return None
            self._handles[archive] = handle
            return handle

    def close(self) -> None:
        with self._lock:
            while self._handles:
                _, handle = self._handles.popitem(last=False)
                handle.close()
        with self._models_lock:
            self._models.clear()
