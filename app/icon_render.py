"""圖示像素處理：把 JAR 內的貼圖轉成清晰、尺寸一致的預覽 PNG。

單一責任：只做影像運算（PIL），不碰 UI、不決定「哪張貼圖屬於哪個物品」（見 ``app.icon_resolve``）。

規則：
- 動畫貼圖（高度是寬度整數倍的長條）只取第一格。
- 非正方形貼圖置中補透明邊，不拉伸。
- 一律用最近鄰（nearest-neighbor）放大成整數倍，像素邊緣維持銳利；過大的圖才縮小。
- 灰階貼圖（草、葉、水、藤蔓等需要生物群系染色者）套用預設群系顏色。
- 方塊的三面貼圖合成等角（isometric）預覽。
- 沒有貼圖的刷怪蛋、找不到圖示時的佔位圖由程式繪製。

此模組的函式會做 CPU 運算，必須在背景執行緒呼叫（不得在 UI 執行緒）。
"""

from __future__ import annotations

import io
from collections.abc import Callable

from PIL import Image, ImageDraw

DISPLAY_PX = 128
"""輸出 PNG 的邊長（像素）；UI 以 1:1 顯示，避免任何縮放模糊。"""

RENDER_VERSION = 2
"""渲染規則版本；改動輸出外觀時遞增，讓磁碟快取自動失效。"""

SYNTHETIC_SCHEME = "mct-icon://"
"""沒有對應貼圖、由程式繪製的圖示 URI 前綴（例如 ``mct-icon://spawn_egg``）。"""

# 原版預設群系染色（Plains 附近的物品欄顏色）
_TINTS: dict[str, tuple[int, int, int]] = {
    "grass": (0x79, 0xC0, 0x5A),
    "foliage": (0x48, 0xB5, 0x18),
    "spruce": (0x61, 0x99, 0x61),
    "birch": (0x80, 0xA7, 0x55),
    "water": (0x3F, 0x76, 0xE4),
}


def tint_for_name(name: str) -> str | None:
    """依貼圖／模型名稱猜測需要的染色種類；不需要染色回傳 None。"""
    lowered = name.casefold()
    if "leaves" in lowered or "foliage" in lowered:
        if "spruce" in lowered:
            return "spruce"
        if "birch" in lowered:
            return "birch"
        return "foliage"
    if "water" in lowered:
        return "water"
    if any(token in lowered for token in ("grass", "fern", "vine", "lily")):
        return "grass"
    return None


def first_frame(image: Image.Image) -> Image.Image:
    """動畫長條貼圖取第一格；一般貼圖原樣回傳。"""
    width, height = image.size
    if width > 0 and height > width and height % width == 0:
        return image.crop((0, 0, width, width))
    return image


def _is_grayscale(image: Image.Image) -> bool:
    data = image.convert("RGBA").tobytes()
    seen = 0
    for i in range(0, len(data), 4):
        if data[i + 3] < 16:
            continue
        seen += 1
        r, g, b = data[i], data[i + 1], data[i + 2]
        if abs(r - g) > 8 or abs(g - b) > 8:
            return False
    return seen > 0


