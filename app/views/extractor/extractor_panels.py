from __future__ import annotations

import flet as ft

from app.ui import theme


def _build_status_bar(view) -> ft.Container:
    """狀態列：左側彩色邊線 + 狀態文字 + 進度條 + 百分比"""
    view.status_text = ft.Text(
        "狀態：閒置",
        size=13,
        color=theme.GREY_700,
        weight=ft.FontWeight.W_500,
    )
    view.progress_bar = ft.ProgressBar(
        value=0, height=8, visible=True,
        bgcolor=theme.GREY_200,
        color=theme.BLUE,
    )
    view._progress_pct = ft.Text(
        "0%",
        size=12,
        color=theme.GREY_600,
        weight=ft.FontWeight.BOLD,
    )

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        view.status_text,
                        view._progress_pct,
                    ],
                    spacing=12,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=view.progress_bar,
                    border_radius=4,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                ),
            ],
            spacing=6,
        ),
        padding=ft.Padding(left=12, top=10, right=12, bottom=10),
        border=ft.Border(
            left=ft.BorderSide(4, theme.GREY_400),
        ),
        border_radius=8,
        bgcolor=theme.GREY_50,
    )


def _build_stats_badge(view) -> ft.Container:
    """統計徽章：成功 / 警告 / 失敗計數"""
    view._stats_success = ft.Text("0", size=14, weight=ft.FontWeight.BOLD, color=theme.GREEN)
    view._stats_warnings = ft.Text("0", size=14, weight=ft.FontWeight.BOLD, color=theme.ORANGE)
    view._stats_failures = ft.Text("0", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.ERROR)

    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=theme.GREEN, size=16),
                ft.Text("成功: ", size=12, color=theme.GREY_600),
                view._stats_success,
                ft.Container(width=20),
                ft.Icon(ft.Icons.WARNING, color=theme.ORANGE, size=16),
                ft.Text("跳過: ", size=12, color=theme.GREY_600),
                view._stats_warnings,
                ft.Container(width=20),
                ft.Icon(ft.Icons.ERROR, color=ft.Colors.ERROR, size=16),
                ft.Text("失敗: ", size=12, color=theme.GREY_600),
                view._stats_failures,
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.START,
        ),
        padding=ft.Padding(left=10, top=8, right=10, bottom=8),
        border=ft.Border.all(1, theme.GREY_200),
        border_radius=6,
        bgcolor=ft.Colors.WHITE,
    )


def _build_path_row(view, icon, label, field, pick_target) -> ft.Container:
    """路徑輸入列：前綴圖示 + TextField + 選擇按鈕"""
    return ft.Column(
        spacing=4,
        controls=[
            ft.Row(
                controls=[
                    ft.Icon(icon, size=14, color=ft.Colors.BLUE_GREY),
                    ft.Text(label, size=12, weight=ft.FontWeight.W_500, color=theme.GREY_700),
                ],
                spacing=6,
            ),
            ft.Row(
                controls=[
                    ft.Container(content=field, expand=True),
                    _build_pick_button(view, pick_target),
                ],
                spacing=6,
            ),
        ],
    )


def _build_pick_button(view, target):
    return ft.IconButton(
        icon=ft.Icons.FOLDER_OPEN_OUTLINED,
        icon_color=ft.Colors.BLUE_GREY_700,
        tooltip='瀏覽...',
        on_click=lambda e: view.pick_directory(target),
    )


def _build_action_zone(
    extract_row: list[ft.Control],
    preview_row: list[ft.Control],
) -> ft.Container:
    """動作區卡片：Extract Zone + Preview Zone 分組"""
    extract_label = ft.Row(
        controls=[
            ft.Icon(ft.Icons.PLAY_ARROW, size=14, color=theme.BLUE_700),
            ft.Text("執行", size=11, weight=ft.FontWeight.BOLD, color=theme.GREY_600),
        ],
        spacing=4,
    )
    preview_label = ft.Row(
        controls=[
            ft.Icon(ft.Icons.PREVIEW, size=14, color=ft.Colors.AMBER_600),
            ft.Text("預覽", size=11, weight=ft.FontWeight.BOLD, color=theme.GREY_600),
        ],
        spacing=4,
    )

    return ft.Container(
        content=ft.Column(
            spacing=8,
            controls=[
                extract_label,
                ft.Row(extract_row, spacing=10),
                ft.Container(height=2),
                preview_label,
                ft.Row(preview_row, spacing=10),
            ],
        ),
        padding=ft.Padding(left=12, top=10, right=12, bottom=10),
        border=ft.Border.all(1, theme.GREY_200),
        border_radius=8,
        bgcolor=ft.Colors.WHITE,
    )


