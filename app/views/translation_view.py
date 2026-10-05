"""app/views/translation_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading  # noqa: F401

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.status_chip import apply_status_style, set_chip_status
from app.views._log import LogView
from app.views.translation.translation_actions import (
    resume_ui_timer,
    run_ftb,
    run_kjs,
    run_md,
    stop_ui_timer,
)
from app.views.translation.translation_actions import (
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
except Exception:  # noqa: BLE001
    run_ftb_translation_service = None

try:
    from app.services_impl.pipelines.kubejs_service import run_kubejs_tooltip_service
except Exception:  # noqa: BLE001
    run_kubejs_tooltip_service = None

try:
    from app.services_impl.pipelines.md_service import run_md_translation_service
except Exception:  # noqa: BLE001
    run_md_translation_service = None

try:
    from app.tasks.task_session import TaskSession
except Exception:  # noqa: BLE001
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
        tab_content = self._init_translation_state_and_tabs(file_picker, page)
        right_panel = self._build_translation_status_and_right_panel(tab_content)

        body = ft.Row(
            [
                ft.Container(expand=3, content=self.tabs),
                ft.Container(expand=2, content=right_panel),
            ],
            expand=True,
            spacing=16,
        )

        self.controls = [
            kit.page_header(
                "任務翻譯工具",
                "處理 FTB Quests、KubeJS Tooltip 與 Markdown 文件，步驟可自由勾選",
                icon=ft.Icons.TRANSLATE,
                tone="ench",
            ),
            body,
        ]

    def _init_translation_state_and_tabs(self, file_picker, page):
        """任務翻譯頁的狀態與分頁。"""
        self._page = page
        self.file_picker = file_picker
        self._state = TranslationRunState()
        self._picker_target_field: ft.TextField | None = None
        self._step_cards: list = []  # 面板建立時登記，reset 後用來刷新步驟卡外觀

        self.session = None
        self._ui_timer_running = False
        self._poller = PollerHandle()  # 輪詢的 owner：卸載時 stop、重新掛載時 resume

        # 右側共用狀態與日誌
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"))
        self._apply_status_style("neutral")
        self.cancel_button = kit.button(
            "取消",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            tooltip="在目前批次完成後停止（已翻譯的部分會保留並寫出）",
            on_click=lambda e: self._on_cancel(),
        )
        self.cancel_button.disabled = True
        # 環形進度；.value 介面與 ft.ProgressBar 相同，actions 層不需要改
        self.progress = kit.ProgressRing(0, size=96, stroke=9, tone="em", sub="進度")
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器）
        # tail 模式與既有的「最後 N 筆整批重建」行為一致
        self.log_view = LogView(
            page=self._page,
            mode="tail",
            tail_lines=250,
        )

        self.ftb_tab_content = self._build_ftb_tab()
        self.kjs_tab_content = self._build_kjs_tab()
        self.md_tab_content = self._build_md_tab()

        tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="FTB Quests"),
                ft.Tab(label="KubeJS Tooltips"),
                ft.Tab(label="Markdown"),
            ],
            indicator_color=C.EM,
            label_color=C.EM,
            unselected_label_color=C.MUTED,
            divider_color=C.LINE,
        )
        tab_view = ft.TabBarView(
            controls=[
                self.ftb_tab_content,
                self.kjs_tab_content,
                self.md_tab_content,
            ],
            expand=True,
        )
        tab_content = ft.Column([tab_bar, tab_view], expand=True, spacing=12)
        return tab_content

    def _build_translation_status_and_right_panel(self, tab_content):
        """狀態卡片與右側面板。"""
        self.tabs = ft.Tabs(
            content=tab_content,
            length=3,
            selected_index=0,
            expand=True,
            animation_duration=180,
        )

        # action layer 讀取的相容 seam
        self.run_ftb_translation_service = run_ftb_translation_service
        self.run_kubejs_tooltip_service = run_kubejs_tooltip_service
        self.run_md_translation_service = run_md_translation_service
        self.TaskSession = TaskSession

        status_card = ft.Container(
            padding=ft.Padding.symmetric(horizontal=18, vertical=16),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
            content=ft.Row(
                [
                    self.progress,
                    ft.Column(
                        [
                            ft.Text("執行狀態", size=12, color=C.DIM),
                            self.status_chip,
                            self.cancel_button,
                        ],
                        spacing=8,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        right_panel = ft.Column(
            [
                status_card,
                kit.section_card(
                    "執行日誌",
                    self.log_view,
                    icon=ft.Icons.TERMINAL,
                    tone="gold",
                    expand=True,
                    actions=[
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=18,
                            icon_color=C.MUTED,
                            tooltip="清空日誌",
                            on_click=lambda e: self._clear_logs(),
                        )
                    ],
                ),
            ],
            expand=True,
            spacing=16,
        )
        return right_panel

    # ------------------------------------------------------------------
    # 樣式 helper（集中到 app.ui.kit / app.ui.design）
    # ------------------------------------------------------------------
    # 本頁原本有 _section_header / _styled_card，現在改用 kit 的共用元件。
    # 目的：
    # - 多個 View 可共用同一套卡片/按鈕樣式
    # - 新 UI 的一致性調整集中在 app/ui/kit 與 app/ui/design。

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
        return build_action_row(
            view=self,
            on_start=on_start,
            on_dry_run=on_dry_run,
            on_reset=on_reset,
            trailing=trailing,
        )

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
        """開啟目錄選擇器並設定目標欄位（同步包裝）。

        Args:
            target: 選擇後要填入路徑的 TextField。
        """
        self._picker_target_field = target
        self._page.run_task(self._async_pick_directory_into)

    async def _async_pick_directory_into(self):
        """async 實作：等待使用者選擇目錄後更新目標欄位。"""
        result = await self.file_picker.get_directory_path()
        if result:
            self._picker_target_field.value = result
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
    def will_unmount(self):
        """換頁／關閉：停止輪詢（idempotent）。任務本身照常執行，不再碰已卸載的控制項。"""
        stop_ui_timer(self)

    def did_mount(self):
        """重新掛載：任務仍在追蹤就接續輪詢（任務已結束時補上最終狀態）。"""
        resume_ui_timer(self)

    def _start_ui_timer(self):
        """啟動 UI 更新計時器"""
        self.cancel_button.disabled = False
        return start_translation_ui_timer(self)

    def _on_cancel(self):
        """要求取消目前的翻譯任務（在批次之間或等待 API 限流時停止）。"""
        session = self.session
        if session is None or not self._ui_timer_running:
            return
        request = getattr(session, "request_cancel", None)
        if request is None:
            return
        request()
        self.cancel_button.disabled = True
        self._set_status("正在取消…", "gold")
        self.page.update()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str, tone: str = "neutral"):
        """更新狀態晶片的文字與顏色（``tone`` 為色組名稱，也接受舊背景色）。"""
        set_chip_status(self.status_chip, text, tone)
        self.page.update()

    def _apply_status_style(self, tone: str):
        apply_status_style(self.status_chip, tone)

    def _refresh_steps(self):
        """步驟卡跟著勾選框的值更新外觀（程式直接改 checkbox.value 之後呼叫）。"""
        for card in self._step_cards:
            card.refresh()

    def _append_log(self, line: str):
        """新增一行日誌到日誌檢視區（直接走 LogView.add）。

        取代原本的裸 controls.append + manual truncate 邏輯。
        LogView 內部已有 show_levels 過濾、max_lines 截斷、等寬字與等級顏色。
        """
        self.log_view.add(line, level="system")

    def _clear_logs(self):
        """清除日誌檢視區的所有內容"""
        self.log_view.clear()
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
        self._set_status("尚未開始", "neutral")
        self.progress.value = 0
        self._refresh_steps()
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
        self._set_status("尚未開始", "neutral")
        self.progress.value = 0
        self._refresh_steps()
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
        self._set_status("尚未開始", "neutral")
        self.progress.value = 0
        self._refresh_steps()
        self._append_log("[UI] 已重置：Markdown 輸入已清空")
        self.page.update()

    @property
    def page(self):
        """回傳 Flet Page 實例 (2026-08-01 PR #85 重構補 @property)。

        之前 def page(self) 沒 @property decorator,變成 bound method reference,
        PR #85 改用 show_snack(self.page, ...) 直接呼叫時,
        self.page 是 method object 而非 Page 實例,SnackBar 永遠跳不出來。
        加 @property 後 self.page 才是 Page 實例。
        """
        return self._page
