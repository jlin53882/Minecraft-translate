from __future__ import annotations

import flet as ft

from app.ui.components import styled_card


def build_pick_button(view, target):
    return ft.IconButton(
        icon=ft.Icons.FOLDER_OPEN_OUTLINED,
        icon_color=ft.Colors.BLUE_GREY_700,
        tooltip='瀏覽...',
        on_click=lambda e: view.pick_directory(target),
    )


def build_settings_card(view):
    return styled_card(
        title='任務設定',
        icon=ft.Icons.FOLDER,
        icon_color=ft.Colors.AMBER_600,
        content=ft.Column(
            spacing=20,
            controls=[
                ft.Column(
                    spacing=8,
                    controls=[
                        ft.Row([
                            ft.Icon(ft.Icons.DNS, size=16, color=ft.Colors.BLUE_GREY),
                            ft.Text('Mods 資料夾', weight=ft.FontWeight.W_500),
                        ]),
                        ft.Text('包含所有 .jar 模組檔案的 Mods 資料夾', size=12, color=ft.Colors.GREY_500),
                        ft.Row(
                            controls=[
                                ft.Container(content=view.mods_dir_textfield, expand=True),
                                build_pick_button(view, view.mods_dir_textfield),
                            ],
                            spacing=5,
                        ),
                    ],
                ),
                ft.Column(
                    spacing=8,
                    controls=[
                        ft.Row([
                            ft.Icon(ft.Icons.OUTPUT, size=16, color=ft.Colors.BLUE_GREY),
                            ft.Text('輸出資料夾', weight=ft.FontWeight.W_500),
                        ]),
                        ft.Text('提取後輸出的資料夾（可留空）會根據選擇型態自動輸出對應結尾資料夾 ，如果輸出路徑中有，則優先使用', size=12, color=ft.Colors.GREY_500),
                        ft.Row(
                            controls=[
                                ft.Container(content=view.output_dir_textfield, expand=True),
                                build_pick_button(view, view.output_dir_textfield),
                                ft.IconButton(
                                    icon=ft.Icons.CLEAR,
                                    icon_size=20,
                                    tooltip='清除路徑',
                                    on_click=view.clear_output_path,
                                ),
                            ],
                            spacing=5,
                        ),
                    ],
                ),
                ft.Container(
                    margin=ft.margin.only(top=10),
                    content=ft.Column(
                        spacing=15,
                        controls=[
                            ft.Row(controls=[view.lang_button, view.book_button], spacing=15),
                            ft.Row(controls=[view.preview_lang_button, view.preview_book_button], spacing=15),
                            ft.Column(
                                spacing=5,
                                controls=[
                                    view.status_text,
                                    ft.Container(
                                        content=view.progress_bar,
                                        border_radius=4,
                                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                                    ),
                                ],
                            ),
                        ],
                    ),
                ),
            ],
        ),
    )


def build_logs_card(view):
    return styled_card(
        title='執行日誌',
        icon=ft.Icons.RECEIPT_LONG,
        content=ft.Container(
            content=view.log_view,
            bgcolor='#1e1e1e',
            border_radius=8,
            expand=True,
            padding=10,
        ),
        expand=True,
    )
