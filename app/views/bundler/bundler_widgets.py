"""打包頁的控制項組裝（BundlerView 的 mixin）。"""

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from translation_tool.utils.log_unit import log_debug


class BundlerWidgetsMixin:
    """BundlerView 的控制項組裝（卡片、路徑列、版本選擇、預覽區）。"""

    def _build_controls(self):
        version_section = self._build_bundler_version_section()
        info_card = self._build_bundler_info_card(version_section)
        paths_card = self._build_bundler_paths_card()
        mcmeta_card, preview_card, start_button = self._build_bundler_preview_cards()
        run_card = ft.Container(
            padding=ft.Padding.symmetric(horizontal=18, vertical=14),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
            content=ft.Row(
                [
                    ft.Column(
                        [self.status_text, self.progress_bar],
                        spacing=8,
                        tight=True,
                        expand=True,
                    ),
                    start_button,
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        log_card = kit.section_card(
            "打包日誌",
            ft.Container(content=self.log_view, height=200),
            icon=ft.Icons.RECEIPT_LONG,
            tone="gold",
            collapsible=True,
        )

        self.controls = [
            kit.page_header(
                "資源包打包",
                "將翻譯結果打包成可直接使用的資源包 ZIP，自動產生 pack.mcmeta 與 pack.png",
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                tone="gold",
            ),
            ft.Row(
                [
                    ft.Column([info_card, paths_card], spacing=16, expand=6),
                    ft.Column(
                        [preview_card, mcmeta_card, run_card], spacing=16, expand=5
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            log_card,
        ]
        self._update_preview()

    def _build_bundler_version_section(self):
        """版本選擇區塊。"""
        log_debug(f"_build_controls: version_expanded={self.version_expanded}")
        # --- 版本選擇（可展開的搜尋清單）---
        current = self.version_search.value or ""
        self._version_toggle_label = ft.Text(
            current or "選擇遊戲版本",
            size=13.5,
            color=C.TEXT if current else C.DIM,
            expand=True,
        )
        self._version_toggle_format = ft.Text(
            self._format_range(self.version_data.get(current, {})),
            size=12,
            color=C.DIM,
            font_family=design.FONT_MONO,
        )
        self._version_toggle_icon = ft.Icon(
            ft.Icons.EXPAND_MORE, size=20, color=C.MUTED
        )
        version_toggle = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.DIAMOND_OUTLINED, size=18, color=C.GOLD),
                    self._version_toggle_label,
                    self._version_toggle_format,
                    self._version_toggle_icon,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=self._toggle_version_expand,
            ink=True,
            padding=ft.Padding.symmetric(horizontal=12, vertical=12),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE2),
            border_radius=design.RADIUS_CONTROL,
        )
        version_dropdown_container = ft.Container(
            content=ft.Column(
                [self.version_search, ft.Container(self.version_list, height=170)],
                spacing=8,
            ),
            padding=10,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
            visible=False,
        )
        self.version_dropdown_container_ref = version_dropdown_container
        version_section = ft.Column(
            [
                kit.section_label("遊戲版本 (pack_format)"),
                version_toggle,
                version_dropdown_container,
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
                            kit.section_label(
                                "其他指定資料夾 / 檔案（從選擇資料夾的下一層開始打包進 ZIP）"
                            ),
                            self.extra_folders_view,
                            kit.button(
                                "新增資料夾",
                                "secondary",
                                icon=ft.Icons.ADD,
                                size="sm",
                                on_click=self._pick_extra_folder,
                            ),
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
        self.mcmeta_view = ft.Text(
            "", size=12, selectable=True, color=C.MUTED, font_family=design.FONT_MONO
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
            self.mcmeta_view,
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
