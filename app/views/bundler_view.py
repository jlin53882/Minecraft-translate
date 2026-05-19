"""app/views/bundler_view.py 模組。

用途：提供打包成品資源包的 UI 與執行流程。

Flet 0.85 執行緒安全須知
-----------------------
背景執行緒（threading.Thread）直接修改 UI 組件（progress_bar.value、controls.append 等）
會被 Flet 0.85 忽略。所有跨執行緒 UI 更新都必須包裝為 async 閉包，透過 page.run_task() 排程。
模式：
    async def _do_update(_=None):
        self.some_control.property = value
    self._page.run_task(_do_update)
"""

import flet as ft
import threading
import os
import json
from translation_tool.utils.log_unit import log_debug

from app.ui import theme
from app.ui.components import styled_card
from app.services_impl.config_service import load_config_json


class BundlerView(ft.Column):
    page: ft.Page
    file_picker: ft.FilePicker

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=16)
        self._page = page
        self.file_picker = file_picker
        self.extra_folders: list[str] = []
        self.version_data: dict = {}

        self.version_search = ft.TextField(
            label="搜尋版本",
            hint_text="輸入版本關鍵字...",
            expand=True,
            border_color=theme.OUTLINE,
            content_padding=10,
            on_change=self._on_version_search_change,
        )
        self.version_list = ft.ListView(
            expand=True,
            height=200,
            spacing=4,
            auto_scroll=False,
        )
        self.version_expanded = False
        self.description_field = ft.TextField(
            label="檔案敘述",
            hint_text="直接輸入文字，或使用 § 顏色代碼",
            expand=True,
            border_color=theme.OUTLINE,
            content_padding=10,
        )
        self.pack_image_field = ft.TextField(
            label="資源包圖片路徑",
            hint_text="選擇 pack.png 圖片（可選）",
            expand=True,
            border_color=theme.OUTLINE,
            content_padding=10,
        )
        self.root_dir_field = ft.TextField(
            label="翻譯專案根目錄",
            hint_text="包含所有翻譯產出的最上層資料夾",
            expand=True,
            border_color=theme.OUTLINE,
            content_padding=10,
            on_change=self._on_root_dir_change,
        )
        self.output_zip_field = ft.TextField(
            label="最終 ZIP 檔案儲存路徑",
            hint_text="留空則自動帶入翻譯專案根目錄+設定檔檔名",
            expand=True,
            border_color=theme.OUTLINE,
            content_padding=10,
        )
        self._config_output_zip_name = "可使用翻譯.zip"
        self.extra_folders_view = ft.ListView(height=100, spacing=4, auto_scroll=False)
        self.progress_bar = ft.ProgressBar(value=0, height=8, visible=False)
        self.log_view = ft.ListView(expand=True, spacing=4, auto_scroll=True)

        self._load_version_data()
        self._init_ui()
        self._load_output_zip_from_config()
        self._build_controls()

    def _load_output_zip_from_config(self):
        """從 config 載入 output_zip_name 並設定 hint_text"""
        config = load_config_json()
        self._config_output_zip_name = config.get("output_bundler", {}).get("output_zip_name", "可使用翻譯.zip")
        self.output_zip_field.hint_text = f"留空則自動帶入：{{root_dir}}\\{self._config_output_zip_name}"

    def _on_root_dir_change(self, e: ft.ControlEvent):
        """當翻譯專案根目錄變更時，更新 output_zip_field 的 hint_text"""
        root_dir = self.root_dir_field.value or ""
        if root_dir and not self.output_zip_field.value:
            self.output_zip_field.hint_text = f"留空則自動帶入：{root_dir}\\{self._config_output_zip_name}"
        elif not self.output_zip_field.value:
            self.output_zip_field.hint_text = f"留空則自動帶入：{{root_dir}}\\{self._config_output_zip_name}"

    def _load_version_data(self):
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "translation_tool",
            "core",
            "resource_pack_version.json",
        )
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self.version_data = json.load(f)
            except Exception:
                self.version_data = {}
        else:
            self.version_data = {}

    def _init_ui(self):
        self._refresh_version_list("")

    def _refresh_version_list(self, search_text: str):
        self.version_list.controls.clear()
        filtered = [v for v in self.version_data.keys() if search_text.lower() in v.lower()]
        if not filtered:
            self.version_list.controls.append(
                ft.Container(
                    content=ft.Text(
                        "請點擊或輸入關鍵字搜尋版本" if search_text else "無可用版本",
                        size=12,
                        color=theme.GREY_500,
                    ),
                    padding=8,
                )
            )
        for version_key in filtered:
            item = ft.Container(
                content=ft.Text(version_key, size=13, text_align=ft.TextAlign.START),
                padding=8,
                border=ft.Border.all(1, theme.OUTLINE),
                border_radius=6,
                on_click=lambda e, v=version_key: self._select_version(v),
            )
            self.version_list.controls.append(item)
        self._page.update()

    def _on_version_search_change(self, e: ft.ControlEvent):
        self._refresh_version_list(e.control.value or "")

    def _select_version(self, version: str):
        log_debug(f"_select_version called: {version}")
        self.version_search.value = version
        self.version_expanded = False
        self._version_toggle_label.value = version
        log_debug(f"_select_version: toggle_label={self._version_toggle_label.value}, expanded={self.version_expanded}")
        self._page.update()

    def _toggle_version_expand(self, e: ft.ControlEvent):
        self.version_expanded = not self.version_expanded
        log_debug(f"_toggle_version_expand: version_expanded={self.version_expanded}")
        self.version_dropdown_container_ref.visible = self.version_expanded
        self._page.update()

    def _build_controls(self):
        log_debug(f"_build_controls: version_expanded={self.version_expanded}")
        self._version_toggle_label = ft.Text(self.version_search.value or "", size=12, color=theme.GREY_800, expand=True)
        version_toggle = ft.Container(
            content=ft.Row([
                ft.Text("選擇版本", size=12, color=theme.GREY_600),
                self._version_toggle_label,
                ft.Icon(ft.Icons.EXPAND_MORE if self.version_expanded else ft.Icons.EXPAND_LESS, size=20),
            ]),
            on_click=self._toggle_version_expand,
            padding=8,
            border=ft.Border.all(1, theme.OUTLINE),
            border_radius=6,
        )
        version_dropdown_container = ft.Container(
            content=self.version_list,
            height=180,
            border=ft.Border.all(1, theme.OUTLINE),
            border_radius=6,
            padding=4,
            visible=False,
        )
        self.version_dropdown_container_ref = version_dropdown_container
        version_section = ft.Column([
            version_toggle,
            version_dropdown_container,
        ], spacing=4)
        self._version_section = version_section

        description_row = ft.Row(
            [
                ft.Container(
                    content=ft.Column([
                        ft.Text("檔案敘述", size=12, color=theme.GREY_600),
                        self.description_field,
                    ], spacing=4),
                    expand=True,
                ),
                ft.Container(
                    content=ft.Column([
                        ft.Text("資源包圖片", size=12, color=theme.GREY_600),
                        ft.Row([
                            self.pack_image_field,
                            ft.IconButton(
                                icon=ft.Icons.IMAGE_SEARCH,
                                tooltip="選擇圖片",
                                on_click=self._pick_pack_image,
                            ),
                        ], spacing=6),
                    ], spacing=4),
                    expand=True,
                ),
            ],
            spacing=16,
        )

        root_dir_row = ft.Row(
            [
                self.root_dir_field,
                # 選擇翻譯專案根目錄
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN,
                    tooltip="選擇資料夾",
                    on_click=self._pick_root_dir,
                ),
            ],
            spacing=8,
        )

        output_zip_row = ft.Row(
            [
                self.output_zip_field,
                # 選擇 ZIP 儲存位置
                ft.IconButton(
                    icon=ft.Icons.SAVE_AS,
                    tooltip="選擇儲存位置",
                    on_click=self._pick_output_zip,
                ),
            ],
            spacing=8,
        )

        extra_folder_section = ft.Column(
            [
                ft.Row([
                    ft.Text("其他指定資料夾", size=13, weight=ft.FontWeight.W_500),
                    # 新增額外資料夾至打包清單
                    ft.IconButton(
                        icon=ft.Icons.ADD,
                        icon_size=20,
                        tooltip="新增資料夾",
                        on_click=self._pick_extra_folder,
                    ),
                ], spacing=8),
                self.extra_folders_view,
                ft.Text("從選擇資料夾的下一層開始打包進 ZIP", size=11, color=theme.GREY_500),
            ],
            spacing=8,
        )

        # 開始打包按鈕：驗證輸入後啟動背景執行緒執行打包任務
        # 執行緒完成後透過 page.run_task() 更新 progress_bar / log_view
        start_button = ft.Button(
            "開始打包",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self.start_bundling_clicked,
            bgcolor=theme.SUCCESS,
            color=ft.Colors.WHITE,
        )

        log_container = ft.Container(
            content=self.log_view,
            bgcolor="#2b2f36",
            border=ft.Border.all(1, "#4b5563"),
            border_radius=8,
            padding=10,
            height=200,
        )

        self.controls = [
            styled_card(
                title="打包設定",
                icon=ft.Icons.ARCHIVE,
                content=ft.Column([
                    version_section,
                    description_row,
                    root_dir_row,
                    output_zip_row,
                    extra_folder_section,
                    start_button,
                    self.progress_bar,
                ], spacing=12),
            ),
            styled_card(
                title="打包日誌",
                icon=ft.Icons.RECEIPT_LONG,
                content=log_container,
            ),
        ]

    def _pick_pack_image(self, e: ft.ControlEvent):
        self.file_picker.on_upload = self._on_pack_image_picked
        self._page.run_task(self._async_pick_pack_image)

    async def _async_pick_pack_image(self):
        result = await self.file_picker.pick_files(
            dialog_title="選擇資源包圖片",
            allow_multiple=False,
            allowed_extensions=["png", "jpg", "jpeg"],
        )
        log_debug("_async_pick_pack_image result: {result}")
        if result:
            self.pack_image_field.value = result[0].path
            self._page.update()

    def _on_pack_image_picked(self, e: ft.FilePickerUploadEvent):
        pass

    def _pick_root_dir(self, e: ft.ControlEvent):
        self._page.run_task(self._async_pick_root_dir)

    async def _async_pick_root_dir(self):
        result = await self.file_picker.get_directory_path(dialog_title="選擇翻譯專案根目錄")
        log_debug(f"_async_pick_root_dir result: {result}")
        if result:
            path = result[0].path if hasattr(result[0], 'path') else result
            self.root_dir_field.value = path
            self._page.update()

    def _pick_output_zip(self, e: ft.ControlEvent):
        self._page.run_task(self._async_pick_output_zip)

    async def _async_pick_output_zip(self):
        result = await self.file_picker.save_file(
            dialog_title="選擇 ZIP 儲存位置",
            allowed_extensions=["zip"],
            file_name="output.zip",
        )
        log_debug("_async_pick_output_zip result: {result}")
        if result:
            self.output_zip_field.value = result
            self._page.update()

    def _on_output_zip_picked(self, e: ft.FilePickerUploadEvent):
        pass

    def _pick_extra_folder(self, e: ft.ControlEvent):
        self._page.run_task(self._async_pick_extra_folder)

    async def _async_pick_extra_folder(self):
        result = await self.file_picker.get_directory_path(dialog_title="選擇資料夾")
        log_debug("_async_pick_extra_folder result: {result}")
        if result and result not in self.extra_folders:
            self.extra_folders.append(result)
            self._refresh_extra_folders()
            self._page.update()

    def _on_extra_folder_picked(self, e: ft.FilePickerUploadEvent):
        pass

    def _refresh_extra_folders(self):
        self.extra_folders_view.controls.clear()
        for path in self.extra_folders:
            is_file = os.path.isfile(path)
            icon = ft.Icons.INSERT_DRIVE_FILE if is_file else ft.Icons.FOLDER
            self.extra_folders_view.controls.append(
                ft.Row(
                    [
                        ft.Icon(icon, size=16, color=theme.BLUE_GREY_500),
                        ft.Text(path, expand=True, size=13, text_align=ft.TextAlign.START),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=16,
                            tooltip="移除",
                            on_click=lambda e, p=path: self._remove_extra_folder(p),
                        ),
                    ],
                    spacing=6,
                )
            )

    def _remove_extra_folder(self, path: str):
        if path in self.extra_folders:
            self.extra_folders.remove(path)
            self._refresh_extra_folders()
            self._page.update()

    def _show_snack_bar(self, message: str, color: str = theme.ERROR):
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self._page.overlay.append(snack)
        snack.open = True
        self._page.update()

    def start_bundling_clicked(self, e: ft.ControlEvent):
        """開始打包按鈕事件處理。

        驗證必填欄位後，啟動背景執行緒執行打包任務。
        執行緒完成後透过 page.run_task() 更新 UI。
        """
        root_dir = self.root_dir_field.value or ""
        output_zip = self.output_zip_field.value or ""

        if not root_dir:
            self._show_snack_bar("請填寫「翻譯專案根目錄」")
            return

        if not output_zip:
            output_zip = os.path.join(root_dir, self._config_output_zip_name)

        version = self.version_search.value or ""
        description = self.description_field.value or ""
        pack_image = self.pack_image_field.value or ""

        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.log_view.controls.clear()
        self._append_log("開始執行打包...")
        self._page.update()

        thread = threading.Thread(
            target=self._bundling_worker,
            args=(root_dir, output_zip, version, description, pack_image),
        )
        thread.start()

    def _append_log(self, msg: str):
        self.log_view.controls.append(ft.Text(msg, size=12, color="cyan400"))

    def _bundling_worker(self, root_dir, output_zip, version, description, pack_image):
        from translation_tool.core.output_bundler import bundle_outputs_generator

        try:
            version_info = self.version_data.get(version, {}) if version else {}
            min_format = version_info.get("min_format", 0)
            max_format = version_info.get("max_format", 0)

            generator_kwargs = {
                "input_root_dir": root_dir,
                "output_zip_path": output_zip,
                "description": description,
                "min_format": min_format,
                "max_format": max_format,
                "pack_image_path": pack_image if pack_image else None,
                "extra_folders": self.extra_folders.copy(),
            }

            for update in bundle_outputs_generator(**generator_kwargs):
                log_msg = update.get("log", "")
                for line in log_msg.split("\n"):
                    if line.strip():
                        async def _do_append(_=None):
                            self.log_view.controls.append(ft.Text(line, size=12, color="cyan400"))
                        self._page.run_task(_do_append)
                if "progress" in update:
                    progress = update["progress"]
                    async def _do_progress(_=None):
                        self.progress_bar.value = progress
                    self._page.run_task(_do_progress)
                if update.get("error"):
                    async def _do_error(_=None):
                        self.progress_bar.color = theme.ERROR
                    self._page.run_task(_do_error)
                self._page.run_task(self._scroll_log)
        except Exception as ex:
            async def _do_error_log(_=None):
                self.log_view.controls.append(ft.Text(f"[錯誤] {ex}", size=12, color="red"))
                self.progress_bar.color = theme.RED
            self._page.run_task(_do_error_log)
        finally:
            async def _do_finish(_=None):
                self.progress_bar.visible = False
            self._page.run_task(_do_finish)

    async def _scroll_log(self):
        await self.log_view.scroll_to(offset=-1, duration=100)

    @property
    def page(self):
        return self._page