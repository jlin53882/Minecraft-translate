import flet as ft

from app.ui.view_wrapper import VIEW_PADDING, wrap_view


def test_wrap_view_is_a_plain_padded_container():
    """頁面外框只給統一留白並填滿空間；背景由 Page 提供（主題切換不需重建頁面）。"""
    inner = ft.Text("x")
    c = wrap_view(inner)

    assert isinstance(c, ft.Container)
    assert c.content is inner
    assert c.expand is True
    assert c.padding == VIEW_PADDING
    # 不再是卡片：沒有自己的底色 / 陰影 / 圓角
    assert c.bgcolor is None
    assert c.shadow is None
    assert not c.border_radius
