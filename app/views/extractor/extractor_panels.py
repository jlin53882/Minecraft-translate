"""extractor_panels.py - JAR 提取頁面面板建構器。

本模組負責 ExtractorView 的 UI 面板組合，提供以下面板：

Settings（左欄）：
  - build_settings_panel() → 路徑設定卡片 + 共用流水線步驟卡 + 跳過開關

面板構建工具：
  - _build_path_row() → 路徑輸入列（icon 前綴 + TextField + 選擇按鈕）
  - _build_action_zone() → 共用步驟列（提取按鈕 + 預覽按鈕）
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

from app.ui import kit
from app.ui.design import C
from app.views.pipeline.pipeline_workbench import (
    PipelineActionSpec,
    PipelineStepSpec,
    build_pipeline_steps_card,
)


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


def _build_action_zone(view, extract_row: list, preview_row: list) -> ft.Control:
    """建立共用垂直步驟卡（Lang / Book / Lang + Book）。

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
    steps = []
    for number, ((title, desc, icon, tone), extract, preview) in enumerate(
        zip(modes, extract_row, preview_row, strict=True),
        start=1,
    ):
        steps.append(
            PipelineStepSpec(
                number=number,
                title=title,
                description=desc,
                icon=icon,
                tone=tone,
                action_control=extract,
                extra_actions=(
                    PipelineActionSpec(
                        label="預覽",
                        icon=ft.Icons.PREVIEW,
                        tooltip=f"預覽 {title}",
                        control=preview,
                    ),
                ),
            )
        )
    return build_pipeline_steps_card(
        steps,
        title="提取流程",
        icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
        tone="dia",
    )


def build_settings_panel(view) -> ft.Column:
    """建立提取頁的設定內容，不重複建立頁首。

    頁首與左右工作區由 ``build_pipeline_workbench`` 統一負責；這個函式
    只保留提取頁自己的設定卡與模式操作卡，避免提取頁再維護另一套頁面
    外框。保留原函式名稱是為了讓既有測試與外部呼叫者不必改變。

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
                            vertical_alignment=ft.CrossAxisAlignment.START,
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
