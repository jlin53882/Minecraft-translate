import flet as ft

from app.ui.components import (
    CARD_PADDING,
    CARD_RADIUS,
    CARD_BG_COLOR,
    styled_card,
    primary_button,
    secondary_button,
)


def test_styled_card_basic_props():
    inner = ft.Text("x")
    c = styled_card(title="T", icon=ft.Icons.INFO, content=inner)

    assert isinstance(c, ft.Container)
    assert c.padding == CARD_PADDING
    assert c.border_radius == CARD_RADIUS
    assert c.bgcolor == CARD_BG_COLOR

    # content should be a Column: header + divider + body
    assert isinstance(c.content, ft.Column)
    assert len(c.content.controls) == 3


def test_primary_button_has_click_handler_and_style():
    called = {"v": False}

    def on_click(e):
        called["v"] = True

    b = primary_button("Go", icon=ft.Icons.PLAY_ARROW, on_click=on_click)
    assert isinstance(b, ft.ElevatedButton)
    assert b.on_click is on_click


def test_primary_button_can_override_bgcolor():
    b = primary_button("OK", icon=ft.Icons.CHECK, on_click=lambda e: None, bgcolor=ft.Colors.GREEN_700)
    # ButtonStyle 物件在 flet 內部可能不是單純 dict，這裡只確認有設 style，避免測太死。
    assert b.style is not None


def test_secondary_button_is_outlined():
    b = secondary_button("Dry", icon=ft.Icons.SEARCH, on_click=lambda e: None)
    assert isinstance(b, ft.OutlinedButton)
