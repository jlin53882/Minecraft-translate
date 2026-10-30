"""打包頁的控制項組裝（BundlerView 的 mixin）。"""

import flet as ft

from app.services_impl.pack_format_service import QUICK_VERSIONS
from app.ui import design, kit
from app.ui.design import C
from app.views.pipeline.pipeline_workbench import (
    build_pipeline_status_card,
    build_pipeline_workbench,
)


class BundlerWidgetsMixin:
    """BundlerView 的控制項組裝（卡片、路徑列、版本選擇、預覽區）。"""

    def _build_controls(self):
        version_section = self._build_bundler_version_section()
        info_card = self._build_bundler_info_card(version_section)
        paths_card = self._build_bundler_paths_card()
        mcmeta_card, preview_card, start_button = self._build_bundler_preview_cards()
        run_card = build_pipeline_status_card(
            progress=self.progress_bar,
            progress_status=self.status_text,
            hint=kit.hint_text("完成後會產生包含 pack.mcmeta 的資源包 ZIP。"),
        )
        log_card = kit.section_card(
            "打包日誌",
            ft.Container(content=self.log_view, height=200),
            icon=ft.Icons.RECEIPT_LONG,
            tone="gold",
            collapsible=True,
        )
        log_card.visible = False
        self.log_card = log_card

        self.workbench_view = build_pipeline_workbench(
            title="資源包打包",
            subtitle="將翻譯結果打包成可直接使用的資源包 ZIP，自動產生 pack.mcmeta 與 pack.png",
            icon=ft.Icons.INVENTORY_2_OUTLINED,
            tone="gold",
            left_controls=[info_card, paths_card, preview_card, mcmeta_card],
            right_controls=[run_card, log_card],
            actions=[start_button],
        )
        self.controls = [self.workbench_view]
        self._update_preview()

    def _build_bundler_version_section(self):
        """版本輸入區塊：單一輸入框 + 常用版本按鈕 + 即時換算說明。"""
        self.version_summary = ft.Text("", size=11.5, color=C.MUTED)
        quick_row = ft.Row(
            [
                ft.Text("常用：", size=11.5, color=C.MUTED),
                *[
                    ft.Button(
                        version,
                        on_click=lambda e, v=version: self._use_quick_version(v),
                    )
                    for version in QUICK_VERSIONS
                ],
            ],
            spacing=6,
            wrap=True,
        )
        version_section = ft.Column(
            [
                kit.section_label("遊戲版本或 pack_format"),
                self.version_field,
                quick_row,
                kit.section_label("最大使用版本（選填）"),
                self.max_format_field,
                self.version_summary,
            ],
            spacing=6,
        )
        self._version_section = version_section
        return version_section

    def _build_bundler_info_card(self, version_section):
        """資訊卡片。"""

        info_card = kit.section_card(
            "資源包資訊",
            ft.Column(
                [
                    version_section,
                    ft.Column(
                        [kit.section_label("檔案敘述"), self.description_field],
                        spacing=6,
                    ),
                    ft.Column(
                        [
                            kit.section_label("資源包圖片"),
                            ft.Row(
                                [
                                    self.pack_image_field,
                                    kit.pick_button(
                                        ft.Icons.IMAGE_SEARCH,
                                        "選擇圖片",
                                        self._pick_pack_image,
                                    ),
                                ],
                                spacing=8,
                            ),
                        ],
                        spacing=6,
                    ),
                ],
                spacing=16,
            ),
            icon=ft.Icons.DIAMOND_OUTLINED,
            tone="gold",
        )
        return info_card

    def _build_bundler_paths_card(self):
        """路徑卡片。"""

        paths_card = kit.section_card(
            "路徑與額外內容",
            ft.Column(
                [
                    ft.Column(
                        [
                            kit.section_label("翻譯專案根目錄 *"),
                            ft.Row(
                                [
                                    self.root_dir_field,
                                    kit.pick_button(
                                        ft.Icons.FOLDER_OPEN,
                                        "選擇資料夾",
                                        self._pick_root_dir,
                                    ),
                                ],
                                spacing=8,
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Column(
                        [
                            kit.section_label("最終 ZIP 儲存路徑"),
                            ft.Row(
                                [
                                    self.output_zip_field,
                                    kit.pick_button(
                                        ft.Icons.SAVE_AS,
                                        "選擇儲存位置",
                                        self._pick_output_zip,
                                    ),
                                ],
                                spacing=8,
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Column(
                        [
                            kit.section_label("其他指定來源"),
                            self.extra_source_controls.content,
                        ],
                        spacing=8,
                    ),
                ],
                spacing=16,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="em",
        )
        return paths_card

    def _build_bundler_preview_cards(self):
        """預覽與 mcmeta 卡片。"""

        # --- 右側預覽 ---
        self.preview_title = ft.Text(spans=[], size=14, selectable=True)
        self.preview_format = ft.Text(
            "", size=12, color=C.DIM, font_family=design.FONT_MONO
        )
        self.preview_image = ft.Container(
            width=72,
            height=72,
            border_radius=8,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE2),
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        self.preview_source_note = ft.Text(
            "", size=11.5, color=C.GOLD, selectable=True, visible=False
        )
        self.mcmeta_view = ft.Text(
            "", size=12, selectable=True, color=C.MUTED, font_family=design.FONT_MONO
        )
        self.mcmeta_source_note = ft.Text(
            "", size=11.5, color=C.GOLD, selectable=True, visible=False
        )
        preview_card = kit.section_card(
            "資源包預覽",
            ft.Row(
                [
                    self.preview_image,
                    ft.Column(
                        [
                            ft.Text("zip 內的 pack.mcmeta", size=11.5, color=C.DIM),
                            self.preview_title,
                            self.preview_format,
                            self.preview_source_note,
                        ],
                        spacing=3,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            icon=ft.Icons.VISIBILITY_OUTLINED,
            tone="dia",
        )
        mcmeta_card = kit.section_card(
            "pack.mcmeta",
            ft.Column([self.mcmeta_source_note, self.mcmeta_view], spacing=8),
            icon=ft.Icons.DATA_OBJECT,
            tone="gold",
        )

        self.start_button = start_button = kit.button(
            "開始打包",
            "primary",
            icon=ft.Icons.INVENTORY_2_OUTLINED,
            size="lg",
            on_click=self.start_bundling_clicked,
        )
        return mcmeta_card, preview_card, start_button
