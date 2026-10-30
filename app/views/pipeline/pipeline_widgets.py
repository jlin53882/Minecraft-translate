"""PipelineView 的控制項與版面組裝（由 pipeline_view.py 拆出，#114）。"""

import flet as ft

from app.ui import kit
from app.views.pipeline.pipeline_config import RESOURCE_PACK_INSTALL_HINT
from app.views.pipeline.pipeline_workbench import (
    PipelineStepSpec,
    build_pipeline_status_card,
    build_pipeline_step_row,
    build_pipeline_steps_card,
    build_pipeline_workbench,
)


class PipelineWidgetsMixin:
    """PipelineView 的控制項與版面組裝（由 pipeline_view.py 拆出，#114）。"""

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
        return build_pipeline_step_row(
            PipelineStepSpec(
                number=number,
                title=title,
                description=desc,
                icon=icon,
                tone=tone,
                on_click=handler,
            ),
            on_button_created=self._run_buttons.append,
        )

    def _build_ui(self):
        paths_card = self._build_pipeline_paths_card()
        status_card, steps_card = self._build_pipeline_steps_and_status_cards()
        one_click = self._build_one_click_button()

        self.workbench_view = build_pipeline_workbench(
            title="模組流水線・一鍵製作",
            subtitle="從 JAR 提取到資源包打包，四個步驟一次完成；可隨時取消，已完成的批次會保留",
            icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
            tone="em",
            left_controls=[paths_card, steps_card],
            right_controls=[status_card, self.progress_panel.container],
            actions=[one_click],
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
        steps_card = build_pipeline_steps_card(
            [
                PipelineStepSpec(
                    1,
                    "抽取資源",
                    "掃描 JAR，取出 lang 與 Patchouli 手冊",
                    ft.Icons.UNARCHIVE,
                    "dia",
                    self._on_extract_click,
                ),
                PipelineStepSpec(
                    2,
                    "語系比對合併",
                    "en_us / zh_cn / zh_tw 智慧合併，保留既有繁中",
                    ft.Icons.CALL_MERGE,
                    "ench",
                    self._on_merge_click,
                ),
                PipelineStepSpec(
                    3,
                    "啟動翻譯",
                    "Gemini 批次翻譯待翻譯條目",
                    ft.Icons.AUTO_AWESOME,
                    "gold",
                    self._on_translate_click,
                ),
                PipelineStepSpec(
                    4,
                    "打包資源",
                    f"輸出 pack.mcmeta 與 ZIP；{RESOURCE_PACK_INSTALL_HINT}",
                    ft.Icons.INVENTORY_2,
                    "em",
                    self._on_bundle_click,
                ),
            ],
            on_button_created=self._run_buttons.append,
        )
        status_card = build_pipeline_status_card(
            progress=self.progress_bar,
            progress_status=self.progress_status,
        )
        return status_card, steps_card
