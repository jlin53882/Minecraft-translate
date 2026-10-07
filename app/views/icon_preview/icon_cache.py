"""圖示預覽頁的圖示提取與快取輔助函式（由 icon_preview_view.py 拆出，#114）。

含 JAR 內 model JSON 解析、圖示提取與批次處理、model index／L2 entries 快取與進度輔助。
測試要 monkeypatch 這些函式時，請 patch 本模組（呼叫者都在這裡查名稱）。
"""

import json
import shutil
import threading
import unicodedata
import zipfile
from collections import OrderedDict, defaultdict
from concurrent.futures import as_completed
from pathlib import Path

from app.icon_reader import IconRef
from app.icon_runtime import get_runtime_asset_paths
from translation_tool.utils.app_paths import get_data_root
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor
from translation_tool.utils.zip_safety import (
    ArchiveBudgetError,
    ZipReadBudget,
    read_limited,
)

# 只有這些內容 key 需要進行 model icon lookup。
# 不在清單裡的 key（如 _comment、advancements.*、recipe_type、jei.* 等）不該有 icon。
_CONTENT_ICON_PREFIXES = frozenset(
    [
        "item",
        "block",
        "entity",
        "enchantment",
        "effect",
        "potion",
        "biome",
        "attribute",
        "tile",
        "-effect",
    ]
)


def _key_needs_icon(key: str) -> bool:
    """判斷 key 是否為需要 icon 的遊戲內容。

    只有 item/block/entity 等前綴才需要 icon。
    metadata key（如 _comment、advancements.*、jei.*）完全不該有 icon。
    """
    if "." not in key:
        return False  # 完全沒有 namespace，不可能是遊戲內容
    prefix = key.split(".")[0]
    return prefix in _CONTENT_ICON_PREFIXES


# ==================================================
# JAR Icon 提取輔助函式（Phase 1: Model JSON 解析）
# ==================================================


def _get_icon_cache_dir() -> Path:
    """取得 icon 快取根目錄（統一至 .icon_cache/jar_icons/）。"""
    return get_data_root() / ".icon_cache" / "jar_icons"


def _get_model_index_cache_dir() -> Path:
    """取得 model index 快取目錄（.icon_cache/model_index/）。"""
    return get_data_root() / ".icon_cache" / "model_index"


def _get_jar_hash(jar_path: Path) -> str:
    """計算 JAR 的 hash（mtime + size），用於 cache 失效判斷。"""
    stat = jar_path.stat()
    return f"mtime:{stat.st_mtime:.0f}_size:{stat.st_size}"


def _migrate_old_icon_cache(source_root: Path) -> bool:
    """向後相容：將舊路徑的 icon cache 搬移至新路徑。

    舊路徑：source_root/_icon_preview/jar_icons/
    新路徑：.icon_cache/jar_icons/

    搬移條件：
        - 舊路徑存在
        - 新路徑尚不存在，或新路徑為空目錄

    回傳：
        True 表示有搬移，False 表示無需搬移
    """

    old_path = source_root / "_icon_preview" / "jar_icons"
    new_path = _get_icon_cache_dir()

    if not old_path.exists():
        # 舊路徑不存在，無需搬移
        return False

    # 新路徑已存在且有內容，不覆蓋
    if new_path.exists() and any(new_path.iterdir()):
        log_info(f"[IconPreview] 新 icon cache 已存在，放棄搬移舊路徑: {old_path}")
        return False

    # 確保新路徑的父目錄存在
    new_path.parent.mkdir(parents=True, exist_ok=True)

    # 搬移所有檔案
    files_moved = 0
    for old_file in old_path.glob("*.png"):
        new_file = new_path / old_file.name
        if not new_file.exists():
            shutil.move(str(old_file), str(new_file))
            files_moved += 1

    log_info(
        f"[IconPreview] 已將 {files_moved} 個 icon 檔案從舊路徑搬移至新路徑: {old_path} → {new_path}"
    )
    return True


