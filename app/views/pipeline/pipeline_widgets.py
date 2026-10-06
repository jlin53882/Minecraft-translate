"""PipelineView 的控制項與版面組裝（由 pipeline_view.py 拆出，#114）。"""

import os

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.ui.sync_text_field import SyncTextField


def _has_files(path: str) -> bool:
    """資料夾存在且至少含有一個檔案。"""
    if not os.path.isdir(path):
        return False
    return any(files for _, _, files in os.walk(path))


class PipelineWidgetsMixin:
    """PipelineView 的控制項與版面組裝（由 pipeline_view.py 拆出，#114）。"""

    def _delete_key_field(self, row_obj):
        self.keys_container.controls.remove(row_obj)
        self._page.update()

    def _add_key_field(self, initial_value=""):
        new_row = ft.Row(spacing=10)
        key_tf = SyncTextField(
            value=initial_value,
            label=f"API Key {len(self.keys_container.controls) + 1}",
            expand=True,
            text_size=12,
            border_color=C.DIA,
        )
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE,
            icon_color=C.RED,
            on_click=lambda _: self._delete_key_field(new_row),
        )
        new_row.controls = [key_tf, del_btn]
        self.keys_container.controls.append(new_row)
        self._page.update()

    # =============================================================================
    # UI Layout
    # =============================================================================

    def _build_one_click_button(self):
        self._one_click_button = kit.button(
            "一鍵製作（自動執行所有流程）",
            "gold",
            icon=ft.Icons.FLASH_ON,
            size="lg",
            on_click=self._on_one_click_click,
        )
        return self._one_click_button

    def _step_row(
        self, number: int, title: str, desc: str, icon: str, tone: str, handler
    ) -> ft.Control:
        """一個流水線步驟：編號圓點 + 標題 / 說明 + 「執行此步驟」按鈕。"""
        button = kit.button(
            "執行",
            "secondary",
            icon=icon,
            size="sm",
            tooltip=f"單獨執行：{title}",
            on_click=handler,
        )
        self._run_buttons.append(button)
        t = get_tone(tone)
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=4, vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
            content=ft.Row(
                [
                    ft.Container(
                        width=34,
                        height=34,
                        border_radius=17,
                        bgcolor=t.bg,
                        border=ft.Border.all(1, t.line),
                        alignment=ft.Alignment.CENTER,
                        content=ft.Text(
                            str(number), size=14, weight=ft.FontWeight.BOLD, color=t.fg
                        ),
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT
                            ),
                            ft.Text(desc, size=12, color=C.DIM),
                        ],
                        spacing=1,
                        tight=True,
                        expand=True,
                    ),
                    button,
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_ui(self):
        paths_card = self._build_pipeline_paths_card()
        status_card, steps_card = self._build_pipeline_steps_and_status_cards()
        one_click = self._build_one_click_button()

        self.workbench_view = ft.Column(
            [
                kit.page_header(
                    "模組流水線・一鍵製作",
                    "從 JAR 提取到資源包打包，四個步驟一次完成；可隨時取消，已完成的批次會保留",
                    icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
                    tone="em",
                    actions=[one_click],
                ),
                ft.Row(
                    [
                        ft.Column([paths_card, steps_card], spacing=16, expand=5),
                        ft.Column(
                            [status_card, self.progress_panel.container],
                            spacing=16,
                            expand=7,
                        ),
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=18,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self.api_view = ft.Column(
            [
                ft.Text("API 金鑰管理", size=24, weight="bold", color=C.GOLD),
                ft.Container(content=self.keys_container, expand=True),
                ft.Button("儲存設定", icon=ft.Icons.SAVE, bgcolor=C.DIA, color=C.ON_EM),
            ],
            spacing=10,
            expand=True,
        )

        self.controls.append(self.workbench_view)

    def _build_pipeline_paths_card(self):
        """路徑卡片。"""
        paths_card = kit.section_card(
            "專案路徑",
            ft.Column(
                [
                    ft.Column(
                        [
                            kit.section_label("讀取來源（mods 資料夾） *"),
                            ft.Row(
                                [
                                    self.input_path_text,
                                    kit.pick_button(
                                        ft.Icons.FOLDER_OPEN,
                                        "選擇 Mod 來源",
                                        lambda _: self._page.run_task(
                                            self._pick_input_dir
                                        ),
                                    ),
                                ],
                                spacing=8,
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Column(
                        [
                            kit.section_label("輸出目的地 *"),
                            ft.Row(
                                [
                                    self.output_path_text,
                                    kit.pick_button(
                                        ft.Icons.FOLDER_SPECIAL_OUTLINED,
                                        "選擇輸出目錄",
                                        lambda _: self._page.run_task(
                                            self._pick_output_dir
                                        ),
                                    ),
                                ],
                                spacing=8,
                            ),
                        ],
                        spacing=6,
                    ),
                ],
                spacing=14,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="em",
        )
        return paths_card

    def _build_pipeline_steps_and_status_cards(self):
        """步驟與狀態卡片。"""
        steps_card = kit.section_card(
            "流水線步驟",
            ft.Column(
                [
                    self._step_row(
                        1,
                        "抽取資源",
                        "掃描 JAR，取出 lang 與 Patchouli 手冊",
                        ft.Icons.UNARCHIVE,
                        "dia",
                        self._on_extract_click,
                    ),
                    self._step_row(
                        2,
                        "語系比對合併",
                        "en_us / zh_cn / zh_tw 智慧合併，保留既有繁中",
                        ft.Icons.CALL_MERGE,
                        "ench",
                        self._on_merge_click,
                    ),
                    self._step_row(
                        3,
                        "啟動翻譯",
                        "Gemini 批次翻譯待翻譯條目",
                        ft.Icons.AUTO_AWESOME,
                        "gold",
                        self._on_translate_click,
                    ),
                    self._step_row(
                        4,
                        "打包資源",
                        "輸出 pack.mcmeta 與 ZIP，可直接放入 resourcepacks",
                        ft.Icons.INVENTORY_2,
                        "em",
                        self._on_bundle_click,
                    ),
                ],
                spacing=0,
            ),
            icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
            tone="ench",
        )
        status_card = ft.Container(
            padding=ft.Padding.symmetric(horizontal=18, vertical=14),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=C.DIM),
                            self.progress_status,
                        ]
                    ),
                    self.progress_bar,
                ],
                spacing=8,
            ),
        )
        return status_card, steps_card
