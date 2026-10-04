from __future__ import annotations

import flet as ft

from app.config_apply import apply_timing_note
from app.ui import design, kit
from app.ui.design import C


def build_card(view, title, controls_list):
    """建立一個包含標題與控制項的設定卡片（``kit.section_card``）。"""
    return kit.section_card(
        title,
        ft.Column(list(controls_list), spacing=12),
        icon=ft.Icons.TUNE,
        tone="em",
    )


def build_header(view):
    """建立設定頁面的頂部標題列（圖示 + 標題 + 說明）。"""
    return kit.page_header(
        "設定",
        "所有設定會寫入 config.json；變更後請按「儲存」，部分項目需重新啟動才會套用",
        icon=ft.Icons.SETTINGS_OUTLINED,
        tone="em",
    )


def build_footer(view):
    """建立設定頁面的底部橫幅（含提示文字與儲存按鈕）。"""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=24, vertical=14),
        bgcolor=C.PANEL,
        border=ft.Border.only(top=ft.BorderSide(1, C.LINE)),
        content=ft.ResponsiveRow(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            run_spacing=10,
            controls=[
                ft.Container(
                    col={"xs": 12, "md": 8},
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=C.DIM),
                            ft.Text(
                                "提示：修改後請務必點擊儲存", color=C.MUTED, size=12.5
                            ),
                        ],
                        spacing=8,
                    ),
                ),
                ft.Container(
                    col={"xs": 12, "md": 4},
                    alignment=ft.Alignment.CENTER_RIGHT,
                    content=kit.button(
                        "儲存所有設定",
                        "primary",
                        icon=ft.Icons.SAVE_OUTLINED,
                        tooltip="寫入 config.json（請確認 API Keys 有填好）",
                        on_click=view.save_config_clicked,
                    ),
                ),
            ],
        ),
    )


def build_key_row(view, tf: ft.TextField):
    """建立包含 TextField 與刪除按鈕的橫向排列。"""
    row = ft.Row(
        controls=[
            tf,
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_color=C.RED,
                tooltip="移除這把 Key",
                on_click=lambda e: view.remove_key_row(row),
            ),
        ]
    )
    return row


def build_key_field(value: str = ""):
    """建立一個密碼類型的 TextField（可顯示密碼）。"""
    return kit.field(
        value=value,
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
        helper=apply_timing_note("lm_translator.keys"),
        text_style=ft.TextStyle(font_family=design.FONT_MONO),
    )