def apply_tint(image: Image.Image, tint: str | None) -> Image.Image:
    """對灰階貼圖乘上預設群系顏色；已有顏色的貼圖不處理（避免重複染色）。"""
    color = _TINTS.get(tint or "")
    if color is None:
        return image
    rgba = image.convert("RGBA")
    if not _is_grayscale(rgba):
        return rgba
    r, g, b, a = rgba.split()
    r = r.point(lambda v: v * color[0] // 255)
    g = g.point(lambda v: v * color[1] // 255)
    b = b.point(lambda v: v * color[2] // 255)
    return Image.merge("RGBA", (r, g, b, a))


def _to_canvas(image: Image.Image, size: int = DISPLAY_PX) -> Image.Image:
    """整數倍最近鄰放大並置中到 size x size 透明畫布；超過 size 則平滑縮小。"""
    image = image.convert("RGBA")
    width, height = image.size
    longest = max(width, height)
    if longest > size:
        scale = size / longest
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.LANCZOS,
        )
    else:
        factor = max(1, size // longest)
        image = image.resize((width * factor, height * factor), Image.NEAREST)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def _encode(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()


def render_flat(png: bytes, *, tint: str | None = None) -> bytes:
    """渲染一般物品／方塊貼圖：取首格、補邊、染色、整數倍最近鄰放大。"""
    with Image.open(io.BytesIO(png)) as source:
        image = first_frame(source.convert("RGBA"))
    return _encode(_to_canvas(apply_tint(image, tint)))


# 等角視圖：頂點座標（128x128 畫布）。可見面為上（up）、左（east）、右（north）。
_TOP = ((64, 8), (112, 36), (64, 64), (16, 36))  # SW, NW, NE, SE
_LEFT = ((16, 36), (64, 64), (64, 120), (16, 92))
_RIGHT = ((64, 64), (112, 36), (112, 92), (64, 120))
_SHADE = {"top": 1.0, "left": 0.82, "right": 0.66}


def _affine_face(
    texture: Image.Image,
    origin: tuple[float, float],
    u_vec: tuple[float, float],
    v_vec: tuple[float, float],
) -> Image.Image:
    """把 texture 以 origin + u*u_vec + v*v_vec 貼到 128x128 畫布（最近鄰取樣）。"""
    width, height = texture.size
    # 輸出座標 p = origin + (u/width)*U + (v/height)*V；解反矩陣得到貼圖座標
    ux, uy = u_vec[0] / width, u_vec[1] / width
    vx, vy = v_vec[0] / height, v_vec[1] / height
    det = ux * vy - uy * vx
    a, b = vy / det, -vx / det
    d, e = -uy / det, ux / det
    c = -(a * origin[0] + b * origin[1])
    f = -(d * origin[0] + e * origin[1])
    return texture.transform(
        (DISPLAY_PX, DISPLAY_PX), Image.AFFINE, (a, b, c, d, e, f), Image.NEAREST
    )


def _shade(image: Image.Image, factor: float) -> Image.Image:
    if factor >= 1.0:
        return image
    r, g, b, a = image.split()
    scale = lambda v: int(v * factor)
    return Image.merge("RGBA", (r.point(scale), g.point(scale), b.point(scale), a))


def render_isometric(
    top: bytes,
    left: bytes,
    right: bytes,
    *,
    tints: tuple[str | None, str | None, str | None] = (None, None, None),
) -> bytes:
    """以頂面／左面（東）／右面（北）貼圖合成等角方塊預覽。"""
    faces = []
    for data, tint in zip((top, left, right), tints, strict=True):
        with Image.open(io.BytesIO(data)) as source:
            faces.append(apply_tint(first_frame(source.convert("RGBA")), tint))
    canvas = Image.new("RGBA", (DISPLAY_PX, DISPLAY_PX), (0, 0, 0, 0))
    # up 面：u 向東、v 向南；NW 角為原點
    placements = (
        (faces[0], _TOP, (112, 36), (-48, 28), (-48, -28), "top"),
        (faces[1], _LEFT, (16, 36), (48, 28), (0, 56), "left"),
        (faces[2], _RIGHT, (64, 64), (48, -28), (0, 56), "right"),
    )
    for texture, polygon, origin, u_vec, v_vec, name in placements:
        layer = _affine_face(texture, origin, u_vec, v_vec)
        mask = Image.new("L", (DISPLAY_PX, DISPLAY_PX), 0)
        ImageDraw.Draw(mask).polygon(polygon, fill=255)
        alpha = Image.composite(
            layer.getchannel("A"), Image.new("L", mask.size, 0), mask
        )
        layer.putalpha(alpha)
        canvas.alpha_composite(_shade(layer, _SHADE[name]))
    return _encode(canvas)


def render_spawn_egg() -> bytes:
    """沒有貼圖的刷怪蛋：繪製通用蛋形圖示（實際顏色由模組程式碼決定，無從得知）。"""
    unit = DISPLAY_PX // 16
    egg = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(egg)
    rows = {
        2: (7, 8), 3: (6, 9), 4: (5, 10), 5: (4, 11), 6: (4, 11), 7: (3, 12),
        8: (3, 12), 9: (3, 12), 10: (3, 12), 11: (4, 11), 12: (4, 11),
        13: (5, 10), 14: (6, 9),
    }  # fmt: skip
    for y, (x0, x1) in rows.items():
        draw.line([(x0, y), (x1, y)], fill=(0xC9, 0xCF, 0xD6, 255))
    for x, y in ((6, 6), (9, 5), (5, 9), (8, 9), (10, 11), (7, 12)):
        draw.point((x, y), fill=(0x4B, 0x52, 0x5C, 255))
    for y, (x0, _x1) in rows.items():
        draw.point((x0, y), fill=(0x2B, 0x30, 0x38, 255))
        draw.point((_x1, y), fill=(0x2B, 0x30, 0x38, 255))
    draw.line([(7, 1), (8, 1)], fill=(0x2B, 0x30, 0x38, 255))
    draw.line([(7, 15), (8, 15)], fill=(0x2B, 0x30, 0x38, 255))
    return _encode(egg.resize((16 * unit, 16 * unit), Image.NEAREST))


SYNTHETIC_RENDERERS: dict[str, Callable[[], bytes]] = {
    "spawn_egg": render_spawn_egg,
}


def render_synthetic(uri: str) -> bytes | None:
    """``mct-icon://<name>`` 的程式繪製圖示；未知名稱回傳 None。"""
    if not uri.startswith(SYNTHETIC_SCHEME):
        return None
    renderer = SYNTHETIC_RENDERERS.get(uri[len(SYNTHETIC_SCHEME) :])
    return renderer() if renderer else None


def render_placeholder() -> bytes:
    """找不到圖示時的一致佔位圖：虛線方框加問號風格的像素圖樣。"""
    size = DISPLAY_PX
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (0x6B, 0x72, 0x80, 255)
    step = 8
    for pos in range(8, size - 8, step * 2):
        draw.line([(pos, 8), (pos + step - 1, 8)], fill=color, width=2)
        draw.line([(pos, size - 9), (pos + step - 1, size - 9)], fill=color, width=2)
        draw.line([(8, pos), (8, pos + step - 1)], fill=color, width=2)
        draw.line([(size - 9, pos), (size - 9, pos + step - 1)], fill=color, width=2)
    glyph = (
        "..XXXX..",
        ".XX..XX.",
        ".....XX.",
        "....XX..",
        "...XX...",
        "........",
        "...XX...",
        "...XX...",
    )
    unit = 8
    ox = (size - 8 * unit) // 2
    for gy, line in enumerate(glyph):
        for gx, ch in enumerate(line):
            if ch == "X":
                x, y = ox + gx * unit, ox + gy * unit
                draw.rectangle([x, y, x + unit - 1, y + unit - 1], fill=color)
    return _encode(image)
