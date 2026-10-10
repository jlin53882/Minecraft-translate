"""設計系統：Deepslate & Emerald（深板岩底色、祖母綠主色）。

這個模組是新 UI 的「顏色與主題」唯一來源，對應 ``docs/design/ui-redesign/`` 的設計稿。

核心做法
- 設計稿的 token（bg / side / panel / panel2 / raised / line / 強調色…）對應到 Flet 的
  ``ft.ColorScheme`` 欄位，再用 ``page.theme`` / ``page.dark_theme`` 交給 Flutter 處理。
  所以**切換深淺色不需要重建任何畫面**，控制項只要用語意色名稱（``C.PANEL`` 等）即可。
- 設計稿有五種強調色（祖母綠 / 金 / 鑽石藍 / 附魔紫 / 紅石紅），``ColorScheme`` 只有三組色系 + error，
  所以金色借用 ``tertiary_fixed`` 這組欄位（它不隨模式自動衍生，由本模組明確指定）。
  使用端一律透過 ``tone("gold")`` 取得，不要直接寫 ``ft.Colors.TERTIARY_FIXED``。

使用方式
    from app.ui import design

    design.apply(page)                      # 設定 page.theme / dark_theme / theme_mode / bgcolor
    ft.Container(bgcolor=design.C.PANEL, border=ft.Border.all(1, design.C.LINE))
    t = design.tone("gold")                 # t.fg / t.bg / t.line
"""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

# ---------------------------------------------------------------------------
# 色票
# ---------------------------------------------------------------------------

FONT_SANS = "Noto Sans TC"
FONT_MONO = "JetBrains Mono"

# Minecraft 的實際色碼預覽固定使用深色畫布，讓 §e 等亮色在淺色主題也清楚。
MC_PREVIEW_BG = "#20252B"
MC_PREVIEW_LINE = "#3A424B"
MC_PREVIEW_TEXT = "#F1F5F9"
MC_PREVIEW_HINT = "#CBD5E1"
MC_BOOK_PREVIEW_BG = "#F3E9CE"
MC_BOOK_PREVIEW_LINE = "#C9B98C"
MC_BOOK_PREVIEW_TEXT = "#2D2619"


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def blend(fg: str, bg: str, alpha: float) -> str:
    """把 fg 以 alpha 疊在 bg 上，回傳不透明的 ``#RRGGBB``（容器色不用半透明，主題切換才穩定）。"""
    fr, fg_, fb = _rgb(fg)
    br, bg_, bb = _rgb(bg)
    mix = [
        round(f * alpha + b * (1 - alpha)) for f, b in ((fr, br), (fg_, bg_), (fb, bb))
    ]
    return "#{:02X}{:02X}{:02X}".format(*mix)


@dataclass(frozen=True)
class Palette:
    """單一模式的設計 token（與設計稿 CSS 變數同名）。"""

    name: str
    # 表面層級：bg < side < panel < panel2 < raised < hover
    bg: str
    side: str
    panel: str
    panel2: str
    raised: str
    hover: str
    line: str
    line2: str
    # 文字
    text: str
    muted: str
    dim: str
    track: str
    logbg: str
    # 強調色
    em: str  # 祖母綠：主色 / 成功
    em2: str  # 主色按鈕漸層的深端
    onem: str  # 主色上的文字
    gold: str  # 金：進行中 / 警告
    dia: str  # 鑽石藍：資訊
    ench: str  # 附魔紫：進階 / 正則
    red: str  # 紅石紅：錯誤 / 危險

    # 強調色的淡底（以強調色疊在 panel 上）
    @property
    def em_bg(self) -> str:
        return blend(self.em, self.panel, 0.13)

    @property
    def gold_bg(self) -> str:
        return blend(self.gold, self.panel, 0.13)

    @property
    def dia_bg(self) -> str:
        return blend(self.dia, self.panel, 0.13)

    @property
    def ench_bg(self) -> str:
        return blend(self.ench, self.panel, 0.14)

    @property
    def red_bg(self) -> str:
        return blend(self.red, self.panel, 0.13)


DARK = Palette(
    name="dark",
    bg="#0A0E13",
    side="#0E131A",
    panel="#131A22",
    panel2="#18212B",
    raised="#1E2834",
    hover="#263243",
    line="#212C39",
    line2="#2C3A4B",
    text="#E8EEF5",
    muted="#94A3B6",
    dim="#6E7F96",
    track="#1F2A37",
    logbg="#0B1016",
    em="#34D399",
    em2="#10B981",
    onem="#04231A",
    gold="#FBBF24",
    dia="#38BDF8",
    ench="#A78BFA",
    red="#F87171",
)

