"""lang key -> 圖示貼圖的挑選規則（純解析，不做影像處理）。

單一責任：給定 lang key 與 ``ModpackAssetCatalog``，沿 item model / blockstate / parent 鏈找出
最能代表該物品的貼圖；方塊模型是完整方塊時額外回報頂／左／右三面貼圖供等角預覽。
缺少原版 client JAR 時，原版 parent 以名稱辨識（例如 ``block/cube_all``），貼圖缺漏則不猜測。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, NamedTuple

from app.icon_catalog import ModelInfo, ModpackAssetCatalog
from app.icon_reader import IconRef, encode_meta
from app.icon_render import SYNTHETIC_SCHEME, tint_for_name
from translation_tool.utils.zip_safety import ZipReadBudget

# 只有這些內容 key 需要進行 model icon lookup。
CONTENT_ICON_PREFIXES = frozenset(
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


def key_needs_icon(key: str) -> bool:
    """lang key 是否為需要圖示的遊戲內容（item／block／entity 等）；``_comment``、``jei.*`` 等不需要。"""
    return "." in key and key.split(".", 1)[0] in CONTENT_ICON_PREFIXES


_MAX_CHAIN_DEPTH = 32

# 原版方塊 parent 模板 -> 三個可見面（up / east / north）使用的 texture 變數
_CUBE_TEMPLATES: dict[str, dict[str, str]] = {
    "block/cube_all": {"up": "all", "east": "all", "north": "all"},
    "block/cube_mirrored_all": {"up": "all", "east": "all", "north": "all"},
    "block/leaves": {"up": "all", "east": "all", "north": "all"},
    "block/cube_column": {"up": "end", "east": "side", "north": "side"},
    "block/cube_column_mirrored": {"up": "end", "east": "side", "north": "side"},
    "block/cube_column_horizontal": {"up": "side", "east": "end", "north": "side"},
    "block/cube_bottom_top": {"up": "top", "east": "side", "north": "side"},
    "block/cube_top": {"up": "top", "east": "side", "north": "side"},
    "block/orientable": {"up": "top", "east": "side", "north": "front"},
    "block/orientable_with_bottom": {"up": "top", "east": "side", "north": "front"},
    "block/cube": {"up": "up", "east": "east", "north": "north"},
    "block/cube_directional": {"up": "up", "east": "east", "north": "north"},
}

_SPAWN_EGG_TEMPLATE = "item/template_spawn_egg"

_FLAT_TEXTURE_ORDER = (
    "layer0",
    "front",
    "all",
    "side",
    "top",
    "end",
    "bottom",
    "up",
    "north",
    "south",
    "east",
    "west",
    "down",
)

_MODEL_SUBDIRS = {
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
}

_TEXTURE_NAME_DIRS = {
    "item": ("item", "block"),
    "block": ("block", "item"),
    "effect": ("mob_effect",),
    "-effect": ("mob_effect",),
}


class IconHit(tuple):
    """``(texture 值, PNG 資源路徑, 貼圖所在 archive)``，``meta`` 帶預覽提示。

    與一般 3-tuple 相等（既有呼叫端與測試不受影響）；``meta`` 可能包含：
    ``k``（``iso`` 等角方塊／``egg`` 程式繪製刷怪蛋）、``f``（等角三面來源）、
    ``t``（染色種類）、``m``（來源 model 資源路徑）。
    """

    def __new__(
        cls,
        texture: str,
        png_path: str,
        archive: Path | None,
        meta: dict[str, Any] | None = None,
    ):
        self = super().__new__(cls, (texture, png_path, archive))
        self.meta = meta
        return self


class _Chain(NamedTuple):
    textures: dict[str, str]
    parents: list[
        str
    ]  # 走訪到的 parent（含找不到的原版模板），形如 "minecraft:block/cube_all"
    cube_faces: dict[str, str] | None
    tinted: frozenset[str]
    custom_uv: bool


class _Context(NamedTuple):
    jar_path: Path
    catalog: ModpackAssetCatalog
    current_zf: zipfile.ZipFile | None
    budget: ZipReadBudget | None


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


def _png_resource_path(value: str) -> str | None:
    location = _resource_location(value, "minecraft")
    if location is None:
        return None
    namespace, path = location
    return f"assets/{namespace}/textures/{path.removesuffix('.png')}.png"


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
    ordered = [key for key in _FLAT_TEXTURE_ORDER if key in textures]
    ordered.extend(key for key in textures if key not in ordered and key != "particle")
    if "particle" in textures:
        ordered.append("particle")  # 粒子貼圖常是破碎效果，只作最後手段
    results: list[str] = []
    for key in ordered:
        value = _resolve_texture_alias(textures, key)
        if value and value not in results:
            results.append(value)
    return results


def _parent_location(
    ctx: _Context, parent: str, namespace: str
) -> tuple[str, str] | None:
    """解析 parent 參照。未帶命名空間時：自己的命名空間有就用，否則視為原版（minecraft）。"""
    if ":" in parent:
        return _resource_location(parent, namespace)
    own = _resource_location(parent, namespace)
    if own and ctx.catalog.has(_model_resource_path(*own)):
        return own
    return _resource_location(parent, "minecraft")


def _walk_chain(ctx: _Context, namespace: str, model_path: str) -> _Chain:
    """沿 parent 鏈收集 texture map（子模型優先）、方塊面與模板名稱。"""
    textures: dict[str, str] = {}
    parents: list[str] = []
    cube_faces: dict[str, str] | None = None
    tinted: set[str] = set()
    custom_uv = False
    location: tuple[str, str] | None = (namespace, model_path.removesuffix(".json"))
    visited: set[tuple[str, str]] = set()
    while location and location not in visited and len(visited) < _MAX_CHAIN_DEPTH:
        visited.add(location)
        info: ModelInfo | None = ctx.catalog.read_model(
            _model_resource_path(*location),
            current_archive=ctx.jar_path,
            current_zf=ctx.current_zf,
            budget=ctx.budget,
        )
        if info is None:
            break
        for key, value in info.textures.items():
            textures.setdefault(key, value)
        if cube_faces is None:
            cube_faces = info.cube_faces
        tinted |= info.tinted
        custom_uv = custom_uv or info.custom_uv
        if not info.parent:
            break
        location = _parent_location(ctx, info.parent, location[0])
        if location:
            parents.append(f"{location[0]}:{location[1]}")
    return _Chain(textures, parents, cube_faces, frozenset(tinted), custom_uv)


def _locate_png(ctx: _Context, texture: str) -> tuple[str, Path] | None:
    """texture 值 -> (PNG 資源路徑, 所在 archive)。

    非原版命名空間找不到時，只在整包恰有一個同名貼圖時採用（附屬模組搬移貼圖的情況）。
    原版貼圖缺漏（沒有 client JAR）時不猜測：猜到別的模組同名貼圖比沒有圖示更糟。
    """
    png_path = _png_resource_path(texture)
    if png_path is None:
        return None
    catalog = ctx.catalog
    if not catalog.has(png_path):
        if png_path.split("/", 2)[1] == "minecraft":
            return None
        png_path = catalog.unique_texture_with_name(png_path.rsplit("/", 1)[-1])
        if png_path is None:
            return None
    archive = catalog.archive_for(png_path, current_archive=ctx.jar_path)
    return (png_path, archive) if archive is not None else None


def _face_texture(chain: _Chain, var: str | None) -> str | None:
    if not var:
        return None
    if var in chain.textures:
        return _resolve_texture_alias(chain.textures, var)
    return var if ("/" in var or ":" in var) else None


def _cube_mapping(chain: _Chain) -> dict[str, str] | None:
    if chain.cube_faces is not None:
        return chain.cube_faces
    for parent in chain.parents:
        namespace, _, path = parent.partition(":")
        if namespace == "minecraft" and path in _CUBE_TEMPLATES:
            return _CUBE_TEMPLATES[path]
    return None


def _tint_kind(png_path: str, var: str | None, chain: _Chain) -> str | None:
    kind = tint_for_name(png_path.rsplit("/", 1)[-1])
    if kind is None and var and var in chain.tinted:
        return "grass"
    return kind


def _isometric_hit(ctx: _Context, chain: _Chain, model: str) -> IconHit | None:
    mapping = _cube_mapping(chain)
    if mapping is None:
        return None
    faces: dict[str, tuple[str, str, Path, str | None]] = {}
    for face, name in (("up", "top"), ("east", "left"), ("north", "right")):
        var = mapping.get(face)
        texture = _face_texture(chain, var)
        located = _locate_png(ctx, texture) if texture else None
        if located is None:
            return None
        png_path, archive = located
        faces[name] = (texture, png_path, archive, _tint_kind(png_path, var, chain))
    texture, png_path, archive, _ = faces["right"]
    meta: dict[str, Any] = {"k": "iso", "m": model}
    differing = {}
    for name in ("top", "left"):
        _t, face_png, face_archive, _tint = faces[name]
        if (face_png, face_archive) == (png_path, archive):
            continue  # 與主貼圖相同者省略（cube_all 只需 {"k": "iso"}）
        differing[name] = (
            face_png if face_archive == archive else [face_archive.as_posix(), face_png]
        )
    if differing:
        meta["f"] = differing
    tints = {name: face[3] for name, face in faces.items() if face[3]}
    if tints:
        meta["t"] = tints
    return IconHit(texture, png_path, archive, meta)


def _flat_hit(ctx: _Context, chain: _Chain, model: str) -> IconHit | None:
    for texture in _texture_candidates(chain.textures):
        located = _locate_png(ctx, texture)
        if located is None:
            continue
        png_path, archive = located
        meta: dict[str, Any] = {"m": model}
        if chain.custom_uv:
            meta["u"] = 1
        tint = tint_for_name(png_path.rsplit("/", 1)[-1])
        if tint is None and chain.tinted:
            tint = "grass"
        if tint:
            meta["t"] = tint
        return IconHit(texture, png_path, archive, meta)
    return None


def _spawn_egg_hit(chain: _Chain, model: str) -> IconHit | None:
    if any(p == f"minecraft:{_SPAWN_EGG_TEMPLATE}" for p in chain.parents):
        return IconHit("synthetic:spawn_egg", "", None, {"k": "egg", "m": model})
    return None


def _resolve_model(ctx: _Context, namespace: str, model_path: str) -> IconHit | None:
    chain = _walk_chain(ctx, namespace, model_path)
    model = f"{namespace}:{model_path.removesuffix('.json')}"
    return (
        _isometric_hit(ctx, chain, model)
        or _flat_hit(ctx, chain, model)
        or _spawn_egg_hit(chain, model)
    )


def _blockstate_models(
    namespace: str,
    block_name: str,
    catalog: ModpackAssetCatalog,
    *,
    current_archive: Path | None,
    current_zf: zipfile.ZipFile | None,
    budget: ZipReadBudget | None,
) -> list[tuple[str, str]]:
    import json

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


def _name_variants(rest: str) -> list[str]:
    return [rest, rest.replace(".", "_")] if "." in rest else [rest]


def _namespace_candidates(
    parts: list[str], lang_namespace: str, catalog: ModpackAssetCatalog
) -> list[tuple[str, str]]:
    """key 可能指向的 (命名空間, 名稱)；以實際存在資源或與 lang 命名空間一致為準。"""
    candidates: list[tuple[str, str]] = []
    key_namespace = parts[1]
    key_rest = ".".join(parts[2:])
    if key_namespace and any(
        catalog.resources.get(f"assets/{key_namespace}/{sub}/{key_rest}.{ext}")
        for sub, ext in (
            ("models/item", "json"),
            ("blockstates", "json"),
            ("models/block", "json"),
            ("textures/mob_effect", "png"),
            ("textures/item", "png"),
            ("textures/block", "png"),
        )
    ):
        candidates.append((key_namespace, key_rest))
    if key_namespace == lang_namespace:
        candidates.append((lang_namespace, key_rest))
    elif lang_namespace in parts[1:]:
        mod_pos = parts.index(lang_namespace, 1)
        candidates.append((lang_namespace, ".".join(parts[mod_pos + 1 :])))
    return list(dict.fromkeys(candidates))


def _model_refs(
    ctx: _Context, prefix: str, namespace: str, rest: str
) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for name in _name_variants(rest):
        for subdir in _MODEL_SUBDIRS.get(prefix, (prefix,)):
            if ctx.catalog.has(_model_resource_path(namespace, f"{subdir}/{name}")):
                refs.append((namespace, f"{subdir}/{name}"))
        if prefix == "entity":
            for egg in (f"item/{name}_spawn_egg", f"item/spawn_egg_{name}"):
                if ctx.catalog.has(_model_resource_path(namespace, egg)):
                    refs.append((namespace, egg))
    if prefix == "block":
        refs.extend(
            _blockstate_models(
                namespace,
                rest,
                ctx.catalog,
                current_archive=ctx.jar_path,
                current_zf=ctx.current_zf,
                budget=ctx.budget,
            )
        )
    return list(dict.fromkeys(refs))


def _texture_by_name(
    ctx: _Context, prefix: str, namespace: str, rest: str
) -> IconHit | None:
    """沒有可用 model 時，以「同名貼圖」作為精準比對（item/block/mob_effect）。"""
    for name in _name_variants(rest):
        for subdir in _TEXTURE_NAME_DIRS.get(prefix, ()):
            png_path = f"assets/{namespace}/textures/{subdir}/{name}.png"
            archive = ctx.catalog.archive_for(png_path, current_archive=ctx.jar_path)
            if archive is not None:
                meta: dict[str, Any] = {}
                tint = tint_for_name(name)
                if tint:
                    meta["t"] = tint
                return IconHit(
                    f"{namespace}:{subdir}/{name}", png_path, archive, meta or None
                )
    return None


def resolve_icon(
    jar_path: Path,
    lang_namespace: str,
    key: str,
    catalog: ModpackAssetCatalog,
    *,
    current_zf: zipfile.ZipFile | None,
    budget: ZipReadBudget | None,
) -> IconHit | None:
    """解析單一 lang key 的圖示；找不到回傳 None。"""
    parts = key.split(".")
    if len(parts) < 3 or parts[0] not in CONTENT_ICON_PREFIXES:
        return None
    prefix = parts[0]
    ctx = _Context(jar_path, catalog, current_zf, budget)
    candidates = _namespace_candidates(parts, lang_namespace, catalog)
    for namespace, rest in candidates:
        if not rest:
            continue
        if prefix in {"effect", "-effect"}:
            hit = _texture_by_name(ctx, prefix, namespace, rest)
            if hit:
                return hit
        for model_namespace, model_path in _model_refs(ctx, prefix, namespace, rest):
            hit = _resolve_model(ctx, model_namespace, model_path)
            if hit:
                return hit
    for namespace, rest in candidates:
        if rest:
            hit = _texture_by_name(ctx, prefix, namespace, rest)
            if hit:
                return hit
    return None


def hit_to_uri(hit: tuple) -> str:
    """把解析結果轉成儲存在索引／entry 的 icon URI（含預覽提示片段）。"""
    texture, png_path, archive = hit[0], hit[1], hit[2]
    meta = getattr(hit, "meta", None)
    if not png_path:
        kind = (meta or {}).get("k", "")
        return f"{SYNTHETIC_SCHEME}{'spawn_egg' if kind == 'egg' else texture}"
    return IconRef(archive, png_path, encode_meta(meta)).to_uri()
