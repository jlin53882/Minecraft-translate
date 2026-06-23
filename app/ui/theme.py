"""主題系統。

提供統一的主題顏色管理，支援 light/dark mode。

使用方式：
    from app.ui import theme

    # 語意顏色（推薦）
    color = theme.PRIMARY
    color = theme.SUCCESS

    # 主題管理者（動態模式切換）
    theme.manager.set_mode('dark')
    color = theme.manager.get('primary')

    # 向後兼容：所有 raw colors 仍可使用
    color = theme.RED_600
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from flet import Colors


class ThemeMode(Enum):
    LIGHT = "light"
    DARK = "dark"


@dataclass
class ColorTokens:
    """單一模式的顏色 token 集合。"""

    # 語意主題色
    primary: str
    secondary: str
    success: str
    error: str
    warning: str
    info: str

    # 背景色
    bg_light: str
    bg_dark: str

    # 文字顏色
    text_primary: str
    text_secondary: str
    text_disabled: str
    text_on_primary: str

    # 表面色
    surface: str
    surface_variant: str
    outline: str
    outline_variant: str

    # 按鈕樣式
    button_height: int
    button_radius: int


# Light Mode Tokens
LIGHT_TOKENS = ColorTokens(
    primary=Colors.BLUE_700,
    secondary=Colors.BLUE_GREY_700,
    success=Colors.GREEN_700,
    error=Colors.RED_700,
    warning=Colors.ORANGE_700,
    info=Colors.BLUE_700,
    bg_light=Colors.WHITE,
    bg_dark=Colors.GREY_900,
    text_primary=Colors.BLACK,
    text_secondary=Colors.GREY,
    text_disabled=Colors.GREY_400,
    text_on_primary=Colors.WHITE,
    surface=Colors.WHITE,
    surface_variant=Colors.GREY_100,
    outline=Colors.OUTLINE,
    outline_variant=Colors.OUTLINE_VARIANT,
    button_height=42,
    button_radius=6,
)

# Dark Mode Tokens
DARK_TOKENS = ColorTokens(
    primary=Colors.BLUE_500,
    secondary=Colors.BLUE_GREY_500,
    success=Colors.GREEN_500,
    error=Colors.RED_500,
    warning=Colors.ORANGE_500,
    info=Colors.BLUE_500,
    bg_light=Colors.GREY_900,
    bg_dark=Colors.GREY_900,
    text_primary=Colors.WHITE,
    text_secondary=Colors.GREY_300,
    text_disabled=Colors.GREY_600,
    text_on_primary=Colors.BLACK,
    surface=Colors.GREY_900,
    surface_variant=Colors.GREY_800,
    outline=Colors.GREY_600,
    outline_variant=Colors.GREY_700,
    button_height=42,
    button_radius=6,
)


class ThemeManager:
    """主題管理者，支援 light/dark mode 動態切換。"""

    _instance: Optional[ThemeManager] = None

    def __init__(self):
        self._mode = ThemeMode.LIGHT
        self._tokens = LIGHT_TOKENS

    @classmethod
    def get_instance(cls) -> ThemeManager:
        """取得單例實例。"""
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def mode(self) -> ThemeMode:
        """取得目前模式。"""
        return self._mode

    @property
    def tokens(self) -> ColorTokens:
        """取得目前顏色 tokens。"""
        return self._tokens

    def set_mode(self, mode: str) -> None:
        """設定主題模式。

        Args:
            mode: 'light' 或 'dark'
        """
        if mode == "dark":
            self._mode = ThemeMode.DARK
            self._tokens = DARK_TOKENS
        else:
            self._mode = ThemeMode.LIGHT
            self._tokens = LIGHT_TOKENS

    def get(self, token_name: str) -> str:
        """取得指定 token 的顏色值。

        Args:
            token_name: token 名稱（如 'primary', 'error', 'bg_light'）

        Returns:
            顏色字串
        """
        token_map = {
            'primary': self._tokens.primary,
            'secondary': self._tokens.secondary,
            'success': self._tokens.success,
            'error': self._tokens.error,
            'warning': self._tokens.warning,
            'info': self._tokens.info,
            'bg_light': self._tokens.bg_light,
            'bg_dark': self._tokens.bg_dark,
            'text_primary': self._tokens.text_primary,
            'text_secondary': self._tokens.text_secondary,
            'text_disabled': self._tokens.text_disabled,
            'text_on_primary': self._tokens.text_on_primary,
            'surface': self._tokens.surface,
            'surface_variant': self._tokens.surface_variant,
            'outline': self._tokens.outline,
            'outline_variant': self._tokens.outline_variant,
        }
        return token_map.get(token_name, self._tokens.primary)


# 全域單例
manager = ThemeManager.get_instance()


# ============================================================
# 語意顏色（主要介面）
# ============================================================

PRIMARY = LIGHT_TOKENS.primary
SECONDARY = LIGHT_TOKENS.secondary
SUCCESS = LIGHT_TOKENS.success
ERROR = LIGHT_TOKENS.error
WARNING = LIGHT_TOKENS.warning
INFO = LIGHT_TOKENS.info

# 背景色
BG_LIGHT = LIGHT_TOKENS.bg_light
BG_DARK = LIGHT_TOKENS.bg_dark

# 文字顏色
TEXT_PRIMARY = LIGHT_TOKENS.text_primary
TEXT_SECONDARY = LIGHT_TOKENS.text_secondary
TEXT_SECONDARY_200 = Colors.GREY_200
TEXT_DISABLED = LIGHT_TOKENS.text_disabled

# 按鈕樣式
BUTTON_HEIGHT = LIGHT_TOKENS.button_height
BUTTON_RADIUS = LIGHT_TOKENS.button_radius


# ============================================================
# Raw Colors（向後兼容，保持所有現有顏色）
# ============================================================

# Red
RED = Colors.RED
RED_50 = Colors.RED_50
RED_100 = Colors.RED_100
RED_200 = Colors.RED_200
RED_300 = Colors.RED_300
RED_400 = Colors.RED_400
RED_500 = Colors.RED_500
RED_600 = Colors.RED_600
RED_700 = Colors.RED_700
RED_800 = Colors.RED_800
RED_900 = Colors.RED_900

# Green
GREEN = Colors.GREEN
GREEN_50 = Colors.GREEN_50
GREEN_100 = Colors.GREEN_100
GREEN_200 = Colors.GREEN_200
GREEN_400 = Colors.GREEN_400
GREEN_500 = Colors.GREEN_500
GREEN_600 = Colors.GREEN_600
GREEN_700 = Colors.GREEN_700
GREEN_800 = Colors.GREEN_800

# Blue
BLUE = Colors.BLUE
BLUE_50 = Colors.BLUE_50
BLUE_100 = Colors.BLUE_100
BLUE_200 = Colors.BLUE_200
BLUE_300 = Colors.BLUE_300
BLUE_400 = Colors.BLUE_400
BLUE_500 = Colors.BLUE_500
BLUE_600 = Colors.BLUE_600
BLUE_700 = Colors.BLUE_700
BLUE_800 = Colors.BLUE_800

# Grey
GREY = Colors.GREY
ORANGE = Colors.ORANGE
AMBER_100 = Colors.AMBER_100
AMBER_500 = Colors.AMBER_500
AMBER_700 = Colors.AMBER_700
AMBER_800 = Colors.AMBER_800
YELLOW_50 = Colors.YELLOW_50
YELLOW = Colors.YELLOW
YELLOW_900 = Colors.YELLOW_900
TEAL_700 = Colors.TEAL_700
PURPLE_700 = Colors.PURPLE_700
CYAN_400 = Colors.CYAN_400
CYAN_700 = Colors.CYAN_700
GREY_50 = Colors.GREY_50
GREY_100 = Colors.GREY_100
GREY_200 = Colors.GREY_200
GREY_300 = Colors.GREY_300
GREY_400 = Colors.GREY_400
GREY_500 = Colors.GREY_500
GREY_600 = Colors.GREY_600
GREY_700 = Colors.GREY_700
GREY_800 = Colors.GREY_800
GREY_900 = Colors.GREY_900

# Blue Grey
BLUE_GREY = Colors.BLUE_GREY
BLUE_GREY_100 = Colors.BLUE_GREY_100
BLUE_GREY_200 = Colors.BLUE_GREY_200
BLUE_GREY_300 = Colors.BLUE_GREY_300
BLUE_GREY_400 = Colors.BLUE_GREY_400
BLUE_GREY_500 = Colors.BLUE_GREY_500
BLUE_GREY_600 = Colors.BLUE_GREY_600
BLUE_GREY_700 = Colors.BLUE_GREY_700
BLUE_GREY_800 = Colors.BLUE_GREY_800
BLUE_GREY_900 = Colors.BLUE_GREY_900

# Amber/Orange/Yellow
AMBER_50 = Colors.AMBER_50
AMBER_100 = Colors.AMBER_100
AMBER_200 = Colors.AMBER_200
AMBER_300 = Colors.AMBER_300
AMBER_400 = Colors.AMBER_400
AMBER_500 = Colors.AMBER_500
AMBER_600 = Colors.AMBER_600
AMBER_700 = Colors.AMBER_700
AMBER_800 = Colors.AMBER_800
AMBER_900 = Colors.AMBER_900

ORANGE = Colors.ORANGE
ORANGE_50 = Colors.ORANGE_50
ORANGE_100 = Colors.ORANGE_100
ORANGE_200 = Colors.ORANGE_200
ORANGE_300 = Colors.ORANGE_300
ORANGE_400 = Colors.ORANGE_400
ORANGE_500 = Colors.ORANGE_500
ORANGE_600 = Colors.ORANGE_600
ORANGE_700 = Colors.ORANGE_700
ORANGE_800 = Colors.ORANGE_800
ORANGE_900 = Colors.ORANGE_900

YELLOW = Colors.YELLOW
YELLOW_50 = Colors.YELLOW_50
YELLOW_100 = Colors.YELLOW_100
YELLOW_200 = Colors.YELLOW_200
YELLOW_300 = Colors.YELLOW_300
YELLOW_400 = Colors.YELLOW_400
YELLOW_500 = Colors.YELLOW_500
YELLOW_600 = Colors.YELLOW_600
YELLOW_700 = Colors.YELLOW_700
YELLOW_800 = Colors.YELLOW_800
YELLOW_900 = Colors.YELLOW_900

# Basic
BLACK = Colors.BLACK
BLACK12 = Colors.BLACK12
WHITE = Colors.WHITE

# Outline
OUTLINE = Colors.OUTLINE
OUTLINE_VARIANT = Colors.OUTLINE_VARIANT

# Primary (alias)
PRIMARY_COLOR = Colors.PRIMARY