LIGHT = Palette(
    name="light",
    bg="#F2F5F8",
    side="#FFFFFF",
    panel="#FFFFFF",
    panel2="#F7F9FB",
    raised="#EDF1F5",
    hover="#E2E8EE",
    line="#E2E8EE",
    line2="#CFD8E2",
    text="#121A23",
    muted="#556577",
    dim="#6B7A8C",
    track="#E6ECF2",
    logbg="#F5F8FB",
    em="#097752",
    em2="#047857",
    onem="#FFFFFF",
    gold="#8D5E00",
    dia="#0369A1",
    ench="#6D4FD6",
    red="#C81E1E",
)

PALETTES = {"dark": DARK, "light": LIGHT}


def palette(mode: str) -> Palette:
    """依模式名稱取得色票；未知名稱回傳深色。"""
    return PALETTES.get(str(mode).lower(), DARK)


# ---------------------------------------------------------------------------
# ColorScheme / Theme
# ---------------------------------------------------------------------------


def color_scheme(p: Palette) -> ft.ColorScheme:
    """設計 token → ``ft.ColorScheme``。"""
    return ft.ColorScheme(
        primary=p.em,
        on_primary=p.onem,
        primary_container=p.em_bg,
        on_primary_container=p.em,
        secondary=p.dia,
        on_secondary=p.onem,
        secondary_container=p.dia_bg,
        on_secondary_container=p.dia,
        tertiary=p.ench,
        on_tertiary=p.onem,
        tertiary_container=p.ench_bg,
        on_tertiary_container=p.ench,
        error=p.red,
        on_error=p.onem,
        error_container=p.red_bg,
        on_error_container=p.red,
        surface=p.bg,
        on_surface=p.text,
        on_surface_variant=p.muted,
        outline=p.line2,
        outline_variant=p.line,
        shadow="#000000",
        scrim="#000000",
        inverse_surface=p.text,
        on_inverse_surface=p.bg,
        inverse_primary=p.em2,
        surface_tint=p.panel,  # 抵消 M3 的高度色調，維持設計稿的平面層級
        surface_dim=p.logbg,
        surface_bright=p.raised,
        surface_container_lowest=p.side,
        surface_container_low=p.panel,
        surface_container=p.panel2,
        surface_container_high=p.raised,
        surface_container_highest=p.hover,
        # 金色：借用 tertiary_fixed 這組（見模組說明）
        primary_fixed=p.em2,
        tertiary_fixed=p.gold,
        tertiary_fixed_dim=p.gold_bg,
        on_tertiary_fixed=p.gold,
    )


# 元件圓角（設計稿：卡片 16、輸入 / 按鈕 10、膠囊圓）
RADIUS_CARD = 16
RADIUS_CONTROL = 10
RADIUS_DIALOG = 20


def _button_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        shape=ft.RoundedRectangleBorder(radius=RADIUS_CONTROL),
        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
    )


def build_theme(mode: str = "dark") -> ft.Theme:
    """建立指定模式的 ``ft.Theme``（色票 + 元件預設樣式）。"""
    p = palette(mode)
    return ft.Theme(
        font_family=FONT_SANS,
        use_material3=True,
        visual_density=ft.VisualDensity.COMFORTABLE,
        color_scheme=color_scheme(p),
        card_theme=ft.CardTheme(
            color=p.panel,
            elevation=0,
            margin=0,
            shape=ft.RoundedRectangleBorder(radius=RADIUS_CARD),
        ),
        divider_theme=ft.DividerTheme(color=p.line, thickness=1, space=1),
        progress_indicator_theme=ft.ProgressIndicatorTheme(
            color=p.em,
            linear_track_color=p.track,
            circular_track_color=p.track,
        ),
        scrollbar_theme=ft.ScrollbarTheme(
            thickness=6,
            radius=3,
            thumb_color=p.line2,
            main_axis_margin=2,
            thumb_visibility=False,  # 只在捲動 / 滑過時出現，不蓋住卡片右緣
        ),
        dialog_theme=ft.DialogTheme(
            bgcolor=p.panel,
            elevation=0,
            shape=ft.RoundedRectangleBorder(radius=RADIUS_DIALOG),
        ),
        snackbar_theme=ft.SnackBarTheme(
            bgcolor=p.raised,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=12),
        ),
        filled_button_theme=ft.FilledButtonTheme(style=_button_style()),
        button_theme=ft.ButtonTheme(style=_button_style()),
        outlined_button_theme=ft.OutlinedButtonTheme(style=_button_style()),
        text_button_theme=ft.TextButtonTheme(style=_button_style()),
    )