# 程序內 model index 快取：(jar 路徑, modid) → (jar_hash, index)
# 同一個 JAR 的每個物品都會查 model index；原本每次都重新讀取並解析 JSON 快取檔
# （40 個 JAR 就讀了 9,179 次），改為每個 JAR 只讀一次。
_MODEL_INDEX_MEMO: dict[tuple[str, str], tuple[str, dict]] = {}
_MODEL_INDEX_MEMO_LOCK = threading.Lock()


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
        self._lock = threading.Lock()
        for archive in self.archives:
            try:
                with zipfile.ZipFile(archive) as zf:
                    names = set(zf.namelist())
            except (OSError, zipfile.BadZipFile):
                continue
            self.members[archive] = names
            for name in names:
                parts = name.split("/")
                if (
                    len(parts) >= 4
                    and parts[0] == "assets"
                    and parts[2] in {"models", "blockstates", "textures"}
                ):
                    self.resources[name].append(archive)
                    if "/textures/" in name:
                        self.texture_names[name.rsplit("/", 1)[-1].casefold()].append(
                            name
                        )
        for paths in self.resources.values():
            paths.sort(key=lambda p: str(p).casefold())

    @classmethod
    def from_mods_directory(cls, source_root: Path) -> "ModpackAssetCatalog":
        source_root = Path(source_root).resolve()
        archives = sorted(source_root.glob("*.jar"), key=lambda p: p.name.casefold())
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
        current = Path(current_archive).resolve() if current_archive else None
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
        current = Path(current_archive).resolve() if current_archive else None
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


def _resource_location(value: str, default_namespace: str) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value:
        return None
    if ":" in value:
        namespace, path = value.split(":", 1)
    else:
        namespace, path = default_namespace, value
    if not namespace or not path or path.startswith("/") or ".." in path.split("/"):
        return None
    return namespace, path


def _model_resource_path(namespace: str, path: str) -> str:
    return f"assets/{namespace}/models/{path.removesuffix('.json')}.json"


