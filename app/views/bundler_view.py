"""app/views/bundler_view.py 模組。

用途：提供打包成品資源包的 UI 與執行流程。
"""

import json
import logging
import os
import threading

import flet as ft

from app import config_store
from app.services_impl.config_service import load_config_json
from app.services_impl.pipelines.bundle_service import bundle_outputs_generator
from app.ui import design, kit
from app.ui.design import C
from app.ui.mc_text import mc_text_spans
from app.ui.snack import show_snack
from app.ui.ui_batcher import UiBatcher
from app.views._log import LogView
from app.views.bundler.bundler_widgets import BundlerWidgetsMixin
from translation_tool.utils.log_unit import log_debug

OUTPUT_ZIP_NAME_PATH = "output_bundler.output_zip_name"


class BundlerView(BundlerWidgetsMixin, ft.Column):
    page: ft.Page
    file_picker: ft.FilePicker

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=16)
        self._page = page
        self.file_picker = file_picker
        self.extra_folders: list[str] = []
        self.version_data: dict = {}
        self._bundling_running = False

        self.version_search = kit.text_field(
            hint="輸入版本關鍵字...",
            icon=ft.Icons.SEARCH,
            expand=True,
            on_change=self._on_version_search_change,
        )
        self.version_list = ft.ListView(
            expand=True,
            height=200,
            spacing=2,
            auto_scroll=False,
        )
        self.version_expanded = False
        self.description_field = kit.text_field(
            hint="直接輸入文字，或使用 § 顏色代碼",
            expand=True,
            on_change=self._on_meta_change,
        )
        self.pack_image_field = kit.text_field(
            hint="選擇 pack.png 圖片（可選）",
            mono=True,
            expand=True,
            on_change=self._on_meta_change,
        )
        self.root_dir_field = kit.text_field(
            hint="包含所有翻譯產出的最上層資料夾",
            mono=True,
            expand=True,
            on_change=self._on_root_dir_change,
        )
        self.output_zip_field = kit.text_field(
            hint="留空則自動帶入翻譯專案根目錄+設定檔檔名",
            mono=True,
            expand=True,
        )
        self._config_output_zip_name = "可使用翻譯.zip"
        self.extra_folders_view = ft.ListView(spacing=6, auto_scroll=False)
        self.progress_bar = kit.progress_bar(0, "em", height=8)
        self.progress_bar.visible = False
        self.status_text = ft.Text(
            "準備就緒", size=13, weight=ft.FontWeight.W_600, color=C.TEXT
        )
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器 + cyan400 bug）
        self.log_view = LogView(
            page=self._page,
            mode="tail",
            tail_lines=250,
        )

        self._load_version_data()
        self._init_ui()
        self._load_output_zip_from_config()
        self._build_controls()

    def _load_output_zip_from_config(self):
        """從 config 載入 output_zip_name 並設定 hint_text"""
        config = load_config_json()
        self._config_output_zip_name = config.get("output_bundler", {}).get(
            "output_zip_name", "可使用翻譯.zip"
        )
        self.output_zip_field.hint_text = (
            f"留空則自動帶入：{{root_dir}}\\{self._config_output_zip_name}"
        )

    def did_mount(self):
        """頁面加入畫面時：重讀設定，並訂閱存檔事件，讓輸出檔名提示即時跟著更新（#117）。"""
        self._refresh_output_zip_hint()
        self._unsubscribe_config = config_store.subscribe_paths(self._on_config_paths)

    def will_unmount(self):
        unsubscribe = getattr(self, "_unsubscribe_config", None)
        self._unsubscribe_config = None
        if unsubscribe is not None:
            unsubscribe()

    def _on_config_paths(self, changed_paths) -> None:
        """設定存檔事件（可能在任何執行緒）：只排程，真正的更新在 page event loop 上做。"""
        if OUTPUT_ZIP_NAME_PATH not in changed_paths:
            return
        run_task = getattr(self._page, "run_task", None)
        if run_task is None:
            return
        try:
            run_task(self._refresh_output_zip_hint_async)
        except Exception:
            logging.getLogger(__name__).debug("無法排程打包頁提示更新", exc_info=True)

    async def _refresh_output_zip_hint_async(self) -> None:
        self._refresh_output_zip_hint()

    def _refresh_output_zip_hint(self) -> None:
        try:
            self._load_output_zip_from_config()
            self._on_root_dir_change(None)  # 依目前是否已填根目錄重算提示文字
            self.update()
        except Exception:
            logging.getLogger(__name__).debug("重新整理打包頁提示失敗", exc_info=True)

    def _on_root_dir_change(self, e: ft.ControlEvent):
        """當翻譯專案根目錄變更時，更新 output_zip_field 的 hint_text"""
        root_dir = self.root_dir_field.value or ""
        if root_dir and not self.output_zip_field.value:
            self.output_zip_field.hint_text = (
                f"留空則自動帶入：{root_dir}\\{self._config_output_zip_name}"
            )
        elif not self.output_zip_field.value:
            self.output_zip_field.hint_text = (
                f"留空則自動帶入：{{root_dir}}\\{self._config_output_zip_name}"
            )

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
            except Exception:  # noqa: BLE001
                self.version_data = {}
        else:
            self.version_data = {}

    def _init_ui(self):
        self._refresh_version_list("")

    def _refresh_version_list(self, search_text: str):
        self.version_list.controls.clear()
        filtered = [v for v in self.version_data if search_text.lower() in v.lower()]
        if not filtered:
            self.version_list.controls.append(
                ft.Container(
                    content=ft.Text(
                        "找不到符合的版本" if search_text else "無可用版本",
                        size=12,
                        color=C.DIM,
                    ),
                    padding=8,
                )
            )
        for version_key in filtered:
            selected = version_key == self.version_search.value
            info = self.version_data.get(version_key, {})
            item = ft.Container(
                content=ft.Row(
                    [
                        ft.Text(
                            version_key,
                            size=13,
                            color=C.EM if selected else C.TEXT,
                            weight=ft.FontWeight.W_600 if selected else None,
                            expand=True,
                        ),
                        ft.Text(
                            self._format_range(info),
                            size=11.5,
                            color=C.DIM,
                            font_family=design.FONT_MONO,
                        ),
                        *(
                            [ft.Icon(ft.Icons.CHECK, size=14, color=C.EM)]
                            if selected
                            else []
                        ),
                    ],
                    spacing=8,
                ),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                border_radius=8,
                bgcolor=C.EM_BG if selected else None,
                ink=True,
                on_click=lambda e, v=version_key: self._select_version(v),
            )
            self.version_list.controls.append(item)
        self._page.update()

    @staticmethod
    def _format_range(info: dict) -> str:
        """版本資料 → 「format 9–15」這種顯示字串。"""
        low, high = info.get("min_format"), info.get("max_format")
        if low is None and high is None:
            return ""
        return f"format {low}" if low == high else f"format {low}–{high}"

    def _on_version_search_change(self, e: ft.ControlEvent):
        self._refresh_version_list(e.control.value or "")

    def _select_version(self, version: str):
        log_debug(f"_select_version called: {version}")
        self.version_search.value = version
        self.version_expanded = False
        self._version_toggle_label.value = version
        self.version_dropdown_container_ref.visible = False
        self._version_toggle_format.value = self._format_range(
            self.version_data.get(version, {})
        )
        self._version_toggle_icon.name = ft.Icons.EXPAND_MORE
        self._update_preview()
        self._page.update()

    def _toggle_version_expand(self, e: ft.ControlEvent):
        self.version_expanded = not self.version_expanded
        log_debug(f"_toggle_version_expand: version_expanded={self.version_expanded}")
        self.version_dropdown_container_ref.visible = self.version_expanded
        self._version_toggle_icon.name = (
            ft.Icons.EXPAND_LESS if self.version_expanded else ft.Icons.EXPAND_MORE
        )
        if self.version_expanded:
            self._refresh_version_list(self.version_search.value or "")
        self._page.update()

    # --- pack.mcmeta / 描述預覽 ---
    def _on_meta_change(self, e: ft.ControlEvent):
        self._update_preview()
        self._page.update()

    def mcmeta_text(self) -> str:
        """目前設定會寫進 pack.mcmeta 的內容（跟 output_bundler 寫入的格式一致）。"""
        info = self.version_data.get(self.version_search.value or "", {})
        pack = {
            "pack": {
                "description": self.description_field.value or "",
                "min_format": str(info.get("min_format", 0)),
                "max_format": str(info.get("max_format", 0)),
            }
        }
        return json.dumps(pack, ensure_ascii=False, indent=2)

    def _update_preview(self):
        """依描述 / 版本 / 圖片更新右側預覽。"""
        description = self.description_field.value or ""
        self.preview_title.spans = mc_text_spans(
            description, default_color=C.TEXT, size=14
        ) or [ft.TextSpan("（尚未輸入描述）", style=ft.TextStyle(color=C.DIM, size=13))]
        self.preview_format.value = self._format_range(
            self.version_data.get(self.version_search.value or "", {})
        )
        self.mcmeta_view.value = self.mcmeta_text()
        image_path = (self.pack_image_field.value or "").strip()
        if image_path and os.path.isfile(image_path):
            self.preview_image.content = ft.Image(
                src=image_path, width=72, height=72, fit=ft.BoxFit.COVER
            )
        else:
            self.preview_image.content = ft.Icon(
                ft.Icons.IMAGE_OUTLINED, size=30, color=C.DIM
            )

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
        result = await self.file_picker.get_directory_path(
            dialog_title="選擇翻譯專案根目錄"
        )
        log_debug(f"_async_pick_root_dir result: {result}")
        if result:
            path = result[0].path if hasattr(result[0], "path") else result
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
            icon = (
                ft.Icons.INSERT_DRIVE_FILE_OUTLINED
                if is_file
                else ft.Icons.FOLDER_OUTLINED
            )
            self.extra_folders_view.controls.append(
                ft.Container(
                    padding=ft.Padding(left=12, top=4, right=4, bottom=4),
                    bgcolor=C.PANEL2,
                    border=ft.Border.all(1, C.LINE),
                    border_radius=design.RADIUS_CONTROL,
                    content=ft.Row(
                        [
                            ft.Icon(icon, size=16, color=C.MUTED),
                            ft.Text(
                                path,
                                expand=True,
                                size=12.5,
                                font_family=design.FONT_MONO,
                                no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                icon_size=16,
                                icon_color=C.MUTED,
                                tooltip="移除",
                                on_click=lambda e, p=path: self._remove_extra_folder(p),
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )

    def _remove_extra_folder(self, path: str):
        if path in self.extra_folders:
            self.extra_folders.remove(path)
            self._refresh_extra_folders()
            self._page.update()

    def start_bundling_clicked(self, e: ft.ControlEvent):
        if self._bundling_running:
            show_snack(self.page, "打包正在執行中，請等待完成")
            return

        root_dir = self.root_dir_field.value or ""
        output_zip = self.output_zip_field.value or ""

        if not root_dir:
            show_snack(self.page, "請填寫「翻譯專案根目錄」")
            return

        if not output_zip:
            # 使用時才讀設定：存檔後的新檔名立刻生效（#117）
            self._load_output_zip_from_config()
            output_zip = os.path.join(root_dir, self._config_output_zip_name)

        version = self.version_search.value or ""
        description = self.description_field.value or ""
        pack_image = self.pack_image_field.value or ""

        self._bundling_running = True
        self.start_button.disabled = True
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.log_view.clear()
        self.status_text.value = "打包中…"
        self.status_text.color = C.TEXT
        self.progress_bar.color = C.EM
        self._append_log("開始執行打包...", level="info")
        self._page.update()

        thread = threading.Thread(
            target=self._bundling_worker,
            args=(root_dir, output_zip, version, description, pack_image),
            daemon=True,
        )
        thread.start()

    def _append_log(self, msg: str, level: str = "info"):
        """新增一行日誌（直接走 LogView.add）。

        PR refactor/unified-log-view: 取代原本的 color='cyan400' bug。
        顏色由 LogView 根據 level 自動從 theme 取。
        """
        self.log_view.add(msg, level=level)

    # 背景打包時，日誌/進度以此間隔批次推到畫面
    _UI_FLUSH_INTERVAL_SEC = 0.2

    def _apply_bundling_ui(self, lines: list[tuple[str, str]], state: dict):
        """（event loop 上）套用一批日誌與進度並刷新畫面。"""
        if lines:
            self.log_view.add_many(lines)
        if state.get("progress") is not None:
            self.progress_bar.value = state["progress"]
        if state.get("error_color"):
            self.progress_bar.color = state["error_color"]
        if state.get("done"):
            self.progress_bar.visible = False
        self.start_button.disabled = self._bundling_running
        if state.get("done"):
            failed = state.get("error_color") is not None
            self.status_text.value = "打包失敗，請查看日誌" if failed else "打包完成"
            self.status_text.color = C.RED if failed else C.EM
        self._page.update()

    def _bundling_worker(self, root_dir, output_zip, version, description, pack_image):
        # 節流 + 背壓：背景執行緒只累積資料，UI 更新交給 event loop
        batcher = UiBatcher(
            self._page, self._apply_bundling_ui, interval=self._UI_FLUSH_INTERVAL_SEC
        )
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
                batcher.add_lines(
                    [(line, "info") for line in log_msg.split("\n") if line.strip()]
                )
                if "progress" in update:
                    batcher.set_state(progress=update["progress"])
                if update.get("error"):
                    batcher.set_state(error_color=C.RED)
                batcher.flush()
        except Exception as ex:  # noqa: BLE001 - 背景執行緒邊界，錯誤顯示於日誌
            batcher.add_lines([(f"[錯誤] {ex}", "error")])
            batcher.set_state(error_color=C.RED)
        finally:
            self._bundling_running = False
            batcher.set_state(done=True)
            batcher.flush(force=True)

    @property
    def page(self):
        return self._page
