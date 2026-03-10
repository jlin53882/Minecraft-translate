import flet as ft

from app.ui.view_wrapper import (
    VIEW_MARGIN,
    VIEW_PADDING,
    VIEW_RADIUS,
    VIEW_SHADOW,
    wrap_view,
)


def test_wrap_view_returns_container_with_expected_style():
    """確保 wrap_view 統一外框樣式。

    這個測試的目的不是測 Flet 渲染（那屬於整合測試），
    而是確保我們的 UI 底層封裝不會被不小心改壞。
    """

    inner = ft.Text("hello")
    c = wrap_view(inner)

    assert isinstance(c, ft.Container)
    assert c.content == inner
    assert c.padding == VIEW_PADDING
    assert c.margin == VIEW_MARGIN
    assert c.border_radius == VIEW_RADIUS
    assert c.shadow == VIEW_SHADOW
    assert c.expand is True