def _model_texture_values(
    namespace: str,
    model_path: str,
    catalog: ModpackAssetCatalog,
    *,
    current_archive: Path | None,
    current_zf: zipfile.ZipFile | None,
    budget: ZipReadBudget | None,
    visited: set[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """收集子模型優先、父模型補齊的 texture map。"""
    visited = visited or set()
    location = (namespace, model_path.removesuffix(".json"))
    if location in visited or len(visited) >= 32:
        return {}
    visited.add(location)
    data_path = _model_resource_path(*location)
    raw = catalog.read(
        data_path,
        current_archive=current_archive,
        current_zf=current_zf,
        budget=budget,
    )
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    textures = data.get("textures")
    result = (
        {str(key): value for key, value in textures.items() if isinstance(value, str)}
        if isinstance(textures, dict)
        else {}
    )
    parent = data.get("parent")
    parent_location = _resource_location(parent, location[0]) if parent else None
    if parent_location:
        parent_textures = _model_texture_values(
            *parent_location,
            catalog,
            current_archive=current_archive,
            current_zf=current_zf,
            budget=budget,
            visited=visited,
        )
        for key, value in parent_textures.items():
            result.setdefault(key, value)
    return result


def _resolve_texture_alias(textures: dict[str, str], key: str) -> str | None:
    seen: set[str] = set()
    value = textures.get(key)
    while isinstance(value, str) and value.startswith("#"):
        alias = value[1:]
        if alias in seen:
            return None
        seen.add(alias)
        value = textures.get(alias)
    return value if isinstance(value, str) and value else None


def _texture_candidates(textures: dict[str, str]) -> list[str]:
    keys = ["layer0", "front", "particle", "all", "side", "end", "top", "bottom"]
    ordered = [key for key in keys if key in textures]
    ordered.extend(key for key in textures if key not in ordered)
    results: list[str] = []
    for key in ordered:
        value = _resolve_texture_alias(textures, key)
        if value and value not in results:
            results.append(value)
    return results


def _png_resource_path(value: str) -> str | None:
    location = _resource_location(value, "minecraft")
    if location is None:
        return None
    namespace, path = location
    return f"assets/{namespace}/textures/{path.removesuffix('.png')}.png"


def _blockstate_models(
    namespace: str,
    block_name: str,
    catalog: ModpackAssetCatalog,
    *,
    current_archive: Path | None,
    current_zf: zipfile.ZipFile | None,
    budget: ZipReadBudget | None,
) -> list[tuple[str, str]]:
    path = f"assets/{namespace}/blockstates/{block_name}.json"
    raw = catalog.read(
        path,
        current_archive=current_archive,
        current_zf=current_zf,
        budget=budget,
    )
    if raw is None:
        return []
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    refs: list[str] = []

    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("model"), str):
                refs.append(value["model"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    if isinstance(data, dict):
        variants = data.get("variants")
        if isinstance(variants, dict) and variants:
            selected = variants.get("")
            if selected is None:
                selected = variants[min(variants)]
            collect(selected)
        if "multipart" in data:
            collect(data["multipart"])
    result = []
    for ref in refs:
        location = _resource_location(ref, namespace)
        if location and location not in result:
            result.append(location)
    return result


def _resolve_icon_from_catalog(
    jar_path: Path,
    lang_namespace: str,
    key: str,
    catalog: ModpackAssetCatalog,
    *,
    current_zf: zipfile.ZipFile | None,
    budget: ZipReadBudget | None,
) -> tuple[str, str, Path] | None:
    parts = key.split(".")
    if len(parts) < 3:
        return None
    prefix = parts[0]
    if prefix not in _CONTENT_ICON_PREFIXES:
        return None

    namespace_candidates: list[tuple[str, str]] = []
    key_namespace = parts[1]
    key_rest = ".".join(parts[2:])
    if key_namespace and (
        catalog.resources.get(f"assets/{key_namespace}/models/item/{key_rest}.json")
        or catalog.resources.get(f"assets/{key_namespace}/blockstates/{key_rest}.json")
        or catalog.resources.get(f"assets/{key_namespace}/models/block/{key_rest}.json")
    ):
        namespace_candidates.append((key_namespace, key_rest))
    if key_namespace == lang_namespace:
        namespace_candidates.append((lang_namespace, key_rest))
    elif lang_namespace in parts[1:]:
        mod_pos = parts.index(lang_namespace, 1)
        namespace_candidates.append((lang_namespace, ".".join(parts[mod_pos + 1 :])))

    model_subdirs = {
        "item": ("item", "block"),
        "block": ("block", "item"),
        "entity": ("entity", "item"),
        "enchantment": ("item", "block"),
        "effect": ("item",),
        "potion": ("item",),
        "biome": ("item",),
        "attribute": ("item",),
        "tile": ("block", "item"),
        "-effect": ("item",),
    }.get(prefix, (prefix,))

    for namespace, rest in dict.fromkeys(namespace_candidates):
        if not rest:
            continue
        model_refs: list[tuple[str, str]] = []
        for subdir in model_subdirs:
            path = f"assets/{namespace}/models/{subdir}/{rest}.json"
            if catalog.has(path):
                model_refs.append((namespace, f"{subdir}/{rest}"))
        if prefix == "block":
            model_refs.extend(
                _blockstate_models(
                    namespace,
                    rest,
                    catalog,
                    current_archive=jar_path,
                    current_zf=current_zf,
                    budget=budget,
                )
            )

        for model_namespace, model_path in dict.fromkeys(model_refs):
            textures = _model_texture_values(
                model_namespace,
                model_path,
                catalog,
                current_archive=jar_path,
                current_zf=current_zf,
                budget=budget,
            )
            for texture in _texture_candidates(textures):
                png_path = _png_resource_path(texture)
                if png_path and not catalog.has(png_path):
                    # Some addon JARs ship models that retain a dependency namespace
                    # after the source texture moved/was renamed. Only accept a
                    # basename fallback when the whole pack has exactly one match.
                    png_path = catalog.unique_texture_with_name(
                        png_path.rsplit("/", 1)[-1]
                    )
                if png_path and catalog.has(png_path):
                    archive = catalog.archive_for(png_path, current_archive=jar_path)
                    if archive is not None:
                        return texture, png_path, archive
    return None


def _load_model_index_from_cache(jar_path: Path, modid: str) -> dict | None:
    """嘗試讀取 model index cache（先查程序內快取，再查磁碟）。

    失效條件：JAR 的 mtime/size 改變，或 cache 檔不存在/格式無效。

    回傳：
        model_index dict（name → [路徑列表]），或 None（cache miss）
    """
    memo_key = (str(jar_path), modid)
    current_hash = _get_jar_hash(jar_path)
    with _MODEL_INDEX_MEMO_LOCK:
        memo = _MODEL_INDEX_MEMO.get(memo_key)
    if memo is not None and memo[0] == current_hash:
        return memo[1]

    index = _load_model_index_from_disk(jar_path, modid, current_hash)
    if index is not None:
        with _MODEL_INDEX_MEMO_LOCK:
            _MODEL_INDEX_MEMO[memo_key] = (current_hash, index)
    return index


def _load_model_index_from_disk(
    jar_path: Path, modid: str, current_hash: str
) -> dict | None:
    """從磁碟讀取 model index cache；hash 或 modid 不符時回傳 None。"""
    cache_dir = _get_model_index_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_jar_name = jar_path.stem  # stem 已剝除副檔名
    cache_file = cache_dir / f"{safe_jar_name}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    # 檢查 jar_hash 是否匹配
    if data.get("jar_hash") != current_hash:
        return None

    if data.get("modid") != modid:
        return None

    return data.get("index")


def _save_model_index_to_cache(jar_path: Path, modid: str, model_index: dict):
    """將 model index 寫入磁碟 cache（atomic write）。"""
    cache_dir = _get_model_index_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_jar_name = jar_path.stem  # stem 已剝除副檔名
    cache_file = cache_dir / f"{safe_jar_name}.json"

    data = {
        "jar_name": jar_path.name,
        "jar_hash": _get_jar_hash(jar_path),
        "modid": modid,
        "index": model_index,
    }

    tmp = cache_dir / f"{cache_file.stem}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_file)
    with _MODEL_INDEX_MEMO_LOCK:
        _MODEL_INDEX_MEMO[(str(jar_path), modid)] = (data["jar_hash"], model_index)


def _build_model_index(names: list[str], modid: str) -> dict[str, list[str]]:
    """動態掃描所有 .json model 檔，建立 name → [路徑列表] index。

    name = 相對路徑（保留子目錄），例如 block/restonia_crystal_block。
    同一個 name 可能來自不同子目錄（block/ vs item/），全部保留。
    """
    index: dict[str, list[str]] = {}
    prefix = f"assets/{modid}/models/"

    for n in names:
        if not (n.startswith(prefix) and n.endswith(".json")):
            continue
        rel = n[len(prefix) :]
        name = rel.replace(".json", "")
        # 保留子目錄前綴（例如 block/restonia_crystal_block）
        # 這樣 _try_extract_mod_icon_from_model 可以用完整路徑做精準 lookup
        if name not in index:
            index[name] = []
        index[name].append(n)

    return index


def _get_texture_value(model_data: dict) -> str | None:
    """從 model JSON 的 textures 欄位取值（無白名單優先順序）。

    邏輯：
        - 只有 1 個 key → 直接取那個值
        - 超過 1 個 key → 依序取：layer0 → front → particle → 任意第一個
    """
    textures = model_data.get("textures", {})
    if not textures:
        return None

    if len(textures) == 1:
        return next(iter(textures.values()))

    for key in ["layer0", "front", "particle"]:
        if key in textures:
            return textures[key]

    return next(iter(textures.values()))


def _follow_parent_chain(
    model_path: str,
    names: set[str],
    modid: str,
    zf: zipfile.ZipFile,
    visited: set[str] | None = None,
    budget: ZipReadBudget | None = None,
) -> str | None:
    """沿 parent chain 遞迴向上找，直到找到有 textures 的 model。

    遇到 minecraft: 開頭的 parent 直接跳過（不處理 Minecraft 內建資源）。
    """
    if visited is None:
        visited = set()

    if model_path in visited or model_path not in names:
        return None
    visited.add(model_path)

    try:
        raw = read_limited(zf, model_path, budget=budget).decode(
            "utf-8", errors="replace"
        )
        data = json.loads(raw)
    except ArchiveBudgetError:
        raise  # 整包累計超限：交給呼叫端中止這個 JAR，不當成單一 model 解析失敗
    except Exception:  # noqa: BLE001
        return None

    tex_val = _get_texture_value(data)
    if tex_val:
        return tex_val

    parent = data.get("parent")
    if not parent:
        return None

    if ":" in parent:
        ns, path = parent.split(":", 1)
        if ns == modid:
            parent_path = f"assets/{ns}/models/{path}.json"
        elif ns == "minecraft":
            return None  # 跳過 Minecraft 內建資源
        else:
            return None
    else:
        base = str(Path(model_path).parent).replace("\\", "/")
        parent_path = f"{base}/{parent}.json"

    return _follow_parent_chain(parent_path, names, modid, zf, visited, budget)


def _texture_to_png_path(tex_val: str) -> str | None:
    """將 texture value（namespace:path）轉換為 JAR 內的 PNG 路徑。

    格式：namespace:path → assets/namespace/textures/path.png
    """
    if not tex_val or ":" not in tex_val:
        return None
    ns, path = tex_val.split(":", 1)
    return f"assets/{ns}/textures/{path}.png"


def _try_extract_mod_icon_from_model(
    jar_path: Path,
    modid: str,
    zf: zipfile.ZipFile,
    names: set[str],
    key: str | None = None,
    budget: ZipReadBudget | None = None,
) -> tuple[str, str] | None:
    """嘗試從 model JSON 解析 mod icon。

    流程：
        1. 建立/讀取 model index（使用 cache）
        2. 如果有 key，先用 key 查 item 自己的 model texture（精準匹配）
        3. 移除 logo/icon.png fallback（錯誤的 icon 比沒有更糟）
           （但只有當 key 的 namespace 與 modid 一致時才套用 fallback）
        4. 使用 texture fallback 策略取值

    回傳：
        (texture_value, png_path) 或 None
    """
    model_index = _load_model_index_from_cache(jar_path, modid)
    if model_index is None:
        model_index = _build_model_index(list(names), modid)
        _save_model_index_to_cache(jar_path, modid, model_index)

    # ===== 優先：嘗試用 key 查 item 自己的 model texture =====
    if key:
        # 將 key 轉為 model name
        # 例如：block.actuallyadditions.restonia_crystal_block → block/restonia_crystal_block
        #       item.actuallyadditions.drill → item/drill
        # 原理：key = "<prefix>.<modid>.<name>"，去掉 modid 前綴就是 model name
        prefix = key.split(".")[0]  # "block" 或 "item" 等
        rest = key[len(prefix) + 1 + len(modid) + 1 :]  # "restonia_crystal_block"
        model_names = [f"{prefix}/{rest}"]
        if prefix == "entity":
            # 一些模組的 entity 翻譯（例如 Worm）沒有 entity model，
            # 但同名物品模型提供了合適的圖示；只在精確同名時作為 fallback。
            model_names.append(f"item/{rest}")

        for model_name in model_names:
            if model_name not in model_index:
                continue
            for model_path in model_index[model_name]:
                tex_val = _follow_parent_chain(
                    model_path, names, modid, zf, budget=budget
                )
                if not tex_val:
                    continue
                png_path = _texture_to_png_path(tex_val)
                if png_path and png_path in names:
                    return tex_val, png_path

    # ===== Fallback：找 icon/logo/item_icon/block_icon 模型 =====
    # 限制：只有當 key 的 namespace 與 modid 一致時才套用 fallback
    # 原因：icon/logo 模型是該 mod 的專屬資源（如 assets/actuallyadditions/models/icon.json）
    # 不該用在 minecraft 命名空間的 key（如 block.minecraft.banner.actuallyadditions.*）
    # key 格式：<prefix>.<namespace>.<name> 或 <prefix>.<name>（vanilla 無 namespace portion）
    # namespace portion = key.split(".")[1]
    # 只有當有 namespace portion（key 有 >= 3 parts）且 namespace != modid 時才阻止
    if key:
        key_parts = key.split(".")
        if len(key_parts) >= 3:
            key_ns = key_parts[1]  # namespace portion
            if key_ns != modid:
                return None  # namespace 不一致，直接回 None，不做任何 fallback

    # 當 model lookup 失敗時，不做任何 logo/icon.png fallback，直接回 None
    return None


# ==================================================
# 批次 Icon 提取（每個 JAR 只開一次 ZIP）
# ==================================================


def _apply_icon_index(jar_to_entries: dict[str, list], icon_index, progress_cb) -> int:
    """索引存在：直接用索引套用 icon，完全不做 JAR I/O。回傳處理的 JAR 數量。"""
    applied = 0
    for entries in jar_to_entries.values():
        for e in entries:
            if not (hasattr(e, "modid") and hasattr(e, "key")):
                continue
            key = e.key
            if key in icon_index:
                e.icon_path = icon_index[key]
                applied += 1
    if progress_cb:
        progress_cb(len(jar_to_entries), len(jar_to_entries))
    log_info(f"[IconPreview] 索引命中：{applied} 個 entry 直接套用 icon")
    return len(jar_to_entries)


def _run_jar_workers(jar_to_entries: dict[str, list], process_jar, progress_cb) -> int:
    """以 ThreadPoolExecutor 並行處理每個 JAR，並把結果套用到對應的 entry。回傳已處理的 JAR 數量。"""
    processed = 0
    total = len(jar_to_entries)
    config_workers = (
        load_config().get("translator", {}).get("parallel_execution_workers", 4)
    )
    max_workers = max(1, config_workers) if isinstance(config_workers, int) else 4
    with ContextThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_jar, jar_name): jar_name
            for jar_name in jar_to_entries
        }
        for future in as_completed(futures):
            jar_name = futures[future]
            try:
                entry_icon_paths = future.result()
                for e in jar_to_entries.get(jar_name, []):
                    if hasattr(e, "key") and e.key in entry_icon_paths:
                        uri = entry_icon_paths[e.key]
                        if uri:
                            e.icon_path = uri
            except Exception as exc:  # noqa: BLE001 - 單一 JAR 圖示解析失敗不中止整批，但要留下是哪個 JAR
                log_warning(
                    f"[IconPreview] {jar_name} 圖示解析失敗：{exc!r}", exc_info=True
                )
            processed += 1
            if progress_cb:
                progress_cb(processed, total)
                if processed % 50 == 0:
                    log_info(f"[IconPreview] 處理進度：{processed}/{total} JARs")
    return processed


def _batch_extract_jar_icons(
    jar_to_entries: dict[str, list],
    icon_cache_root: Path,
    source_root: Path,
    progress_cb=None,
) -> int:
    """批次處理多個 JAR 的 icon 提取（支援預建立索引 + ThreadPoolExecutor）。

    PR60 優化架構：
        1. 嘗試從預建立索引讀取（instant，零 JAR I/O）
        2. 若無索引：使用 ThreadPoolExecutor 8 threads 建立索引（1-2 分鐘）
        3. 若索引正在建立中（另一執行緒）：降級為 ThreadPoolExecutor 即時處理

    參數：
        jar_to_entries: {jar_name: [entries]}，同一個 JAR 的所有 entry
        icon_cache_root: （已廢棄，參數保留但不再使用）
        source_root: 資料根目錄（JAR 所在位置）
        progress_cb: 進度回呼（可選）

    回傳：
        處理的 JAR 數量
    """

    # ===== Phase 0: Per-batch in-memory cache：同一 (modid, key) 在同一批次內不重複解析 =====
    _result_cache: dict[tuple[str, str], str | None] = {}

    # ===== Phase 1: 嘗試從預建立索引讀取（instant）=====
    icon_index = None
    try:
        from app import icon_index as idx_module

        icon_index = idx_module.load_icon_index(source_root)
    except Exception as exc:  # noqa: BLE001 - 預建索引載入失敗時改為逐 JAR 解析，但要留下紀錄
        log_warning(f"[IconPreview] 載入預建圖示索引失敗，改為逐 JAR 解析：{exc!r}")

    if icon_index is not None:
        return _apply_icon_index(jar_to_entries, icon_index, progress_cb)

    asset_catalog = ModpackAssetCatalog.from_mods_directory(source_root)

    # ===== Phase 2: 無索引 → ThreadPoolExecutor 即時處理 =====
    log_info(
        f"[IconPreview] 無索引，啟動 ThreadPoolExecutor 處理 {len(jar_to_entries)} 個 JAR"
    )

    def _process_jar(jar_name: str) -> dict[str, str | None]:
        """Worker：處理單一 JAR，回傳 {key: icon_uri or None}。"""
        jar_path = source_root / jar_name
        result_map: dict[str, str | None] = {}
        if not jar_path.exists():
            return result_map
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                budget = ZipReadBudget.for_icon_scan(jar_name)
                for e in jar_to_entries.get(jar_name, []):
                    if not (hasattr(e, "modid") and hasattr(e, "key")):
                        continue
                    modid = e.modid
                    key = e.key
                    # 預先過濾：不需要 icon 的 key 直接跳過，不浪費 lookup 時間
                    if not _key_needs_icon(key):
                        continue
                    # Per-(modid, key) cache：同 (modid, key) 在同批次不重複解析
                    cache_key = (modid, key)
                    if cache_key in _result_cache:
                        result_map[key] = _result_cache[cache_key]
                        continue
                    res = _resolve_icon_from_catalog(
                        jar_path,
                        modid,
                        key,
                        asset_catalog,
                        current_zf=zf,
                        budget=budget,
                    )
                    if res:
                        _tex_val, png_path, texture_archive = res
                        uri = IconRef(texture_archive, png_path).to_uri()
                        result_map[key] = uri
                        _result_cache[cache_key] = uri
                    else:
                        _result_cache[cache_key] = None
                        result_map[key] = None
        except ArchiveBudgetError:
            # 累計讀取超過安全上限（budget 已記錄警告）：保留已解析的部分，略過剩餘項目
            log_warning(
                f"[IconPreview] 略過 {jar_name} 剩餘項目的圖示解析（累計讀取超限）"
            )
        except Exception as exc:  # noqa: BLE001 - 單一 JAR 解析失敗保留已解析的部分，但要留下是哪個 JAR
            log_warning(
                f"[IconPreview] {jar_name} 圖示解析中斷：{exc!r}", exc_info=True
            )
        return result_map

    try:
        processed = _run_jar_workers(jar_to_entries, _process_jar, progress_cb)
    finally:
        asset_catalog.close()

    log_info(f"[IconPreview] ThreadPoolExecutor 完成：{processed} 個 JAR")
    return processed


# ==================================================
# L2 磁碟快取工具函式
# ==================================================


# ==================================================
# Phase 進度條輔助
# ==================================================


def to_halfwidth(text):
    """
    將字串正規化為半形（NFKC）
    - 只處理 str
    - 非 str 原樣返回（安全）
    """
    if not isinstance(text, str):
        return text
    return unicodedata.normalize("NFKC", text)
