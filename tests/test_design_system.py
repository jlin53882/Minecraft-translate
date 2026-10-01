"""設計系統（app/ui/design.py）：色票對比度、ColorScheme 對應、主題與語意色。"""

from __future__ import annotations

from itertools import pairwise
from types import SimpleNamespace

import flet as ft
import pytest

from app.ui import design
from app.ui.design import (
    DARK,
    LIGHT,
    C,
    Palette,
    blend,
    build_theme,
    color_scheme,
    tone,
)


def _lum(hex_color: str) -> float:
    r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


PALETTES = [DARK, LIGHT]
ACCENTS = ["em", "gold", "dia", "ench", "red"]


# ---------------------------------------------------------------------------
# 色票
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
def test_all_tokens_are_valid_hex(p: Palette):
    for name in [
        "bg",
        "side",
        "panel",
        "panel2",
        "raised",
        "hover",
        "line",
        "line2",
        "text",
        "muted",
        "dim",
        "track",
        "logbg",
        "em",
        "em2",
        "onem",
        "gold",
        "dia",
        "ench",
        "red",
    ]:
        value = getattr(p, name)
        assert len(value) == 7 and value.startswith("#"), (p.name, name, value)
        int(value[1:], 16)


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
def test_text_contrast_meets_wcag(p: Palette):
    for surface in (p.bg, p.side, p.panel, p.panel2, p.raised):
        assert contrast(p.text, surface) >= 7, (p.name, surface)  # AAA
        assert contrast(p.muted, surface) >= 4.5, (p.name, surface)  # AA


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
def test_dim_text_is_readable_on_panels(p: Palette):
    """dim 用在提示與次要說明：至少 AA large / UI（3:1）以上。"""
    assert contrast(p.dim, p.panel) >= 4.0
    assert contrast(p.dim, p.bg) >= 3.5


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
@pytest.mark.parametrize("accent", ACCENTS)
def test_accent_text_contrast(p: Palette, accent: str):
    fg = getattr(p, accent)
    own_bg = getattr(p, f"{accent}_bg")
    assert contrast(fg, p.panel) >= 4.5, "強調色文字 / 圖示在卡片上"
    assert contrast(fg, p.bg) >= 4.5, "強調色在頁面底色上"
    assert contrast(fg, own_bg) >= 4.5, "chip：強調色文字放在自己的淡底上"


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
def test_text_on_filled_accents(p: Palette):
    """主按鈕等實心強調色上的文字（onem）。"""
    for accent in ACCENTS:
        assert contrast(p.onem, getattr(p, accent)) >= 4.5, (p.name, accent)


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
def test_surface_layers_are_distinguishable(p: Palette):
    layers = [p.bg, p.side, p.panel, p.panel2, p.raised, p.hover]
    for a, b in pairwise(layers):
        if a != b:  # 淺色模式 side 與 panel 都是白色，是設計上的選擇
            assert abs(_lum(a) - _lum(b)) > 0.0005


def test_dark_surfaces_get_lighter_with_elevation():
    layers = [DARK.bg, DARK.side, DARK.panel, DARK.panel2, DARK.raised, DARK.hover]
    assert [_lum(x) for x in layers] == sorted(_lum(x) for x in layers)


def test_blend():
    assert blend("#FFFFFF", "#000000", 0.5) == "#808080"
    assert blend("#34D399", "#131A22", 0) == "#131A22"
    assert blend("#34D399", "#131A22", 1) == "#34D399"


def test_palette_lookup_defaults_to_dark():
    assert design.palette("light") is LIGHT
    assert design.palette("LIGHT") is LIGHT
    assert design.palette("dark") is DARK
    assert design.palette("nonsense") is DARK


# ---------------------------------------------------------------------------
# ColorScheme / Theme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("p", PALETTES, ids=lambda p: p.name)
def test_color_scheme_maps_the_design_tokens(p: Palette):
    cs = color_scheme(p)

    assert cs.primary == p.em and cs.on_primary == p.onem
    assert cs.secondary == p.dia and cs.tertiary == p.ench and cs.error == p.red
    assert cs.surface == p.bg
    assert cs.surface_container_lowest == p.side
    assert cs.surface_container_low == p.panel
    assert cs.surface_container == p.panel2
    assert cs.surface_container_high == p.raised
    assert cs.surface_container_highest == p.hover
    assert cs.outline_variant == p.line and cs.outline == p.line2
    assert cs.on_surface == p.text and cs.on_surface_variant == p.muted
    assert cs.tertiary_fixed == p.gold  # 金色借用 tertiary_fixed
    assert cs.tertiary_fixed_dim == p.gold_bg


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_build_theme(mode):
    theme = build_theme(mode)

    assert isinstance(theme, ft.Theme)
    assert theme.use_material3 is True
    assert theme.font_family == design.FONT_SANS
    assert theme.color_scheme.primary == design.palette(mode).em
    assert theme.card_theme.elevation == 0
    assert theme.dialog_theme.shape.radius == design.RADIUS_DIALOG
    assert theme.progress_indicator_theme.color == design.palette(mode).em


def test_dark_and_light_themes_differ():
    assert (
        build_theme("dark").color_scheme.surface
        != build_theme("light").color_scheme.surface
    )


# ---------------------------------------------------------------------------
# 語意色與強調色組
# ---------------------------------------------------------------------------


def test_semantic_names_are_flet_theme_colors():
    """語意色是 Flet 主題色名稱（由 Flutter 依主題解析），不是寫死的 hex。"""
    for name in (
        "BG",
        "SIDE",
        "PANEL",
        "PANEL2",
        "RAISED",
        "LINE",
        "TEXT",
        "MUTED",
        "EM",
        "RED",
    ):
        value = getattr(C, name)
        assert isinstance(value, str) and not value.startswith("#"), name


def test_every_semantic_name_builds_a_valid_control():
    for name, value in vars(C).items():
        if name.startswith("_"):
            continue
        ft.Container(bgcolor=value, border=ft.Border.all(1, value))  # 不應拋例外


@pytest.mark.parametrize("name", ["em", "gold", "dia", "ench", "red", "neutral"])
def test_tone_has_fg_bg_line(name):
    t = tone(name)

    assert t.name == name
    assert t.fg and t.bg and t.line


def test_tone_resolves_status_words_and_unknowns():
    assert tone("done").name == "em"
    assert tone("running").name == "gold"
    assert tone("warning").name == "gold"
    assert tone("failed").name == "red"
    assert tone("info").name == "dia"
    assert tone("RED").name == "red"
    assert tone(None).name == "neutral"
    assert tone("whatever").name == "neutral"


def test_every_status_word_maps_to_a_known_tone():
    for word, name in design.STATUS_TONE.items():
        assert name in design.TONES, word


# ---------------------------------------------------------------------------
# 套用到 Page
# ---------------------------------------------------------------------------


def test_apply_sets_both_themes_and_defaults_to_dark():
    page = SimpleNamespace()

    design.apply(page)

    assert page.theme_mode == ft.ThemeMode.DARK
    assert page.theme.color_scheme.surface == LIGHT.bg
    assert page.dark_theme.color_scheme.surface == DARK.bg
    assert page.bgcolor == C.BG


def test_apply_light_mode_and_toggle_helpers():
    page = SimpleNamespace()
    design.apply(page, "light")

    assert page.theme_mode == ft.ThemeMode.LIGHT
    assert design.mode_of(page) == "light"
    assert design.toggled_mode(page) == "dark"
    page.theme_mode = ft.ThemeMode.DARK
    assert design.toggled_mode(page) == "light"
