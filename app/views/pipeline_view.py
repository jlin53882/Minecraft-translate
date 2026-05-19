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
from app.logging.task_session import TaskSession
from translation_tool.utils.config_manager import load_config
from app.services_impl.pipelines.extract_service import (
    run_lang_extraction_service,
    run_book_extraction_service,
)
from app.views.extractor.extractor_state import PreviewState
from translation_tool.core.jar_processor import preview_extraction_generator, find_jar_files
from translation_tool.core.jar_processor_preview import ExtractionSummary
import threading
import time
import os


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

    def _show_snack_bar(self, message: str, color: str = RED_400):
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self._page.overlay.append(snack)
        snack.open = True
        self._page.update()

    # =============================================================================
    # 抽取資源 Dialog
    # =============================================================================

    def _open_extract_dialog(self, e=None):
        """打開抽取資源設定對話框"""
        mods_dir = (self.input_path_text.value or "").strip()
        output_dir = (self.output_path_text.value or "").strip()

        cfg = load_config()
        lang_codes = cfg.get("jar_extractor", {}).get("lang_codes", ["en_us", "zh_cn", "zh_tw"])

        self._extract_mods_field = ft.TextField(
            label="Mod 來源",
            hint_text=f"自動帶入：{mods_dir}" if mods_dir else "留空使用上方設定的 Mod 來源",
            value=mods_dir,
            expand=True,
            border_color=BLUE_700,
        )
        self._extract_output_field = ft.TextField(
            label="輸出目錄",
            hint_text=f"自動帶入：{output_dir}" if output_dir else "留空使用上方設定的輸出目錄",
            value=output_dir,
            expand=True,
            border_color=BLUE_700,
        )
        self._extract_mode = ft.RadioGroup(
            content=ft.Column([
                ft.Radio("提取 Lang", "lang"),
                ft.Radio("提取 Book", "book"),
                ft.Radio("全部執行（Lang + Book）", "both"),
            ], spacing=4),
            on_change=lambda e: print(f"[DEBUG] RadioGroup on_change: value={self._extract_mode.value}"),
        )

        lang_code_checks = {}
        for code in lang_codes:
            lang_code_checks[code] = ft.Checkbox(label=code, value=True)

        def pick_mods_dir(e=None):
            async def do_pick():
                result = await self.file_picker.get_directory_path()
                if result:
                    self._extract_mods_field.value = result
                    self._page.update()
            self._page.run_task(do_pick)

        def browse_mods_dir(e=None):
            async def do_pick():
                result = await self.file_picker.get_directory_path()
                if result:
                    self._extract_mods_field.value = result
                    self._page.update()
            self._page.run_task(do_pick)

        def pick_output_dir(e=None):
            async def do_pick():
                result = await self.file_picker.get_directory_path()
                if result:
                    self._extract_output_field.value = result
                    self._page.update()
            self._page.run_task(do_pick)

        def browse_output_dir(e=None):
            async def do_pick():
                result = await self.file_picker.get_directory_path()
                if result:
                    self._extract_output_field.value = result
                    self._page.update()
            self._page.run_task(do_pick)

        def close_dialog(dialog):
            dialog.open = False
            self._page.update()

        def show_preview_result(dialog):
            mods = (self._extract_mods_field.value or "").strip()
            if not mods or not os.path.isdir(mods):
                self._show_snack_bar("⚠️ 請選擇有效的 Mod 來源")
                return

            mode = self._extract_mode.value or "lang"
            jar_files = find_jar_files(mods)
            total_jars = len(jar_files)

            preview_state = PreviewState()
            preview_state.total = total_jars
            preview_state.current = 0

            def do_preview():
                try:
                    for update in preview_extraction_generator(mods, mode):
                        if 'error' in update:
                            preview_state.error = update['error']
                            preview_state.done = True
                            break
                        preview_state.progress = update.get('progress', 0)
                        preview_state.current = update.get('current', 0)
                        preview_state.total = update.get('total', 0)
                        if 'result' in update:
                            preview_state.result = update['result']
                            preview_state.done = True
                except Exception as ex:
                    preview_state.error = str(ex)
                    preview_state.done = True

            threading.Thread(target=do_preview, daemon=True).start()

            preview_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("預覽結果"),
                content=ft.Text(f"預覽掃描中...（0/{total_jars}）"),
                actions=[ft.TextButton("取消", on_click=lambda e: close_preview_dialog(preview_dialog))],
            )

            def close_preview_dialog(d):
                d.open = False
                self._page.update()

            self._page.overlay.append(preview_dialog)
            preview_dialog.open = True
            self._page.update()

            def poll_preview():
                while not preview_state.done:
                    time.sleep(0.2)
                    async def do_update(_):
                        pct = int(preview_state.progress * 100)
                        preview_dialog.content = ft.Text(f"預覽掃描中...（{preview_state.current}/{preview_state.total}）{pct}%")
                        self._page.update()
                    self._page.run_task(do_update, None)

                async def do_final(_):
                    if preview_state.error:
                        preview_dialog.content = ft.Text(f"預覽錯誤：{preview_state.error}")
                    elif preview_state.result:
                        result = preview_state.result
                        jar_count = total_jars
                        total_files = result.get('total_files', 0)
                        preview_dialog.content = ft.Column([
                            ft.Text(f"JAR 數量：{jar_count} 個"),
                            ft.Text(f"預計提取：{total_files} 個語言檔案"),
                            ft.Divider(),
                            ft.Text("詳細清單：", weight="bold"),
                            ft.ListView(height=200, expand=True),
                        ], tight=False)
                    preview_dialog.actions = [ft.TextButton("確定", on_click=lambda e: close_preview_dialog(preview_dialog))]
                    self._page.update()
                self._page.run_task(do_final, None)

            threading.Thread(target=poll_preview, daemon=True).start()

        def start_extraction(dialog):
            mods = (self._extract_mods_field.value or "").strip()
            output = (self._extract_output_field.value or "").strip()
            mode = self._extract_mode.value or "lang"

            if not mods:
                self._show_snack_bar("⚠️ Mod 來源為必填欄位")
                return
            if not os.path.isdir(mods):
                self._show_snack_bar("⚠️ Mod 來源資料夾不存在")
                return
            if not output:
                self._show_snack_bar("⚠️ 輸出目錄為必填欄位")
                return
            if not os.path.isdir(output):
                self._show_snack_bar("⚠️ 輸出目錄不存在")
                return

            close_dialog(dialog)
            self._run_extraction(mods, output, mode)

        lang_codes_section = ft.Column([lang_code_checks[code] for code in lang_codes], spacing=2)

        content = ft.Column([
            ft.Text("Mod 來源", weight="bold", size=13),
            ft.Row([
                self._extract_mods_field,
                ft.Button("選擇資料夾", icon=ft.Icons.FOLDER, on_click=pick_mods_dir),
                ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_mods_dir),
            ]),
            ft.Text("輸出目錄", weight="bold", size=13),
            ft.Row([
                self._extract_output_field,
                ft.Button("選擇資料夾", icon=ft.Icons.FOLDER_SPECIAL, on_click=pick_output_dir),
                ft.Button("瀏覽", icon=ft.Icons.SEARCH, on_click=browse_output_dir),
            ]),
            ft.Text("輸出說明：", weight="bold", size=13),
            ft.Text("→ {output}/jar_mod_extract/_提取lang_輸出/（Lang 模式）", color=GREY_600, size=12),
            ft.Text("→ {output}/jar_mod_extract/_提取book_輸出/（Book 模式）", color=GREY_600, size=12),
            ft.Divider(),
            ft.Text("執行模式", weight="bold", size=13),
            self._extract_mode,
            ft.Divider(),
            ft.Text("語言代碼（動態）", weight="bold", size=13),
            lang_codes_section,
        ], spacing=10, tight=False)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("📦 抽取資源設定"),
            content=ft.Container(content=content, width=550),
            actions=[
                ft.TextButton("取消", on_click=lambda e: close_dialog(dialog)),
                ft.OutlinedButton("預覽結果", icon=ft.Icons.PREVIEW, on_click=lambda e: show_preview_result(dialog)),
                ft.Button("確定執行", icon=ft.Icons.CHECK, bgcolor=GREEN_700, color=WHITE,
                          on_click=lambda e: start_extraction(dialog)),
            ],
        )

        self._page.overlay.append(dialog)
        dialog.open = True
        self._page.update()

    def _run_extraction(self, mods_dir: str, output_dir: str, mode: str):
        """執行抽取資源（背景執行緒）"""
        session = TaskSession()
        self.progress_panel.set_step_running(1, "抽取資源")
        self.progress_panel.add_log(f"▶ 開始：抽取資源（{mode}）")

        def worker():
            try:
                os.makedirs(os.path.join(output_dir, "jar_mod_extract", "_提取lang_輸出"), exist_ok=True)
                os.makedirs(os.path.join(output_dir, "jar_mod_extract", "_提取book_輸出"), exist_ok=True)

                if mode in ("lang", "both"):
                    lang_out = os.path.join(output_dir, "jar_mod_extract", "_提取lang_輸出")
                    run_lang_extraction_service(mods_dir, lang_out, session)
                    async def do_lang_log(_):
                        self.progress_panel.add_log("✅ Lang 抽取完成")
                    self._page.run_task(do_lang_log, None)

                if mode in ("book", "both"):
                    book_out = os.path.join(output_dir, "jar_mod_extract", "_提取book_輸出")
                    run_book_extraction_service(mods_dir, book_out, session)
                    async def do_book_log(_):
                        self.progress_panel.add_log("✅ Book 抽取完成")
                    self._page.run_task(do_book_log, None)

                async def do_finish(_):
                    self.progress_panel.finish_step(1, not session.error)
                self._page.run_task(do_finish, None)

            except Exception as ex:
                async def do_err_log(_):
                    self.progress_panel.add_log(f"❌ 錯誤：{ex}", False)
                self._page.run_task(do_err_log, None)
                async def do_err_finish(_):
                    self.progress_panel.finish_step(1, False)
                self._page.run_task(do_err_finish, None)

        threading.Thread(target=worker, daemon=True).start()
        self._poll_session(session)

    def _poll_session(self, session: TaskSession):
        """輪詢 session 直到完成"""
        def poll():
            while session.status in ("RUNNING", "IDLE"):
                time.sleep(0.5)
                snap = session.snapshot()
                progress = float(snap.get("progress", 0) or 0)
                async def do_progress(_):
                    self._update_progress(progress, f"{int(progress * 100)}%")
                self._page.run_task(do_progress, None)
                for log_entry in snap.get("logs", []):
                    async def do_log(_, le=log_entry):
                        self.progress_panel.add_log(le.text)
                    self._page.run_task(do_log, None)
            async def do_finish(_):
                self._update_progress(1.0, "完成")
            self._page.run_task(do_finish, None)

        threading.Thread(target=poll, daemon=True).start()

    def _on_extract_click(self, e=None):
        self._open_extract_dialog()

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