from __future__ import annotations

import flet as ft

from app.ui.components import primary_button

def build_card(view, title, controls_list):
    """建立一個包含標題與控制項的卡片 UI 元件。"""
    return ft.Card(
        elevation=2,
        surface_tint_color=ft.Colors.WHITE,
        content=ft.Container(
            padding=15,
            content=ft.Column(
                [
                    ft.Text(title, theme_style=ft.TextThemeStyle.TITLE_MEDIUM, color=ft.Colors.BLUE_800, weight=ft.FontWeight.BOLD),
                    ft.Divider(height=10, thickness=1, color=ft.Colors.BLUE_50),
                    *controls_list,
                ],
                spacing=12,
            ),
        ),
    )

def build_header(view):
    """建立設定頁面的頂部標題列（含圖示與標題文字）。"""
    return ft.Container(
        padding=ft.padding.only(left=5, bottom=10),
        content=ft.Row([
            ft.Icon(ft.Icons.SETTINGS_APPLICATIONS, size=28, color=ft.Colors.BLUE_GREY_800),
            ft.Text('全域設定 (Global Settings)', theme_style=ft.TextThemeStyle.HEADLINE_MEDIUM, color=ft.Colors.BLUE_GREY_900),
        ]),
    )

def build_footer(view):
    """建立設定頁面的底部橫幅（含提示文字與儲存按鈕）。"""
    return ft.Container(
        padding=ft.padding.symmetric(horizontal=20, vertical=10),
        bgcolor=ft.Colors.WHITE,
        border=ft.border.only(top=ft.BorderSide(1, ft.Colors.GREY_300)),
        shadow=ft.BoxShadow(spread_radius=1, blur_radius=5, color=ft.Colors.BLACK12, offset=ft.Offset(0, -1)),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Text('提示：修改後請務必點擊儲存', color=ft.Colors.GREY_600, size=12),
                primary_button('儲存所有設定', icon=ft.Icons.SAVE, tooltip='寫入 config.json（請確認 API Keys 有填好）', on_click=view.save_config_clicked),
            ],
        ),
    )

def build_key_row(view, tf: ft.TextField):
    """建立包含 TextField 與刪除按鈕的橫向排列。"""
    row = ft.Row(controls=[tf, ft.IconButton(icon=ft.Icons.DELETE, on_click=lambda e: view.remove_key_row(row))])
    return row

def build_key_field(value: str = ''):
    """建立一個密碼类型的 TextField（可顯示密碼）。"""
    return ft.TextField(value=value, password=True, can_reveal_password=True, expand=True, dense=True)
