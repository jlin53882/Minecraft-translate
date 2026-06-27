"""extractor_panels.py - JAR 提取頁面面板建構器。

本模組負責 ExtractorView 的 UI 面板組合，提供以下面板：

Layout（最外層）：
  - build_main_layout() → ft.Column（單欄垂直：設定在上，日誌在下）

Settings（左欄）：
  - build_settings_panel() → 路徑設定卡片 + 動作區卡片 + 統計徽章

Logs（右欄）：
  - build_logs_panel() → 狀態列 + 日誌檢視器（固定高度 350dp，可滾動）

面板構建工具：
  - _build_status_bar() → 狀態列（4px 左側彩色邊線 + status_text + 進度條 + 百分比）
  - _build_stats_badge() → 統計徽章（成功/跳過/失敗即時計數）
  - _build_path_row() → 路徑輸入列（icon 前綴 + TextField + 選擇按鈕）
  - _build_action_zone() → 動作區卡片（執行按鈕 + 預覽按鈕 + 跳過開關）
  - _build_pick_button() → 目錄選擇 IconButton

設計原則：
  - 所有面板皆使用 build_* 命名，內部工具用 _build_* 命名
  - view 實例屬性（如 status_text、progress_bar）由面板函式直接注入
  - 每個卡片獨立的邊框 + 灰底 radius=10 包裝，統一視覺一致性
"""

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
    """路徑輸入列：前綴圖示 + TextField + 選擇按鈕。

    參數：
        view: ExtractorView 實例
        icon: Flet 圖示（如 ft.Icons.DNS）
        label: 輸入框標籤文字
        field: TextField 控制項
        pick_target: 點擊按鈕後填入路徑的 TextField

    Returns:
        ft.Container 包裝的路徑輸入列
    """
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
    """目錄選擇 IconButton。

    參數：
        view: ExtractorView 實例
        target: 選擇目錄後填入路徑的 TextField

    Returns:
        ft.IconButton 按鈕
    """
    return ft.IconButton(
        icon=ft.Icons.FOLDER_OPEN_OUTLINED,
        icon_color=ft.Colors.BLUE_GREY_700,
        tooltip='瀏覽...',
        on_click=lambda e: view.pick_directory(target),
    )


def _build_action_zone(
    view,
    extract_row: list[ft.Control],
    preview_row: list[ft.Control],
) -> ft.Container:
    """動作區卡片：包含「執行」與「預覽」兩組按鈕列，以及跳過開關。

    參數：
        view：ExtractorView 實例（需具備 skip_zh_cn_switch 屬性）。
        extract_row：執行按鈕列（Lang / Book / Dual Extract）。
        preview_row：預覽按鈕列（Lang / Book / Dual Preview）。

    回傳：
        ft.Container 包裝的動作區卡片，含灰白邊框與半透明背景。
    """
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
                ft.Container(height=4),
                view.skip_zh_cn_switch,
            ],
        ),
        padding=ft.Padding(left=12, top=10, right=12, bottom=10),
        border=ft.Border.all(1, theme.GREY_200),
        border_radius=8,
        bgcolor=ft.Colors.WHITE,
    )


def build_settings_panel(view) -> ft.Column:
    """左側設定面板：包含路徑輸入區、動作按鈕區、以及統計徽章。

    面板組合：
        1. 路徑卡片：Mods 資料夾 + 輸出資料夾（含清除按鈕）
        2. 動作卡片：執行區（Lang / Book / Dual Extract）+
                     預覽區（Lang / Book / Dual Preview）+
                     跳過 zh_cn 開關
        3. 統計徽章：成功 / 跳過 / 失敗計數（即時更新）

    參數：
        view：ExtractorView 實例。

    回傳：
        ft.Column，可直接加入 ExtractorView 的 controls。
    """
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
            # --- 動作區（包含跳過開關）---
            _build_action_zone(
                view,
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
    """右側日誌面板：包含狀態列與日誌檢視器。

    面板組合：
        1. 狀態列（_build_status_bar）：左側彩色邊線 + 狀態文字 + 進度條 + 百分比。
        2. 日誌檢視器（view.log_view）：固定高度 350dp，深色背景，可滾動。

    參數：
        view：ExtractorView 實例。

    回傳：
        ft.Column，可直接加入 ExtractorView 的 controls。
    """
    return ft.Column(
        spacing=10,
        scroll=ft.ScrollMode.ADAPTIVE,
        controls=[
            _build_status_bar(view),
            ft.Container(
                content=view.log_view,
                bgcolor='#1e1e1e',
                border_radius=8,
                height=350,
                padding=10,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
        ],
        expand=True,
    )


def build_main_layout(view) -> ft.Column:
    """ExtractorView 最外層垂直佈局：設定面板在上，日誌面板在下。

    面板組合：
        1. 設定面板（build_settings_panel）：灰底圓角包裝。
        2. 日誌面板（build_logs_panel）：灰底圓角包裝，expand=True 填滿剩餘空間。

    參數：
        view：ExtractorView 實例。

    回傳：
        ft.Column，寬度 expand=True，包含兩個包裝過的面板。
    """
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
            ),
        ],
    )