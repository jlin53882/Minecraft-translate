"""show_snack：舊呼叫端的背景色會被轉成新設計的語意色組。"""

import flet as ft
import pytest

from app.ui import theme
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.ui.snack import show_snack, snack_style


@pytest.mark.parametrize(
    ("color", "tone"),
    [
        (ft.Colors.RED_600, "red"),
        (theme.ERROR, "red"),
        (ft.Colors.GREEN_400, "em"),
        (theme.SUCCESS, "em"),
        (ft.Colors.ORANGE_700, "gold"),
        (ft.Colors.AMBER_200, "gold"),
        (ft.Colors.BLUE_500, "dia"),
        (theme.PRIMARY, "em"),
        (ft.Colors.PURPLE_700, "ench"),
        (ft.Colors.GREY_700, "neutral"),
        (theme.WARNING, "gold"),
        (theme.INFO, "dia"),
        (theme.RED_200, "red"),
        (theme.GREEN_700, "em"),
        (None, "neutral"),
    ],
)
def test_snack_style_maps_legacy_colors_to_tones(color, tone):
    assert snack_style(color)[0] == tone


class _Page:
    def __init__(self):
        self.overlay = []
        self.width = 1100

    def show_dialog(self, snack):
        self.overlay.append(snack)

    def update(self):
        pass


def test_show_snack_uses_panel_background_and_tone_text():
    page = _Page()
    snack = show_snack(page, "完成", ft.Colors.GREEN_600)
    assert snack.bgcolor == C.RAISED
    assert snack.content.value == "完成"  # 既有呼叫端 / 測試依賴 content 是單一 Text
    assert snack.content.color == get_tone("em").fg
    assert snack.behavior == ft.SnackBarBehavior.FLOATING


def test_show_snack_reserves_space_above_the_app_statusbar():
    snack = show_snack(_Page(), "完成")
    assert snack.margin is not None
    assert snack.margin.left == pytest.approx(320)
    assert snack.margin.right == pytest.approx(320)
    assert snack.margin.bottom >= 40


def test_show_snack_default_is_error_toned():
    snack = show_snack(_Page(), "失敗")
    assert snack.content.color == get_tone("red").fg
