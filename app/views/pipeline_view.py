"""模組流水線翻譯打包視圖 - 移植自 Arnold 0.28.3

用途：
- 提供模組流水線翻譯打包工作台 UI
- 包含：Mod 來源/輸出目錄選擇、任務執行按鈕、API 設定
"""

import flet as ft

from app.ui.theme import (
    BLUE_600, BLUE_700, GREEN_700, TEAL_700, PURPLE_700,
    YELLOW_900, YELLOW, CYAN_400, CYAN_700, GREY_500, GREY_600,
    RED_400, ORANGE_700, WHITE, BLUE_50, GREY_200, BLUE_400,
    GREEN_600, GREEN_50, RED_50,
)


# =============================================================================
# PipelineStepChip - 步驟狀態晶片
# =============================================================================

class PipelineStepChip:
    """單一步驟狀態晶片"""

    def __init__(self, name: str, step_num: int):
        self.name = name
        self.step_num = step_num
        self.status = "waiting"

        self.chip = ft.Chip(
            label=ft.Text(f"{step_num}. {name}"),
            bgcolor=GREY_200,
        )
        self.icon = ft.Icon(ft.Icons.CIRCLE, size=12, color=GREY_500)
        self.chip.leading = self.icon
        self._update_chip()

    def _update_chip(self):
        colors = {
            "waiting": (GREY_500, GREY_200),
            "running": (BLUE_400, BLUE_50),
            "done": (GREEN_600, GREEN_50),
            "failed": (RED_400, RED_50),
        }
        icons = {
            "waiting": ft.Icons.CIRCLE,
            "running": ft.Icons.PENDING,
            "done": ft.Icons.CHECK_CIRCLE,
            "failed": ft.Icons.ERROR,
        }
        color, bg = colors.get(self.status, (GREY_500, GREY_200))
        self.icon.name = icons[self.status]
        self.icon.color = color
        self.chip.bgcolor = bg

    def set_status(self, status: str):
        self.status = status
        self._update_chip()


# =============================================================================
# PipelineProgressPanel - 日誌+進度面板
# =============================================================================

class PipelineProgressPanel:
    """日誌+進度面板，顯示步驟狀態晶片、進度條、即時日誌"""

    def __init__(self, page: ft.Page):
        self._page = page
        self.steps = [
            PipelineStepChip("抽取資源", 1),
            PipelineStepChip("語系比對", 2),
            PipelineStepChip("啟動翻譯", 3),
            PipelineStepChip("打包資源", 4),
        ]
        self.current_step = None

        self.step_row = ft.Row(
            controls=[
                ft.Container(content=self.steps[0].chip, padding=5),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=GREY_500),
                ft.Container(content=self.steps[1].chip, padding=5),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=GREY_500),
                ft.Container(content=self.steps[2].chip, padding=5),
                ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=GREY_500),
                ft.Container(content=self.steps[3].chip, padding=5),
            ],
            spacing=5,
        )

        self.current_label = ft.Text("等待執行...", color=GREY_600, size=14)
        self.log_view = ft.ListView(height=120, spacing=3, auto_scroll=True)

        self.container = ft.Container(
            content=ft.Column([
                ft.Text("執行進度", weight="bold", color=BLUE_700),
                self.step_row,
                self.current_label,
                ft.Divider(),
                ft.Row([ft.Icon(ft.Icons.INFO, size=14, color=GREY_500),
                        ft.Text("步驟日誌：", size=12, color=GREY_600)]),
                self.log_view,
            ], spacing=5),
            padding=12,
            bgcolor=BLUE_50,
            border_radius=10,
            visible=False,
        )

    def start(self):
        self.container.visible = True
        for step in self.steps:
            step.set_status("waiting")

    def set_step_running(self, step_num: int, name: str):
        self.current_step = step_num - 1
        for i, step in enumerate(self.steps):
            if i < self.current_step:
                step.set_status("done")
            elif i == self.current_step:
                step.set_status("running")
            else:
                step.set_status("waiting")
        self.current_label.value = f"目前的：{name}"

    def add_log(self, msg: str, is_success: bool | None = None):
        color = GREEN_600 if is_success == True else (RED_400 if is_success == False else CYAN_700)
        self.log_view.controls.append(
            ft.Text(f">> {msg}", color=color, size=12, font_family="Consolas")
        )
        self._page.update()

    def finish_step(self, step_num: int, success: bool):
        self.steps[step_num - 1].set_status("done" if success else "failed")

    def finish_all(self, success: bool):
        if success:
            self.current_label.value = "✅ 一鍵製作完成！"
        else:
            self.current_label.value = "❌ 流程失敗"
        for step in self.steps:
            if step.status == "running":
                step.set_status("failed" if not success else "done")

    def hide(self):
        self.container.visible = False

    def clear_logs(self):
        self.log_view.controls.clear()


