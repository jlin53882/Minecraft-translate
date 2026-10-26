"""圖示解析準確度與渲染清晰度的回歸測試（全部使用小型合成 JAR）。"""

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app import icon_render
from app.icon_catalog import ModpackAssetCatalog
from app.icon_index import _iter_entries_from_lang_files, _loads_lang_json
from app.icon_preview_image import describe_icon_uri, render_icon_uri
from app.icon_reader import IconRef
from app.icon_resolve import hit_to_uri, resolve_icon


def _png(color, size=(16, 16)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, format="PNG")
    return out.getvalue()


def _jar(path: Path, files: dict[str, bytes | str | dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            if isinstance(content, dict):
                content = json.dumps(content)
            zf.writestr(name, content)
    return path


def _resolve(mods: Path, jar: Path, ns: str, key: str):
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    try:
        with zipfile.ZipFile(jar) as zf:
            return resolve_icon(jar, ns, key, catalog, current_zf=zf, budget=None)
    finally:
        catalog.close()


def test_cube_all_block_gets_isometric_hint(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {
            "assets/m/models/block/stone.json": {
                "parent": "minecraft:block/cube_all",
                "textures": {"all": "m:block/stone"},
            },
            "assets/m/textures/block/stone.png": _png((1, 2, 3, 255)),
        },
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "block.m.stone")
    assert hit.meta["k"] == "iso"
    assert "f" not in hit.meta  # 三面相同，省略


def test_column_block_uses_end_for_top_and_side_for_faces(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {
            "assets/m/models/block/log.json": {
                "parent": "block/cube_column",
                "textures": {"end": "m:block/log_top", "side": "m:block/log"},
            },
            "assets/m/textures/block/log_top.png": _png((9, 9, 9, 255)),
            "assets/m/textures/block/log.png": _png((5, 5, 5, 255)),
        },
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "block.m.log")
    assert hit[1].endswith("block/log.png")
    assert hit.meta["f"] == {"top": "assets/m/textures/block/log_top.png"}


def test_item_generated_is_flat_and_particle_is_last(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {
            "assets/m/models/block/crate.json": {
                "textures": {"particle": "m:block/broken", "side": "m:block/crate"}
            },
            "assets/m/textures/block/broken.png": _png((1, 1, 1, 255)),
            "assets/m/textures/block/crate.png": _png((2, 2, 2, 255)),
        },
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "block.m.crate")
    assert hit[1].endswith("block/crate.png")


def test_mob_effect_texture_is_found_without_model(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {"assets/m/textures/mob_effect/glow.png": _png((1, 1, 1, 255), (18, 18))},
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "effect.m.glow")
    assert hit[1] == "assets/m/textures/mob_effect/glow.png"


def test_texture_by_name_when_model_missing(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {"assets/m/textures/item/ruby.png": _png((1, 1, 1, 255))},
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "item.m.ruby")
    assert hit[1] == "assets/m/textures/item/ruby.png"


def test_spawn_egg_template_becomes_synthetic_icon(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {
            "assets/m/models/item/bug_spawn_egg.json": {
                "parent": "item/template_spawn_egg"
            }
        },
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "item.m.bug_spawn_egg")
    assert hit_to_uri(hit) == "mct-icon://spawn_egg"
    assert render_icon_uri("mct-icon://spawn_egg")


def test_entity_key_uses_spawn_egg_item_model(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {
            "assets/m/models/item/bug_spawn_egg.json": {
                "parent": "item/template_spawn_egg"
            }
        },
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "entity.m.bug")
    assert hit_to_uri(hit) == "mct-icon://spawn_egg"


def test_missing_vanilla_texture_is_not_guessed_from_other_mods(tmp_path):
    mods = tmp_path / "mods"
    jar = _jar(
        mods / "a.jar",
        {
            "assets/a/models/block/fancy.json": {
                "parent": "block/cube_all",
                "textures": {"all": "minecraft:block/cauldron_top"},
            }
        },
    )
    _jar(
        mods / "b.jar",
        {"assets/b/textures/block/cauldron_top.png": _png((1, 1, 1, 255))},
    )
    assert _resolve(mods, jar, "a", "block.a.fancy") is None


def test_client_jar_supplies_vanilla_texture(tmp_path):
    profile = tmp_path / "pack"
    jar = _jar(
        profile / "mods" / "a.jar",
        {
            "assets/a/models/block/fancy.json": {
                "parent": "block/cube_all",
                "textures": {"all": "minecraft:block/stone"},
            }
        },
    )
    _jar(
        profile / "pack.jar",
        {"assets/minecraft/textures/block/stone.png": _png((1, 1, 1, 255))},
    )
    hit = _resolve(profile / "mods", jar, "a", "block.a.fancy")
    assert hit[1] == "assets/minecraft/textures/block/stone.png"


def test_custom_uv_model_is_flagged(tmp_path):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {
            "assets/m/models/block/plush.json": {
                "texture_size": [64, 64],
                "textures": {"0": "m:block/plush"},
                "elements": [{"from": [0, 0, 0], "to": [4, 4, 4], "faces": {}}],
            },
            "assets/m/textures/block/plush.png": _png((1, 1, 1, 255), (64, 64)),
        },
    )
    hit = _resolve(tmp_path / "mods", jar, "m", "block.m.plush")
    assert hit.meta["u"] == 1
    assert "UV" in describe_icon_uri(hit_to_uri(hit))


def test_model_json_is_read_once_per_catalog(tmp_path):
    mods = tmp_path / "mods"
    jar = _jar(
        mods / "m.jar",
        {
            "assets/m/models/block/base.json": {"textures": {"all": "m:block/x"}},
            "assets/m/models/block/a.json": {"parent": "m:block/base"},
            "assets/m/models/block/b.json": {"parent": "m:block/base"},
            "assets/m/textures/block/x.png": _png((1, 1, 1, 255)),
        },
    )
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    reads = []
    original = catalog.read
    catalog.read = lambda path, **kw: (reads.append(path), original(path, **kw))[1]
    try:
        with zipfile.ZipFile(jar) as zf:
            for key in ("block.m.a", "block.m.b", "block.m.a", "block.m.b"):
                resolve_icon(jar, "m", key, catalog, current_zf=zf, budget=None)
    finally:
        catalog.close()
    assert reads.count("assets/m/models/block/base.json") == 1


def test_uri_meta_round_trips_and_stays_backward_compatible():
    ref = IconRef(Path("/a/b.jar"), "assets/m/textures/x.png", '{"k":"iso"}')
    parsed = IconRef.parse(ref.to_uri())
    assert parsed == ref
    assert parsed.meta == {"k": "iso"}
    assert IconRef.parse("jar:///a/b.jar:assets/x.png").meta is None


def test_lang_json_with_comments_trailing_commas_and_raw_newlines(tmp_path):
    text = '﻿{"item.m.a": "x\ny", // note\n "item.m.b": "http://z", /* c */\n}'
    assert _loads_lang_json(text.lstrip("﻿")) == {
        "item.m.a": "x\ny",
        "item.m.b": "http://z",
    }
    jar = _jar(tmp_path / "m.jar", {"assets/m/lang/en_us.json": text.encode("utf-8")})
    with zipfile.ZipFile(jar) as zf:
        assert [k for _m, k, _v in _iter_entries_from_lang_files(zf)] == [
            "item.m.a",
            "item.m.b",
        ]


# ---------------- 渲染 ----------------


def _decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_animation_strip_uses_first_frame_only():
    strip = Image.new("RGBA", (16, 48), (0, 0, 255, 255))
    strip.paste((255, 0, 0, 255), (0, 0, 16, 16))
    out = io.BytesIO()
    strip.save(out, format="PNG")
    image = _decode(icon_render.render_flat(out.getvalue()))
    assert image.size == (icon_render.DISPLAY_PX,) * 2
    assert image.getpixel((64, 64)) == (255, 0, 0, 255)


def test_small_icons_scale_by_integer_factor_without_blending():
    src = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    src.putpixel((0, 0), (255, 0, 0, 255))
    out = io.BytesIO()
    src.save(out, format="PNG")
    image = _decode(icon_render.render_flat(out.getvalue()))
    assert image.getpixel((7, 7)) == (255, 0, 0, 255)
    assert image.getpixel((8, 8)) == (0, 0, 0, 0)  # 邊界銳利，沒有插值


def test_non_square_texture_is_padded_not_stretched():
    out = io.BytesIO()
    Image.new("RGBA", (16, 8), (0, 255, 0, 255)).save(out, format="PNG")
    image = _decode(icon_render.render_flat(out.getvalue()))
    assert image.getpixel((64, 64)) == (0, 255, 0, 255)
    assert image.getpixel((64, 5))[3] == 0


def test_grayscale_leaves_get_biome_tint_but_colored_do_not():
    gray = Image.new("RGBA", (16, 16), (200, 200, 200, 255))
    colored = Image.new("RGBA", (16, 16), (200, 10, 10, 255))
    assert icon_render.tint_for_name("oak_leaves.png") == "foliage"
    tinted = icon_render.apply_tint(gray, "foliage").getpixel((0, 0))
    assert tinted[1] > tinted[0] and tinted[1] > tinted[2]
    assert icon_render.apply_tint(colored, "foliage").getpixel((0, 0)) == (
        200,
        10,
        10,
        255,
    )


def test_isometric_cube_shows_three_distinct_faces():
    def solid(color):
        out = io.BytesIO()
        Image.new("RGBA", (16, 16), color).save(out, format="PNG")
        return out.getvalue()

    image = _decode(
        icon_render.render_isometric(
            solid((255, 0, 0, 255)), solid((0, 255, 0, 255)), solid((0, 0, 255, 255))
        )
    )
    top, left, right = (
        image.getpixel((64, 36)),
        image.getpixel((40, 80)),
        image.getpixel((90, 80)),
    )
    assert top[0] > top[1] and top[0] > top[2]
    assert left[1] > left[0]
    assert right[2] > right[0]
    assert image.getpixel((2, 2))[3] == 0


def test_render_icon_uri_is_cached_and_reads_zip_once(tmp_path, monkeypatch):
    from app import icon_preview_image as mod

    jar = _jar(
        tmp_path / "m.jar", {"assets/m/textures/item/a.png": _png((1, 2, 3, 255))}
    )
    uri = IconRef(jar, "assets/m/textures/item/a.png").to_uri()
    mod.clear_preview_image_cache()
    calls = []
    original = mod.read_icon_bytes
    monkeypatch.setattr(
        mod, "read_icon_bytes", lambda *a: (calls.append(a), original(*a))[1]
    )
    first = render_icon_uri(uri)
    assert render_icon_uri(uri) is first
    assert len(calls) == 1


def test_missing_icon_tooltip_explains_client_jar():
    assert "client JAR" in describe_icon_uri(None)


def test_resolved_archive_paths_are_cached(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    jar = _jar(mods / "m.jar", {"assets/m/textures/item/a.png": _png((1, 1, 1, 255))})
    catalog = ModpackAssetCatalog.from_mods_directory(mods)
    calls = []
    original = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self, *a, **k: (calls.append(1), original(self, *a, **k))[1],
    )
    for _ in range(50):
        catalog.archive_for("assets/m/textures/item/a.png", current_archive=jar)
    catalog.close()
    assert len(calls) <= 1


@pytest.mark.parametrize("key", ["item.m.tooltip", "item.m.a.tooltip"])
def test_tooltip_keys_do_not_get_icons(tmp_path, key):
    jar = _jar(
        tmp_path / "mods" / "m.jar",
        {"assets/m/textures/item/a.png": _png((1, 1, 1, 255))},
    )
    assert _resolve(tmp_path / "mods", jar, "m", key) is None


def test_row_icon_widget_is_pixel_sharp_and_placeholder_only_for_content_keys():
    from types import SimpleNamespace

    import flet as ft

    from app.views import icon_preview_row as rows

    result = SimpleNamespace(icon_path=None, reason="", risk=None)
    widget, _ = rows._build_icon_widget(None, result, "item.m.ruby")
    assert isinstance(widget, ft.Image)
    assert widget.filter_quality == ft.FilterQuality.NONE
    assert widget.width == icon_render.DISPLAY_PX
    none_widget, _ = rows._build_icon_widget(None, result, "item.m.ruby.tooltip_x")
    assert none_widget is not None  # item.* 前綴仍屬內容 key
    no_icon, _ = rows._build_icon_widget(None, result, "advancements.m.title")
    assert no_icon is None
