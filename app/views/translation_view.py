"""app/views/translation_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading
import time

import flet as ft
from app.ui import theme

# UI 共用元件：抽出重複的卡片/按鈕樣式，集中在 app.ui
from app.ui.components import primary_button, secondary_button, styled_card
from app.views.translation.translation_actions import (
    run_ftb,
    run_kjs,
    run_md,
    start_ui_timer as start_translation_ui_timer,
)
from app.views.translation.translation_panels import (
    build_action_row,
    build_ftb_tab,
    build_kjs_tab,
    build_md_tab,
    build_path_row,
)
from app.views.translation.translation_state import TranslationRunState

# 可選匯入：避免某個 service 暫時不可用時，整頁無法開啟
try:
    from app.services_impl.pipelines.ftb_service import run_ftb_translation_service
except Exception:
    run_ftb_translation_service = None

try:
    from app.services_impl.pipelines.kubejs_service import run_kubejs_tooltip_service
except Exception:
    run_kubejs_tooltip_service = None

try:
    from app.services_impl.pipelines.md_service import run_md_translation_service
except Exception:
    run_md_translation_service = None

try:
    from app.task_session import TaskSession
except Exception:
    TaskSession = None

class TranslationView(ft.Column):
    """翻譯工作台：FTB / KubeJS / Markdown 三流程統一入口。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 TranslationView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(expand=True, spacing=16)
        self.page = page
        self.file_picker = file_picker
        self._state = TranslationRunState()
        self._picker_target_field: ft.TextField | None = None

        self.session = None
        self._ui_timer_running = False

        # 右側共用狀態與日誌
        self.status_chip = ft.Chip(
            label=ft.Text("尚未開始"), bgcolor=theme.GREY_200
        )
        self.progress = ft.ProgressBar(
            value=0, height=8, bgcolor=theme.GREY_200, color=theme.BLUE
        )
        self.log_view = ft.ListView(expand=True, spacing=4, auto_scroll=True)

        header = ft.Row(
            [
                ft.Text("Translation Workbench", size=22, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    tooltip="清空右側日誌",
                    on_click=lambda e: self._clear_logs(),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        self.tabs = ft.Tabs(
            selected_index=0,
            expand=True,
            animation_duration=180,
            tabs=[
                ft.Tab(text="FTB Quests", content=self._build_ftb_tab()),
                ft.Tab(text="KubeJS Tooltips", content=self._build_kjs_tab()),
                ft.Tab(text="Markdown", content=self._build_md_tab()),
            ],
        )

        # action layer 讀取的相容 seam
        self.run_ftb_translation_service = run_ftb_translation_service
        self.run_kubejs_tooltip_service = run_kubejs_tooltip_service
        self.run_md_translation_service = run_md_translation_service
        self.TaskSession = TaskSession

        right_panel = ft.Column(
            [
                styled_card(
                    title="執行狀態",
                    icon=ft.Icons.TIMELINE,
                    content=ft.Column(
                        [
                            ft.Row([self.status_chip], wrap=True),
                            self.progress,
                        ],
                        spacing=10,
                    ),
                ),
                styled_card(
                    title="執行日誌",
                    icon=ft.Icons.RECEIPT_LONG,
                    expand=True,
                    content=ft.Container(
                        expand=True,
                        bgcolor="#1e1e1e",
                        border_radius=8,
                        padding=10,
                        content=self.log_view,
                    ),
                ),
            ],
            expand=True,
            spacing=12,
        )

        body = ft.Row(
            [
                ft.Container(
                    expand=2,
                    content=styled_card(
                        title="翻譯流程",
                        icon=ft.Icons.TUNE,
                        expand=True,
                        content=self.tabs,
                    ),
                ),
                ft.Container(expand=1, content=right_panel),
            ],
            expand=True,
            spacing=12,
        )

        self.summary_card = ft.Container(
            padding=14,
            border_radius=10,
            bgcolor=theme.WHITE,
            border=ft.border.all(1, theme.BLACK12),
            content=ft.Row(
                [
                    # ft.Icon(ft.Icons.INFO_OUTLINE, size=18, color=theme.BLUE_GREY_700),
                    # ft.Text("本頁已與 Extractor 風格對齊；僅調整 UI 樣式，不影響流程邏輯。"),
                ],
                spacing=10,
            ),
        )

        self.controls = [header, body, self.summary_card]

        if self.file_picker not in self.page.overlay:
            self.page.overlay.append(self.file_picker)

    # ------------------------------------------------------------------
    # 樣式 helper（集中到 app.ui.components）
    # ------------------------------------------------------------------
    # 本頁原本有 _section_header / _styled_card，現在改用共用的 styled_card / primary_button 等。
    # 目的：
    # - 多個 View 可共用同一套卡片/按鈕樣式
    # - 之後要調 UI 一致性，只需要改 app/ui/components.py

    def _path_row(self, field: ft.TextField) -> ft.Control:
        """建立路徑輸入列 UI"""
        return build_path_row(self, field)

    def _action_row(
        self,
        *,
        on_start,
        on_dry_run,
        on_reset,
        trailing: list[ft.Control] | None = None,
    ) -> ft.Control:
        """建立操作按鈕列 UI"""
        return build_action_row(view=self, on_start=on_start, on_dry_run=on_dry_run, on_reset=on_reset, trailing=trailing)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------
    def _build_ftb_tab(self) -> ft.Control:
        """建立 FTB 翻譯標籤頁"""
        return build_ftb_tab(self)

    def _build_kjs_tab(self) -> ft.Control:
        """建立 KubeJS 翻譯標籤頁"""
        return build_kjs_tab(self)

    def _build_md_tab(self) -> ft.Control:
        """建立 Markdown 翻譯標籤頁"""
        return build_md_tab(self)

    # ------------------------------------------------------------------
    # directory picker
    # ------------------------------------------------------------------
    def _pick_directory_into(self, target: ft.TextField):
        """開啟目錄選擇器並設定目標欄位"""
        self._picker_target_field = target
        self.file_picker.on_result = self._on_dir_picked
        self.file_picker.get_directory_path()

    def _on_dir_picked(self, e: ft.FilePickerResultEvent):
        """目錄選擇後更新目標欄位"""
        if not e.path:
            return
        if self._picker_target_field is not None:
            self._picker_target_field.value = e.path
        self.page.update()

    # ------------------------------------------------------------------
    # runners
    # ------------------------------------------------------------------
    def _run_ftb(self, *, dry_run: bool):
        """執行 FBT 翻譯流程"""
        return run_ftb(self, dry_run=dry_run)

    def _run_kjs(self, *, dry_run: bool):
        """執行 KubeJS 翻譯流程"""
        return run_kjs(self, dry_run=dry_run)

    def _run_md(self, *, dry_run: bool):
        """執行 Markdown 翻譯流程"""
        return run_md(self, dry_run=dry_run)

    # ------------------------------------------------------------------
    # ui poller
    # ------------------------------------------------------------------
    def _start_ui_timer(self):
        """啟動 UI 更新計時器"""
        return start_translation_ui_timer(self)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str):
        """更新狀態晶片的文字與顏色"""
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color
        self.page.update()

    def _append_log(self, line: str):
        """新增一行日誌到日誌檢視區"""
        self.log_view.controls.append(ft.Text(line, size=13, color=theme.GREY_100))
        if len(self.log_view.controls) > 400:
            self.log_view.controls = self.log_view.controls[-300:]
        self.page.update()

    def _clear_logs(self):
        """清除日誌檢視區的所有內容"""
        self.log_view.controls.clear()
        self.page.update()

    # ------------------------------------------------------------------
    # reset actions
    # ------------------------------------------------------------------
    def _reset_ftb_inputs(self):
        """重置 FTB 翻譯的所有輸入欄位"""
        self.ftb_in_dir.value = ""
        self.ftb_out_dir.value = ""
        self.ftb_step_export.value = True
        self.ftb_step_clean.value = True
        self.ftb_step_translate.value = True
        self.ftb_step_inject.value = True
        self.ftb_write_new_cache.value = True
        self._set_status("尚未開始", theme.GREY_200)
        self.progress.value = 0
        self._append_log("[UI] 已重置：FTB Quests 輸入已清空")
        self.page.update()

    def _reset_kjs_inputs(self):
        """重置 KubeJS 翻譯的所有輸入欄位"""
        self.kjs_in_dir.value = ""
        self.kjs_out_dir.value = ""
        self.kjs_step_extract.value = True
        self.kjs_step_translate.value = True
        self.kjs_step_inject.value = True
        self.kjs_write_new_cache.value = True
        self._set_status("尚未開始", theme.GREY_200)
        self.progress.value = 0
        self._append_log("[UI] 已重置：KubeJS 輸入已清空")
        self.page.update()

    def _reset_md_inputs(self):
        """重置 Markdown 翻譯的所有輸入欄位"""
        self.md_in_dir.value = ""
        self.md_out_dir.value = ""
        self.md_step_extract.value = True
        self.md_step_translate.value = True
        self.md_step_inject.value = True
        self.md_write_new_cache.value = True
        self.md_lang_mode.value = "non_cjk_only"
        self._set_status("尚未開始", theme.GREY_200)
        self.progress.value = 0
        self._append_log("[UI] 已重置：Markdown 輸入已清空")
        self.page.update()

    def _show_snack(self, message: str, color: str = theme.RED_600):
        """在頁面顯示 Snack Bar 提示訊息"""
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