def build_settings_panel(view) -> ft.Column:
    """左欄：路徑設定 + 跳過開關 + 按鈕區 + 統計"""
    return ft.Column(
        scroll=ft.ScrollMode.ADAPTIVE,
        spacing=16,
        controls=[
            # --- 路徑設定 ---
            ft.Container(
                content=ft.Column(
                    spacing=12,
                    controls=[
                        _build_path_row(view, ft.Icons.DNS, "Mods 資料夾", view.mods_dir_textfield, view.mods_dir_textfield),
                        ft.Column(
                            spacing=4,
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Icon(ft.Icons.OUTPUT, size=14, color=ft.Colors.BLUE_GREY),
                                        ft.Text("輸出資料夾", size=12, weight=ft.FontWeight.W_500, color=theme.GREY_700),
                                    ],
                                    spacing=6,
                                ),
                                ft.Row(
                                    controls=[
                                        ft.Container(content=view.output_dir_textfield, expand=True),
                                        _build_pick_button(view, view.output_dir_textfield),
                                        ft.IconButton(
                                            icon=ft.Icons.CLEAR,
                                            icon_size=18,
                                            tooltip='清除路徑',
                                            on_click=view.clear_output_path,
                                        ),
                                    ],
                                    spacing=6,
                                ),
                            ],
                        ),
                    ],
                ),
                padding=14,
                border=ft.Border.all(1, theme.GREY_200),
                border_radius=8,
                bgcolor=ft.Colors.WHITE,
            ),
            # --- 跳過開關 ---
            ft.Container(
                content=ft.Row(
                    controls=[
                        view.skip_zh_cn_switch,
                        ft.Text("跳過 zh_cn 抽取", size=13, color=theme.GREY_700),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.START,
                ),
                padding=ft.Padding(left=10, top=8, right=10, bottom=8),
                border=ft.Border.all(1, theme.GREY_200),
                border_radius=6,
                bgcolor=ft.Colors.WHITE,
            ),
            # --- 動作區 ---
            _build_action_zone(
                extract_row=[
                    view.lang_button,
                    view.book_button,
                    view.dual_extract_button,
                ],
                preview_row=[
                    view.preview_lang_button,
                    view.preview_book_button,
                    view.dual_preview_button,
                ],
            ),
            # --- 統計 Badge ---
            _build_stats_badge(view),
        ],
    )


def build_logs_panel(view) -> ft.Column:
    """日誌面板：狀態列 + 日誌檢視器（可滾動）"""
    return ft.Column(
        spacing=10,
        scroll=ft.ScrollMode.ADAPTIVE,
        controls=[
            _build_status_bar(view),
            ft.Container(
                content=view.log_view,
                bgcolor='#1e1e1e',
                border_radius=8,
                expand=True,
                padding=10,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
        ],
        expand=True,
    )


def build_main_layout(view) -> ft.Column:
    """單欄垂直佈局：設定卡片在上，日誌卡片在下（均可滾動）"""
    return ft.Column(
        scroll=ft.ScrollMode.ADAPTIVE,
        spacing=12,
        controls=[
            ft.Container(
                content=build_settings_panel(view),
                padding=10,
                border=ft.Border.all(1, theme.GREY_200),
                border_radius=10,
                bgcolor=theme.GREY_50,
            ),
            ft.Container(
                content=build_logs_panel(view),
                padding=10,
                border=ft.Border.all(1, theme.GREY_200),
                border_radius=10,
                bgcolor=theme.GREY_50,
                expand=True,
            ),
        ],
    )