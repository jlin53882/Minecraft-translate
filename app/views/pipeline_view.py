"""模組流水線翻譯打包視圖 - 移植自 Arnold 0.28.3

用途：
- 提供模組流水線翻譯打包工作台 UI
- 包含：Mod 來源/輸出目錄選擇、任務執行按鈕、API 設定
"""

import flet as ft


class PipelineView(ft.Column):
    """模組流水線翻譯打包工作台視圖"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker

        self.input_path_text = ft.Text("尚未選擇讀取來源...", color="grey600", size=12)
        self.output_path_text = ft.Text("尚未選擇輸出目的地...", color="grey600", size=12)
        self.log_content = ft.ListView(expand=True, spacing=5, auto_scroll=True)
        self.progress_bar = ft.ProgressBar(width=float("inf"), height=8, value=0, color="cyan400", bgcolor="#E0E0E0")
        self.progress_status = ft.Text("等待任務啟動...", size=12, color="grey600")
        self.keys_container = ft.Column(spacing=10)

        self._build_ui()

    def _add_log(self, msg, is_err=False):
        self.log_content.controls.append(
            ft.Text(f">> {msg}", color="red400" if is_err else "cyan700", size=12, font_family="Consolas")
        )
        self._page.update()

    def _update_progress(self, val, text):
        self.progress_bar.value = val
        self.progress_status.value = text
        self._page.update()

    async def _pick_input_dir(self, e=None):
        result = await self.file_picker.get_directory_path()
        if result:
            self.input_path_text.value = result
            self._page.update()

    async def _pick_output_dir(self, e=None):
        result = await self.file_picker.get_directory_path()
        if result:
            self.output_path_text.value = result
            self._page.update()

    def _delete_key_field(self, row_obj):
        self.keys_container.controls.remove(row_obj)
        self._page.update()

    def _add_key_field(self, initial_value=""):
        new_row = ft.Row(spacing=10)
        key_tf = ft.TextField(
            value=initial_value,
            label=f"API Key {len(self.keys_container.controls) + 1}",
            expand=True,
            text_size=12,
            border_color="blue700"
        )
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE,
            icon_color="red400",
            on_click=lambda _: self._delete_key_field(new_row)
        )
        new_row.controls = [key_tf, del_btn]
        self.keys_container.controls.append(new_row)
        self._page.update()

    def _build_ui(self):
        self.workbench_view = ft.Column([
            ft.Text("翻譯工作台", size=24, weight="bold", color="blue700"),

            ft.Container(
                content=ft.Column([
                    ft.Text("1. 基礎與打包配置", weight="bold", color="blue600"),
                    ft.Row([
                        ft.ElevatedButton("Mod 來源", icon=ft.Icons.FOLDER, on_click=lambda _: self._page.run_task(self._pick_input_dir)),
                        ft.Container(content=self.input_path_text, expand=True)
                    ]),
                    ft.Row([
                        ft.ElevatedButton("輸出目錄", icon=ft.Icons.FOLDER_SPECIAL, on_click=lambda _: self._page.run_task(self._pick_output_dir)),
                        ft.Container(content=self.output_path_text, expand=True)
                    ]),
                ], spacing=10),
                bgcolor="surfaceContainerLow", padding=20, border_radius=15
            ),

            ft.Text("2. 執行任務", weight="bold", color="blue600"),
            ft.Column([
                ft.Row([
                    ft.ElevatedButton("抽取資源", icon=ft.Icons.UNARCHIVE, expand=True, height=55, bgcolor="green700", color="white"),
                    ft.ElevatedButton("語系比對", icon=ft.Icons.SEARCH, expand=True, height=55, bgcolor="teal700", color="white"),
                    ft.ElevatedButton("啟動翻譯", icon=ft.Icons.AUTO_AWESOME, expand=True, height=55, bgcolor="blue700", color="white"),
                    ft.ElevatedButton("打包資源", icon=ft.Icons.INVENTORY_2, expand=True, height=55, bgcolor="purple700", color="white"),
                ], spacing=10),

                ft.Container(
                    content=ft.Column([
                        ft.Row([ft.Icon(ft.Icons.INFO, size=14, color="grey500"), self.progress_status]),
                        self.progress_bar
                    ], spacing=5),
                    padding=5,
                ),

                ft.ElevatedButton(
                    "一鍵製作 (自動執行所有流程)",
                    icon=ft.Icons.FLASH_ON,
                    width=float("inf"),
                    height=35,
                    bgcolor=ft.Colors.YELLOW_900,
                    color=ft.Colors.YELLOW,
                ),
            ], spacing=15, expand=True)
        ])

        self.api_view = ft.Column([
            ft.Text("API 金鑰管理", size=24, weight="bold", color="orange700"),
            ft.Container(content=self.keys_container, expand=True),
            ft.ElevatedButton("儲存設定", icon=ft.Icons.SAVE, bgcolor="blue700", color="white"),
        ], spacing=10, expand=True)

        self.controls.append(self.workbench_view)