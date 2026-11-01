"""app/views/translation_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading  # noqa: F401

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import apply_status_style, make_status_chip, set_chip_status
from app.views._log import LogView
from app.views.pipeline.pipeline_workbench import (
    build_pipeline_log_card,
    build_pipeline_status_card,
    build_pipeline_workbench,
)
from app.views.translation.translation_actions import (
    resume_ui_timer,
    run_md,
    stop_ui_timer,
)
from app.views.translation.translation_actions import (
    start_ui_timer as start_translation_ui_timer,
)
from app.views.translation.translation_panels import (
    build_action_row,
    build_md_tab,
    build_path_row,
)
from translation_tool.utils.log_unit import log_error, log_warning

# 可選匯入：避免某個 service 暫時不可用時，整頁無法開啟
try:
    from app.services_impl.pipelines.md_service import run_md_translation_service
except Exception as _exc:  # noqa: BLE001 - 服務載入失敗時整頁仍可開啟，但原因必須留在 log
    log_error("Markdown 翻譯服務載入失敗，翻譯頁的 MD 功能將無法使用", exc_info=True)
    run_md_translation_service = None

try:
    from app.tasks.task_session import TaskSession
except Exception as _exc:  # noqa: BLE001 - 載入失敗時整頁仍可開啟，但原因必須留在 log
    log_error("TaskSession 載入失敗，翻譯頁將無法建立任務", exc_info=True)
    TaskSession = None


class TranslationView(ft.Column):
    """任務翻譯工作台：Markdown 文件翻譯。FTB／KubeJS 已移至流水線頁。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 TranslationView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(expand=True, spacing=16)
        tab_content = self._init_translation_state_and_tabs(file_picker, page)
        right_panel = self._build_translation_status_and_right_panel(tab_content)

        self.workbench_view = build_pipeline_workbench(
            title="任務翻譯工具",
            subtitle="處理 Markdown 文件；左側設定流程，右側查看狀態與 Log。",
            icon=ft.Icons.TRANSLATE,
            tone="ench",
            left_controls=[tab_content],
            right_controls=[right_panel],
        )
        self.controls = [self.workbench_view]

    def _init_translation_state_and_tabs(self, file_picker, page):
        """任務翻譯頁的狀態與分頁。"""
        self._page = page
        self.file_picker = file_picker
        self._picker_target_field: ft.TextField | None = None
        self._step_cards: list = []  # 面板建立時登記，reset 後用來刷新步驟卡外觀

        self.session = None
        self._ui_timer_running = False
        self._poller = PollerHandle()  # 輪詢的 owner：卸載時 stop、重新掛載時 resume

        # 右側共用狀態與日誌
        self.status_chip = make_status_chip("尚未開始")
        self._apply_status_style("neutral")
        self.cancel_button = kit.button(
            "取消",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            tooltip="在目前批次完成後停止（已翻譯的部分會保留並寫出）",
            on_click=lambda e: self._on_cancel(),
        )
        self.cancel_button.disabled = True
        self.cancel_button.visible = False
        # 共用水平進度條；.value 介面與 ft.ProgressBar 相同，actions 層不需要改
        self.progress = kit.progress_bar(0, "em", height=8)
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=C.MUTED)
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器）
        # tail 模式與既有的「最後 N 筆整批重建」行為一致
        self.log_view = LogView(
            page=self._page,
            mode="tail",
            tail_lines=250,
        )

        self.md_tab_content = self._build_md_tab()
        scope_note = kit.section_card(
            "FTB Quests／KubeJS",
            kit.hint_text(
                "FTB Quests 與 KubeJS 翻譯已移到側欄的「KubeJS / FTB 流水線」頁，"
                "共用預覽、確認與發布流程。本頁目前只處理 Markdown 文件。"
            ),
            icon=ft.Icons.INFO_OUTLINE,
            tone="dia",
        )
        tab_content = ft.Column(
            [scope_note, self.md_tab_content],
            expand=True,
            spacing=12,
        )
        return tab_content

    def _build_translation_status_and_right_panel(self, tab_content):
        """狀態卡片與右側面板。"""
        # action layer 讀取的相容 seam
        self.run_md_translation_service = run_md_translation_service
        self.TaskSession = TaskSession

        status_card = build_pipeline_status_card(
            progress=self.progress,
            progress_status=self.progress_status,
            status_chip=self.status_chip,
            cancel=self.cancel_button,
            title="執行狀態",
            icon=ft.Icons.TRANSLATE,
            tone="gold",
            hint=kit.hint_text(
                "翻譯工作會沿用左側的步驟設定；取消只在批次檢查點生效。"
            ),
        )
        self.log_panel = build_pipeline_log_card(
            self.log_view,
            title="執行日誌",
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
        )
        self.log_panel.visible = False
        right_panel = ft.Column(
            [status_card, self.log_panel],
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
    def _run_md(self, *, dry_run: bool):
        """執行 Markdown 翻譯流程"""
        return run_md(self, dry_run=dry_run)

    def resume_interrupted(self, task) -> None:
        """重開後續跑（#164）：切到對應分頁、帶入上次的輸入與選項後開始。

        只在使用者於啟動時的確認對話框按下「續跑」後才會被呼叫；已完成的譯文由翻譯快取還原，
        只會翻譯尚未完成的部分。一律是正式執行（dry-run 關閉、翻譯步驟開啟）。
        """
        if self._ui_timer_running:
            show_snack(self.page, "已有翻譯任務執行中，無法續跑上次的任務", C.GOLD)
            return
        options = getattr(task, "options", None) or {}
        kind = getattr(task, "kind", "")

        def flag(name: str, default: bool = True) -> bool:
            return bool(options.get(name, default))

        if kind != "md":
            log_warning(f"[Resume] 任務翻譯頁不支援續跑類型：{kind!r}")
            return
        self.md_in_dir.value = task.input_dir
        self.md_out_dir.value = task.output_dir or ""
        self.md_step_extract.value = flag("step_extract")
        self.md_step_translate.value = True
        self.md_step_inject.value = flag("step_inject")
        self.md_write_new_cache.value = flag("write_new_cache")
        self.md_lang_mode.value = str(options.get("lang_mode") or "non_cjk_only")
        runner = self._run_md
        self._refresh_steps()
        runner(dry_run=False)

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
        self.log_panel.visible = True
        self.cancel_button.visible = True
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
        self.progress_status.value = text
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
        self.log_panel.visible = True
        self.log_view.add(line, level="system", dedupe=False)

    def _clear_logs(self):
        """清除日誌檢視區的所有內容"""
        self.log_view.clear()
        self.page.update()

    # ------------------------------------------------------------------
    # reset actions
    # ------------------------------------------------------------------
    def _reset_md_inputs(self):
        """重置 Markdown 翻譯的所有輸入欄位"""
        self.cancel_button.visible = False
        self.md_in_dir.value = ""
        self.md_out_dir.value = ""
        self.md_step_extract.value = True
        self.md_step_translate.value = True
        self.md_step_inject.value = True
        self.md_write_new_cache.value = False
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
