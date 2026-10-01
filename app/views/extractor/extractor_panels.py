"""extractor_panels.py - JAR 提取頁面面板建構器。

本模組負責 ExtractorView 的 UI 面板組合，提供以下面板：

Settings（左欄）：
  - build_settings_panel() → 路徑設定卡片 + 動作區卡片 + 跳過開關

面板構建工具：
  - _build_path_row() → 路徑輸入列（icon 前綴 + TextField + 選擇按鈕）
  - _build_action_zone() → 動作區卡片（執行按鈕 + 預覽按鈕）
  - _build_pick_button() → 目錄選擇 IconButton

🐛 2026-08-01 user review 物理刪除 (commit 14b823e 系列):
  - build_logs_panel → 移至 extractor_view.py 內聯 (S1 修復)
  - _build_status_bar → 內聯到 build_logs_panel 裡，刪除
  - build_main_layout → 沒用上，由 ExtractorView.__init__ 直接組裝

設計原則：
  - 所有面板皆使用 build_* 命名，內部工具用 _build_* 命名
  - view 實例屬性（如 status_text、progress_bar）由面板函式直接注入
  - 每個卡片獨立的邊框 + 灰底 radius=10 包裝，統一視覺一致性
"""

import flet as ft

from app.ui import design, kit
from app.ui.design import C


def _build_path_row(view, icon, label, field, pick_target) -> ft.Control:
    """路徑輸入列：小標籤 + TextField + 選擇按鈕。

    參數：
        view: ExtractorView 實例
        icon: 標籤前的圖示（保留參數；新版只用文字小標籤）
        label: 輸入框標籤文字
        field: TextField 控制項
        pick_target: 點擊按鈕後填入路徑的 TextField
    """
    return ft.Column(
        spacing=6,
        controls=[
            kit.section_label(label),
            ft.Row(
                controls=[field, _build_pick_button(view, pick_target)],
                spacing=8,
            ),
        ],
    )


def _build_pick_button(view, target):
    """目錄選擇 IconButton。

    參數：
        view: ExtractorView 實例
        target: 選擇目錄後填入路徑的 TextField
    """
    return kit.pick_button(
        ft.Icons.FOLDER_OPEN_OUTLINED,
        "瀏覽...",
        lambda e: view.pick_directory(target),
    )


def _mode_card(
    title: str, desc: str, icon: str, tone: str, extract_button, preview_button
) -> ft.Container:
    """一種提取模式的卡片：圖示 + 說明 + 「提取」「預覽」兩顆按鈕。"""
    return ft.Container(
        expand=1,
        padding=16,
        bgcolor=C.PANEL,
        border=ft.Border.all(1, C.LINE),
        border_radius=design.RADIUS_CARD,
        content=ft.Column(
            [
                kit.tone_icon(icon, tone, size=20, box=40, radius=11),
                ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=C.TEXT),
                ft.Text(desc, size=12, color=C.DIM),
                ft.Container(height=4),
                extract_button,
                preview_button,
            ],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def _build_action_zone(view, extract_row: list, preview_row: list) -> ft.Control:
    """三種模式卡（Lang / Book / Lang + Book），各自有「提取」與「預覽」按鈕。

    參數：
        view：ExtractorView 實例。
        extract_row：[Lang, Book, Dual] 的提取按鈕。
        preview_row：[Lang, Book, Dual] 的預覽按鈕。
    """
    modes = (
        (
            "Lang 語言檔",
            "從 mods/*.jar 提取 assets/*/lang 語言檔",
            ft.Icons.TRANSLATE,
            "dia",
        ),
        ("Book 手冊", "提取 Patchouli 等手冊內容", ft.Icons.MENU_BOOK_OUTLINED, "em"),
        (
            "Lang + Book",
            "同時提取語言檔與手冊，分別輸出統計",
            ft.Icons.LAYERS_OUTLINED,
            "ench",
        ),
    )
    cards = [
        _mode_card(title, desc, icon, tone, extract, preview)
        for (title, desc, icon, tone), extract, preview in zip(
            modes, extract_row, preview_row, strict=True
        )
    ]
    return ft.Row(cards, spacing=16, vertical_alignment=ft.CrossAxisAlignment.START)


def build_settings_panel(view) -> ft.Column:
    """整個提取頁的內容：頁首 + 路徑 / 選項設定 + 三種模式卡。

    參數：
        view：ExtractorView 實例（需具備輸入框、開關與六顆按鈕屬性）。
    """
    settings_card = kit.section_card(
        "提取設定",
        ft.Column(
            spacing=16,
            controls=[
                _build_path_row(
                    view,
                    ft.Icons.DNS,
                    "Mods 資料夾（含 JAR） *",
                    view.mods_dir_textfield,
                    view.mods_dir_textfield,
                ),
                ft.Column(
                    spacing=6,
                    controls=[
                        kit.section_label("輸出資料夾"),
                        ft.Row(
                            controls=[
                                view.output_dir_textfield,
                                _build_pick_button(view, view.output_dir_textfield),
                                ft.IconButton(
                                    icon=ft.Icons.CLEAR,
                                    icon_size=18,
                                    icon_color=C.MUTED,
                                    tooltip="清除路徑",
                                    on_click=view.clear_output_path,
                                ),
                            ],
                            spacing=8,
                        ),
                    ],
                ),
                view.skip_zh_cn_switch,
            ],
        ),
        icon=ft.Icons.SETTINGS_OUTLINED,
        tone="dia",
    )
    return ft.Column(
        scroll=ft.ScrollMode.ADAPTIVE,
        expand=True,
        spacing=18,
        controls=[
            kit.page_header(
                "JAR 提取",
                "從模組 JAR 取出語言檔與 Patchouli 手冊；可先預覽掃描結果再決定提取範圍",
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                tone="dia",
            ),
            settings_card,
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
        ],
    )