class PipelineView(ft.Column):
    """模組流水線翻譯打包工作台視圖"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker

        self.input_path_text = ft.Text("尚未選擇讀取來源...", color=GREY_600, size=12)
        self.output_path_text = ft.Text("尚未選擇輸出目的地...", color=GREY_600, size=12)
        self.log_content = ft.ListView(expand=True, spacing=5, auto_scroll=True)
        self.progress_bar = ft.ProgressBar(width=float("inf"), height=8, value=0, color=CYAN_400, bgcolor="#E0E0E0")
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=GREY_600)
        self.keys_container = ft.Column(spacing=10)

        self.progress_panel = PipelineProgressPanel(page)

        self._build_ui()

    def _add_log(self, msg, is_err=False):
        self.log_content.controls.append(
            ft.Text(f">> {msg}", color=RED_400 if is_err else CYAN_700, size=12, font_family="Consolas")
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
            border_color=BLUE_700
        )
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE,
            icon_color=RED_400,
            on_click=lambda _: self._delete_key_field(new_row)
        )
        new_row.controls = [key_tf, del_btn]
        self.keys_container.controls.append(new_row)
        self._page.update()

    def _show_progress_panel(self):
        """顯示進度面板並清除舊日誌"""
        self.progress_panel.clear_logs()
        self.progress_panel.start()
        self._page.update()

    def _on_extract_click(self, e=None):
        self._show_progress_panel()
        self.progress_panel.add_log("▶ 開始：抽取資源")
        self.progress_panel.finish_step(1, True)

    def _on_merge_click(self, e=None):
        self._show_progress_panel()
        self.progress_panel.add_log("▶ 開始：語系比對")
        self.progress_panel.finish_step(2, True)

    def _on_translate_click(self, e=None):
        self._show_progress_panel()
        self.progress_panel.add_log("▶ 開始：啟動翻譯")
        self.progress_panel.finish_step(3, True)

    def _on_bundle_click(self, e=None):
        self._show_progress_panel()
        self.progress_panel.add_log("▶ 開始：打包資源")
        self.progress_panel.finish_step(4, True)

    def _on_one_click_click(self, e=None):
        self._show_progress_panel()
        self.progress_panel.add_log("▶ 開始：一鍵製作")
        self.progress_panel.set_step_running(1, "抽取資源")

    def _build_ui(self):
        self.workbench_view = ft.Column([
            ft.Text("翻譯工作台", size=24, weight="bold", color=BLUE_700),

            ft.Container(
                content=ft.Column([
                    ft.Text("1. 基礎與打包配置", weight="bold", color=BLUE_600),
                    ft.Row([
                        ft.Button("Mod 來源", icon=ft.Icons.FOLDER, on_click=lambda _: self._page.run_task(self._pick_input_dir)),
                        ft.Container(content=self.input_path_text, expand=True)
                    ]),
                    ft.Row([
                        ft.Button("輸出目錄", icon=ft.Icons.FOLDER_SPECIAL, on_click=lambda _: self._page.run_task(self._pick_output_dir)),
                        ft.Container(content=self.output_path_text, expand=True)
                    ]),
                ], spacing=10),
                bgcolor="surfaceContainerLow", padding=20, border_radius=15
            ),

            self.progress_panel.container,

            ft.Text("2. 執行任務", weight="bold", color=BLUE_600),
            ft.Column([
                ft.Row([
                    ft.Button("抽取資源", icon=ft.Icons.UNARCHIVE, expand=True, height=55, bgcolor=GREEN_700, color=WHITE, on_click=self._on_extract_click),
                    ft.Button("語系比對", icon=ft.Icons.SEARCH, expand=True, height=55, bgcolor=TEAL_700, color=WHITE, on_click=self._on_merge_click),
                    ft.Button("啟動翻譯", icon=ft.Icons.AUTO_AWESOME, expand=True, height=55, bgcolor=BLUE_700, color=WHITE, on_click=self._on_translate_click),
                    ft.Button("打包資源", icon=ft.Icons.INVENTORY_2, expand=True, height=55, bgcolor=PURPLE_700, color=WHITE, on_click=self._on_bundle_click),
                ], spacing=10),

                ft.Container(
                    content=ft.Column([
                        ft.Row([ft.Icon(ft.Icons.INFO, size=14, color=GREY_500), self.progress_status]),
                        self.progress_bar
                    ], spacing=5),
                    padding=5,
                ),

                ft.Button(
                    "一鍵製作 (自動執行所有流程)",
                    icon=ft.Icons.FLASH_ON,
                    width=float("inf"),
                    height=35,
                    bgcolor=YELLOW_900,
                    color=YELLOW,
                    on_click=self._on_one_click_click,
                ),
            ], spacing=15, expand=True)
        ])

        self.api_view = ft.Column([
            ft.Text("API 金鑰管理", size=24, weight="bold", color=ORANGE_700),
            ft.Container(content=self.keys_container, expand=True),
            ft.Button("儲存設定", icon=ft.Icons.SAVE, bgcolor=BLUE_700, color=WHITE),
        ], spacing=10, expand=True)

        self.controls.append(self.workbench_view)