# ---------------------------------------------------------------------------
# 語意色名稱：給所有控制項使用（這些字串由 Flutter 依目前主題解析）
# ---------------------------------------------------------------------------


class C:
    """語意色名稱。用在 ``bgcolor`` / ``color`` / ``border`` 等屬性，會跟著主題自動切換。"""

    # 表面
    BG = ft.Colors.SURFACE
    SIDE = ft.Colors.SURFACE_CONTAINER_LOWEST
    PANEL = ft.Colors.SURFACE_CONTAINER_LOW
    PANEL2 = ft.Colors.SURFACE_CONTAINER
    RAISED = ft.Colors.SURFACE_CONTAINER_HIGH
    HOVER = ft.Colors.SURFACE_CONTAINER_HIGHEST
    LOG_BG = ft.Colors.SURFACE_DIM
    TRACK = ft.Colors.SURFACE_CONTAINER_HIGHEST
    # 線條
    LINE = ft.Colors.OUTLINE_VARIANT
    LINE2 = ft.Colors.OUTLINE
    # 文字
    TEXT = ft.Colors.ON_SURFACE
    MUTED = ft.Colors.ON_SURFACE_VARIANT
    DIM = ft.Colors.with_opacity(0.72, ft.Colors.ON_SURFACE_VARIANT)
    # 主色（祖母綠）
    EM = ft.Colors.PRIMARY
    EM_DARK = ft.Colors.PRIMARY_FIXED
    ON_EM = ft.Colors.ON_PRIMARY
    EM_BG = ft.Colors.PRIMARY_CONTAINER
    EM_LINE = ft.Colors.with_opacity(0.4, ft.Colors.PRIMARY)
    # 強調色
    GOLD = ft.Colors.TERTIARY_FIXED
    GOLD_BG = ft.Colors.TERTIARY_FIXED_DIM
    DIA = ft.Colors.SECONDARY
    DIA_BG = ft.Colors.SECONDARY_CONTAINER
    ENCH = ft.Colors.TERTIARY
    ENCH_BG = ft.Colors.TERTIARY_CONTAINER
    RED = ft.Colors.ERROR
    RED_BG = ft.Colors.ERROR_CONTAINER


@dataclass(frozen=True)
class Tone:
    """一組強調色：文字 / 圖示用 ``fg``、淡底 ``bg``、邊框 ``line``。"""

    name: str
    fg: str
    bg: str
    line: str


def _tone(name: str, fg: str, bg: str) -> Tone:
    return Tone(name=name, fg=fg, bg=bg, line=ft.Colors.with_opacity(0.4, fg))


TONES: dict[str, Tone] = {
    "em": _tone("em", C.EM, C.EM_BG),
    "gold": _tone("gold", C.GOLD, C.GOLD_BG),
    "dia": _tone("dia", C.DIA, C.DIA_BG),
    "ench": _tone("ench", C.ENCH, C.ENCH_BG),
    "red": _tone("red", C.RED, C.RED_BG),
    "neutral": Tone("neutral", C.MUTED, C.RAISED, C.LINE),
}

# 狀態字串 → 強調色（元件與頁面共用，避免每頁各自對應）
STATUS_TONE = {
    "ok": "em",
    "done": "em",
    "success": "em",
    "run": "gold",
    "running": "gold",
    "warn": "gold",
    "warning": "gold",
    "info": "dia",
    "error": "red",
    "fail": "red",
    "failed": "red",
    "todo": "neutral",
    "idle": "neutral",
}


def tone(name: str | None) -> Tone:
    """取得強調色組；名稱或狀態不認得時回傳中性色。"""
    key = str(name or "neutral").lower()
    return TONES.get(key) or TONES[STATUS_TONE.get(key, "neutral")]


# ---------------------------------------------------------------------------
# 套用到 Page
# ---------------------------------------------------------------------------

THEME_MODES = {"dark": ft.ThemeMode.DARK, "light": ft.ThemeMode.LIGHT}


def apply(page: ft.Page, mode: str = "dark") -> None:
    """把設計系統套到 Page：兩組主題 + 預設模式 + 背景色。"""
    page.theme = build_theme("light")
    page.dark_theme = build_theme("dark")
    page.theme_mode = THEME_MODES.get(str(mode).lower(), ft.ThemeMode.DARK)
    page.bgcolor = C.BG


def mode_of(page: ft.Page) -> str:
    """目前頁面的模式名稱（"dark" / "light"）；SYSTEM 視為 light（桌面預設）。"""
    return "dark" if page.theme_mode == ft.ThemeMode.DARK else "light"


def toggled_mode(page: ft.Page) -> str:
    """切換後的模式名稱。"""
    return "light" if mode_of(page) == "dark" else "dark"
