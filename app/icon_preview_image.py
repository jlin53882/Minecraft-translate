"""icon URI -> 預覽 PNG bytes（讀 JAR、套用 ``app.icon_render`` 的像素規則、記憶體快取）。

會做 ZIP 讀取與影像運算，必須在背景執行緒呼叫。同一 URI 的結果放在小型 LRU，
捲動／切頁回到已看過的列時不再解壓與縮放。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from app.icon_reader import IconRef, read_icon_bytes
from app.icon_render import (
    RENDER_VERSION,
    SYNTHETIC_SCHEME,
    render_flat,
    render_isometric,
    render_synthetic,
)
from translation_tool.utils.cache_registry import register_cache
from translation_tool.utils.log_unit import log_warning

_CACHE_MAX = 2048
_cache: OrderedDict[tuple[int, str], bytes] = OrderedDict()
_cache_lock = threading.Lock()


def clear_preview_image_cache() -> None:
    """清除已渲染圖示的記憶體快取。"""
    with _cache_lock:
        _cache.clear()


register_cache("icon_preview.rendered_images", clear_preview_image_cache)


def _face_source(ref: IconRef, face: object) -> tuple[Path, str]:
    """meta 中的面來源：省略 = 主貼圖；字串 = 同 archive 的另一張；[archive, png] = 跨 archive。"""
    if isinstance(face, list) and len(face) == 2:
        return Path(face[0]), str(face[1])
    if isinstance(face, str):
        return ref.jar_path, face
    return ref.jar_path, ref.png_path


def _render_isometric(ref: IconRef, meta: dict) -> bytes | None:
    faces = meta.get("f") or {}
    tints = meta.get("t") if isinstance(meta.get("t"), dict) else {}
    data = {}
    for name in ("top", "left", "right"):
        jar, png = _face_source(ref, faces.get(name) if name != "right" else None)
        raw = read_icon_bytes(jar, png)
        if raw is None:
            return None
        data[name] = raw
    return render_isometric(
        data["top"],
        data["left"],
        data["right"],
        tints=(tints.get("top"), tints.get("left"), tints.get("right")),
    )


def _render_uri(uri: str) -> bytes | None:
    if uri.startswith(SYNTHETIC_SCHEME):
        return render_synthetic(uri)
    ref = IconRef.parse(uri)
    if ref is None:
        return None
    meta = ref.meta or {}
    if meta.get("k") == "iso":
        rendered = _render_isometric(ref, meta)
        if rendered is not None:
            return rendered
    raw = read_icon_bytes(ref.jar_path, ref.png_path)
    if raw is None:
        return None
    tint = meta.get("t")
    return render_flat(raw, tint=tint if isinstance(tint, str) else None)


def render_icon_uri(uri: str) -> bytes | None:
    """回傳 URI 對應的 128x128 預覽 PNG；來源不存在或不是有效圖片時回傳 None。"""
    key = (RENDER_VERSION, uri)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    try:
        rendered = _render_uri(uri)
    except Exception as exc:  # noqa: BLE001 - 單張壞圖不應中斷整頁，改顯示佔位圖
        log_warning(f"[IconPreview] 圖示渲染失敗 {uri[:160]}: {exc!r}")
        return None
    if rendered is not None:
        with _cache_lock:
            _cache[key] = rendered
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    return rendered


def describe_icon_uri(uri: str | None) -> str:
    """懸停提示文字：貼圖／模型路徑與預覽種類。純字串運算，不做 I/O，可在 UI 執行緒呼叫。"""
    if not uri:
        return (
            "找不到圖示。\n可能的原因：沒有貼圖、貼圖引用原版資源"
            "（請把原版 client JAR 放在 mods 資料夾的同層目錄），或使用無法預覽的 3D 模型。"
        )
    text = str(uri)
    if text.startswith(SYNTHETIC_SCHEME):
        return "通用刷怪蛋圖示（此刷怪蛋沒有貼圖，顏色由模組程式碼決定）"
    ref = IconRef.parse(text)
    if ref is None:
        return text
    meta = ref.meta or {}
    lines = [f"貼圖：{ref.png_path}"]
    model = meta.get("m")
    if isinstance(model, str) and ":" in model:
        namespace, path = model.split(":", 1)
        lines.append(f"模型：assets/{namespace}/models/{path}.json")
    lines.append(f"來源：{ref.jar_path.name}")
    if meta.get("k") == "iso":
        lines.append("等角預覽（頂面、左面、右面貼圖合成）")
    if meta.get("t"):
        lines.append("灰階貼圖已套用預設群系顏色")
    if meta.get("u"):
        lines.append("此貼圖是 3D 模型的 UV 展開圖，僅供參考")
    return "\n".join(lines)
