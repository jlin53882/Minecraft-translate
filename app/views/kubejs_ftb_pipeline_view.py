"""KubeJS／FTB Quests 的來源到 overlay 流水線頁面。

這個頁面只負責協調既有 scanner、ModDB 與 overlay writer；不執行模組包內的
JavaScript，也不另建翻譯資料庫。Web 版以路徑欄位作為一級輸入，檔案選擇器
只是便利功能，避免瀏覽器回傳 ``None`` 時仍顯示「已加入」。
"""

from __future__ import annotations

import asyncio
import json

import flet as ft

from app.services_impl.pack_pipeline_service import (
    discover_kubejs_ftb_pack_candidates,
    run_kubejs_ftb_pipeline_service,
)
from app.tasks.operation_registry import launch_page_operation
from app.tasks.task_session import TaskSession, tag_session
from app.ui import kit
from app.ui.design import C
from app.ui.dialogs import close_page_dialog, present_page_dialog
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import make_status_chip, set_chip_status
from app.ui.update_scope import update_regions
from app.views.pipeline.kubejs_ftb_wizard import KubejsFtbWizard
from app.views.pipeline.pipeline_forms import build_pipeline_dialog, dialog_button
from app.views.pipeline.pipeline_progress import PipelineProgressPanel
from app.views.pipeline.pipeline_workbench import (
    PipelineStepSpec,
    build_pipeline_input_row,
    build_pipeline_status_card,
    build_pipeline_steps_card,
    build_pipeline_workbench,
)

_POLL_INTERVAL_SEC = 0.2


_MODES = ("both", "ftb", "kubejs")


class KubejsFtbPipelineView(ft.Column):
    """以一鍵流水線工作台呈現 KubeJS／FTB 的來源、執行與輸出。

    同一個實作同時服務側欄的「KubeJS / FTB 流水線」頁與任務翻譯頁的 FTB／KubeJS 分頁：
    ``mode`` 決定來源類型（``both`` ／ ``ftb`` ／ ``kubejs``），``embedded=True`` 時不顯示頁首。
    同一時間只允許一個流水線執行，避免兩個入口同時寫入 ModDB。
    """

    # 目前執行中的流水線擁有者（任一入口）；None 表示沒有執行中的流水線。
    _active_run: KubejsFtbPipelineView | None = None

    def __init__(
        self,
        page: ft.Page,
        file_picker: ft.FilePicker,
        *,
        mode: str = "both",
        embedded: bool = False,
    ):
        if mode not in _MODES:
            raise ValueError(f"未知的流水線模式：{mode!r}")
        super().__init__(expand=True, spacing=14, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self.file_picker = file_picker
        self._mode = mode
        self._embedded = embedded
        self.session: TaskSession | None = None
        self._poller = PollerHandle()
        self._running = False
        self._candidates: list[str] = []
        self._source_generation = 0
        self._current_dry_run = True
        self._current_publish = False
        self._one_click_stage: str | None = None
        self._publish_dialog: ft.AlertDialog | None = None
        self._publish_confirm_button: ft.Button | None = None

        self._build_controls(page)
        self._build_layout()

    def _build_controls(self, page: ft.Page) -> None:
        """建立工作台需要的控制項；各群組維持單一職責。"""
        self._build_input_controls()
        self._build_mode_controls()
        self._build_status_controls(page)
        self._build_action_controls()

    def _build_input_controls(self) -> None:
        self.source_field = kit.text_field(
            "來源資料夾／模組包根目錄",
            hint="Web 可直接輸入完整路徑；也可輸入 kubejs 或 quests 資料夾",
            mono=True,
            expand=True,
            path=True,
            on_change=self._source_changed,
        )
        self.version_field = kit.text_field(
            "遊戲版本", value="1.21.1", mono=True, width=260
        )
        self.scan_mode = kit.dropdown(
            label="同步模式",
            value="full",
            width=300,
            options=[
                ft.dropdown.Option("full", "完整掃描（非破壞同步）"),
                ft.dropdown.Option("incremental", "增量同步（只處理變更來源）"),
            ],
        )
        self.root_dropdown = kit.dropdown(
            label="有效來源根目錄（多個候選時必須明確選擇）",
            expand=True,
            options=[],
        )
        self.output_field = kit.text_field(
            "Overlay 輸出目錄（正式發布必填）",
            hint="例如：D:/翻譯輸出/ATM10-overlay",
            mono=True,
            expand=True,
            path=True,
        )

    def _build_mode_controls(self) -> None:
        mode = self._mode
        self.include_kubejs = ft.Checkbox(
            label="KubeJS", value=mode != "ftb", visible=mode != "ftb"
        )
        self.include_ftb = ft.Checkbox(
            label="FTB Quests", value=mode != "kubejs", visible=mode != "kubejs"
        )
        self.translate_missing = ft.Checkbox(label="LM 翻譯待處理", value=False)

    def _build_status_controls(self, page: ft.Page) -> None:
        self.status_chip = make_status_chip("尚未開始")
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=C.MUTED)
        self.progress = kit.progress_bar(0, "em", height=8)
        self.source_summary = ft.Text(
            "尚未分析來源。", size=12, color=C.MUTED, selectable=True
        )
        self.output_summary = ft.Text(
            "尚未產生 overlay 預覽。", size=12, color=C.MUTED, selectable=True
        )
        self.run_summary = ft.Text(
            "尚未執行。", size=12, color=C.MUTED, selectable=True
        )
        self.progress_panel = PipelineProgressPanel(
            page,
            on_cancel=self._cancel,
            step_names=["分析來源", "同步 ModDB", "LM 翻譯", "輸出 overlay"],
            log_height=180,
            progress=self.progress,
        )
        self.log_view = self.progress_panel.log_view
        self.cancel_button = self.progress_panel.cancel_button

    def _build_action_controls(self) -> None:
        """步驟按鈕由步驟卡建立；這裡只保留一鍵執行與統一停用用的清單。"""
        self._run_buttons: list[ft.Button] = []
        self._one_click_button = kit.button(
            "一鍵執行",
            "gold",
            icon=ft.Icons.FLASH_ON,
            size="lg",
            on_click=self._one_click_clicked,
        )
        self._run_buttons.append(self._one_click_button)

    def _build_layout(self) -> None:
        """與一鍵製作相同的兩欄工作台：左側路徑與步驟，右側狀態與日誌。"""
        paths_card = self._build_paths_card()
        steps_card = self._build_steps_card()
        status_panel = self._build_status_panel()
        self.progress_panel.attach_idle_status_card(status_panel)
        self._wizard = KubejsFtbWizard(self)
        self.workbench_view = build_pipeline_workbench(
            show_header=not self._embedded,
            title="KubeJS / FTB 任務流水線",
            subtitle="來源分析 → ModDB 同步 → LM 翻譯 → overlay 輸出；與模組包一鍵製作相同的操作方式",
            icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
            tone="em",
            left_controls=[paths_card, steps_card],
            right_controls=[status_panel, self.progress_panel.container],
            actions=[self._one_click_button],
        )
        self.controls = [self.workbench_view]

    def _source_changed(self, _e=None) -> None:
        """來源路徑變更後清除舊候選，避免執行時沿用前一個模組包根目錄。"""
        self._source_generation += 1
        self._candidates = []
        self.root_dropdown.value = None
        kit.set_dropdown_options(self.root_dropdown, [])
        self.source_summary.value = "來源已變更，請重新分析來源。"
        self.source_summary.color = C.GOLD
        self._safe_update()

    def _build_paths_card(self) -> ft.Control:
        """專案路徑卡：來源、候選根目錄與 overlay 輸出目錄，結構與一鍵製作的路徑卡相同。"""
        return kit.section_card(
            "專案路徑",
            ft.Column(
                [
                    ft.Column(
                        [
                            kit.section_label("讀取來源（模組包根目錄） *"),
                            build_pipeline_input_row(
                                self.source_field,
                                kit.pick_button(
                                    ft.Icons.FOLDER_OPEN,
                                    "選擇模組包根目錄；Web 不支援時請直接輸入路徑",
                                    self._pick_source,
                                ),
                            ),
                        ],
                        spacing=6,
                    ),
                    self.source_summary,
                ],
                spacing=14,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="em",
        )

    def _build_steps_card(self) -> ft.Control:
        """四個階段與一鍵製作相同的編號、按鈕與狀態色；每個按鈕只觸發對應的服務模式。"""
        return build_pipeline_steps_card(
            [
                PipelineStepSpec(
                    1,
                    "分析來源",
                    "辨識 KubeJS 與 FTB Quests 的實際來源根目錄",
                    ft.Icons.FIND_IN_PAGE_OUTLINED,
                    "dia",
                    self._discover_clicked,
                ),
                PipelineStepSpec(
                    2,
                    "預覽同步計畫",
                    "唯讀掃描並計算 ModDB 變更，不寫入資料庫",
                    ft.Icons.SEARCH,
                    "ench",
                    self._preview_run,
                ),
                PipelineStepSpec(
                    3,
                    "同步 ModDB",
                    "寫入來源條目；啟用 LM 時翻譯待處理條目，不輸出檔案",
                    ft.Icons.SYNC,
                    "gold",
                    self._sync_run,
                ),
                PipelineStepSpec(
                    4,
                    "輸出 overlay",
                    "正式發布 kubejs／FTB Quests overlay 到輸出目錄",
                    ft.Icons.PUBLISH,
                    "em",
                    self._publish_run,
                ),
            ],
            title="流水線步驟",
            icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
            tone="ench",
            on_button_created=self._run_buttons.append,
        )

    def _build_status_panel(self) -> ft.Control:
        return build_pipeline_status_card(
            progress=self.progress,
            progress_status=self.progress_status,
            status_chip=self.status_chip,
            summary=ft.Column([self.run_summary, self.output_summary], spacing=6),
            title="執行狀態",
            icon=ft.Icons.TIMELINE,
            tone="gold",
        )

    async def _pick_output(self, _e=None) -> None:
        result = await self.file_picker.get_directory_path()
        if result:
            self.output_field.value = result
            self._safe_update()

    def _set_run_buttons_disabled(self, disabled: bool) -> None:
        for button in self._run_buttons:
            button.disabled = disabled

    def _one_click_clicked(self, _e=None) -> None:
        """一鍵執行：開啟設定精靈；第 4 步開始唯讀預覽，預覽後由確認對話框決定是否發布。"""
        if self._running:
            show_snack(self._page, "已有 KubeJS／FTB 流水線執行中", C.GOLD)
            return
        if not (self.source_field.value or "").strip():
            show_snack(self._page, "請先輸入來源路徑", C.RED)
            return
        self._wizard.open()

    def _begin_one_click_preview(self) -> None:
        """精靈第 4 步「開始預覽」：唯讀預覽，成功後才會出現發布確認。"""
        self._one_click_stage = "preview"
        self._page.run_task(self._run_one_click_preview)

    async def _run_one_click_preview(self) -> None:
        await self._start(dry_run=True, publish=False)
        if not self._running:
            self._one_click_stage = None

    def _offer_publish_confirmation(self, success: bool, summary: dict) -> None:
        """預覽完成後顯示發布確認；有衝突或預覽失敗時不提供確認按鈕。"""
        if not success or not summary:
            show_snack(self._page, "預覽未完成，未開始發布", C.GOLD)
            return
        output = summary.get("output") or {}
        conflicts = len(output.get("conflicts") or [])
        self._publish_dialog = self._build_publish_dialog(
            summary, blocked=conflicts > 0
        )
        present_page_dialog(self._page, self._publish_dialog)

    def _build_publish_dialog(self, summary: dict, *, blocked: bool) -> ft.AlertDialog:
        """確認發布對話框：列出預覽的來源、同步、翻譯與 overlay 數量，確認後才寫入模組包。"""
        coverage = summary.get("coverage") or {}
        sync = summary.get("sync") or {}
        output = summary.get("output") or {}
        lines = [
            f"來源根目錄：{summary.get('source_root', '—')}",
            (
                f"來源檔案 {coverage.get('files_read', 0):,}/{coverage.get('files_seen', 0):,}"
                f"　候選 {coverage.get('items_found', 0):,}"
                f"　引用 {coverage.get('references_found', 0):,}"
            ),
            (
                f"待同步 {sync.get('items_to_sync', 0):,}"
                f"　未變更略過 {sync.get('items_unchanged', 0):,}"
            ),
            (
                "LM 翻譯：發布時翻譯待處理條目"
                if self.translate_missing.value
                else "LM 翻譯：未啟用"
            ),
            f"overlay 輸出目錄：{output.get('output_root', self.output_field.value)}",
            (
                f"將套用 {output.get('entries_applied', 0):,}"
                f"　衝突 {len(output.get('conflicts') or []):,}"
                f"　未套用 {len(output.get('unapplied') or []):,}"
            ),
        ]
        body = [ft.Text(line, size=13, color=C.TEXT, selectable=True) for line in lines]
        if blocked:
            body.append(
                ft.Text(
                    "輸出有衝突，請先處理衝突後再發布。",
                    size=13,
                    color=C.RED,
                    weight=ft.FontWeight.W_500,
                )
            )
        # 停用時改用無固定底色的 secondary 樣式，避免 primary 的底色蓋住 Flet 的停用外觀。
        confirm = dialog_button(
            "確認執行並發布",
            self._confirm_publish,
            role="secondary" if blocked else "primary",
            icon=ft.Icons.PUBLISH,
        )
        confirm.disabled = blocked
        self._publish_confirm_button = confirm
        return build_pipeline_dialog(
            self._page,
            title="確認發布 overlay",
            content=ft.Column(body, spacing=10, tight=True),
            actions=[dialog_button("取消", self._close_publish_dialog), confirm],
            icon=ft.Icons.PUBLISH,
            tone="em",
        )

    def _close_publish_dialog(self, _e=None) -> None:
        if self._publish_dialog is not None:
            close_page_dialog(self._page, self._publish_dialog)

    async def _confirm_publish(self, _e=None) -> None:
        self._close_publish_dialog()
        await self._start(dry_run=False, publish=True)

    async def _pick_source(self, _e=None) -> None:
        result = await self.file_picker.get_directory_path()
        if result:
            self.source_field.value = result
            self._source_changed()
            await self._discover()
        else:
            self.source_summary.value = (
                "檔案選擇器沒有回傳路徑；請在上方欄位直接輸入來源。"
            )
            self.source_summary.color = C.GOLD
            self._safe_update()

    async def _discover_clicked(self, _e=None) -> None:
        await self._discover()

    async def _discover(self) -> None:
        source = (self.source_field.value or "").strip()
        if not source:
            self.source_summary.value = "請先輸入來源路徑。"
            self.source_summary.color = C.RED
            self._safe_update()
            return
        generation = self._source_generation
        self._set_run_buttons_disabled(True)
        self.source_summary.value = "分析有限深度來源候選中…"
        self.source_summary.color = C.MUTED
        self._safe_update()
        try:
            candidates = await asyncio.to_thread(
                discover_kubejs_ftb_pack_candidates, source
            )
        except (OSError, RuntimeError, ValueError) as exc:
            candidates = []
            if (
                generation != self._source_generation
                or source != (self.source_field.value or "").strip()
            ):
                self._set_run_buttons_disabled(self._running)
                return
            self.source_summary.value = f"來源分析失敗：{exc}"
            self.source_summary.color = C.RED
        if (
            generation != self._source_generation
            or source != (self.source_field.value or "").strip()
        ):
            self._set_run_buttons_disabled(self._running)
            return
        self._candidates = candidates
        kit.set_dropdown_options(
            self.root_dropdown,
            [(candidate, candidate) for candidate in candidates],
        )
        if len(candidates) == 1:
            self.root_dropdown.value = candidates[0]
            self.source_summary.value = f"找到 1 個來源根目錄：{candidates[0]}"
            self.source_summary.color = C.EM
        elif candidates:
            self.root_dropdown.value = None
            self.source_summary.value = (
                f"找到 {len(candidates)} 個候選；請在下拉選單明確選擇，不會自動合併。"
            )
            self.source_summary.color = C.GOLD
        elif not self.source_summary.value.startswith("來源分析失敗"):
            self.root_dropdown.value = None
            self.source_summary.value = (
                "找不到 kubejs 或 config/ftbquests/quests；請確認選的是模組包根目錄。"
            )
            self.source_summary.color = C.RED
        self._set_run_buttons_disabled(self._running)
        self._safe_update()

    async def _preview_run(self, _e=None) -> None:
        await self._start(dry_run=True, publish=False)

    async def _sync_run(self, _e=None) -> None:
        await self._start(dry_run=False, publish=False)

    async def _publish_run(self, _e=None) -> None:
        await self._start(dry_run=False, publish=True)

    def _run_blocker(self, publish: bool) -> tuple[str, str] | None:
        """回傳無法開始執行的原因與提示顏色；None 表示輸入可以開始執行。

        根目錄候選在 ``_start`` 分析來源後才能檢查，因此不在這裡處理。
        """
        active = KubejsFtbPipelineView._active_run
        if self._running or (active is not None and active is not self):
            return "已有 KubeJS／FTB 流水線執行中", C.GOLD
        if not (self.source_field.value or "").strip():
            return "請先輸入來源路徑", C.RED
        if not (self.include_kubejs.value or self.include_ftb.value):
            return "至少選擇 KubeJS 或 FTB Quests", C.RED
        if publish and not (self.output_field.value or "").strip():
            return "正式發布前請填寫 overlay 輸出目錄", C.RED
        return None

    async def _start(self, *, dry_run: bool, publish: bool) -> None:
        blocker = self._run_blocker(publish)
        if blocker is not None:
            show_snack(self._page, blocker[0], blocker[1])
            return
        if not self._candidates:
            await self._discover()
        if len(self._candidates) > 1 and not self.root_dropdown.value:
            show_snack(self._page, "來源有多個候選，請先明確選擇根目錄", C.GOLD)
            return
        output = (self.output_field.value or "").strip() or None
        self.session = tag_session(
            TaskSession(max_logs=2000),
            "KubeJS / FTB 流水線",
            "kubejs_ftb_pipeline",
            page=self._page,
        )
        self._running = True
        self._current_dry_run = dry_run
        self._current_publish = publish
        self._set_running(True)
        self.progress_panel.start()
        self.progress.value = 0
        self.progress_panel.clear_logs()
        self.run_summary.value = "執行中…"
        self._safe_update()
        source = (self.source_field.value or "").strip()
        version = (self.version_field.value or "").strip()
        pack_root = self.root_dropdown.value or (
            self._candidates[0] if self._candidates else None
        )
        launched = launch_page_operation(
            self._page,
            lambda: run_kubejs_ftb_pipeline_service(
                source,
                version,
                self.session,
                output_root=output,
                scan_mode=self.scan_mode.value or "full",
                include_kubejs=bool(self.include_kubejs.value),
                include_ftb=bool(self.include_ftb.value),
                dry_run=dry_run,
                publish=publish,
                pack_root=pack_root,
                translate_missing=bool(self.translate_missing.value),
            ),
            name="KubeJS / FTB 流水線",
            owner="kubejs-ftb-pipeline",
            task_session=self.session,
        )
        if not launched:
            self._running = False
            KubejsFtbPipelineView._active_run = None
            self._set_running(False)
            show_snack(self._page, "應用程式正在關閉，未啟動流水線", C.GOLD)
            self._safe_update()
            return
        KubejsFtbPipelineView._active_run = self
        self._poller.start(self._page, self._poll)

    def _cancel(self, _e=None) -> None:
        if self.session is None or not self._running:
            return
        self.session.request_cancel()
        self.cancel_button.disabled = True
        set_chip_status(self.status_chip, "取消中…", "gold")
        self._safe_update()

    async def _poll(self, alive=lambda: True) -> None:
        while alive() and self._running:
            try:
                self._sync_session()
            except Exception as exc:  # noqa: BLE001 - UI 卸載時停止輪詢並保留任務結果
                self._running = False
                self._set_running(False)
                self.log_view.add_error(f"畫面輪詢中止：{exc!r}")
                self._safe_update()
                break
            if alive() and self._running:
                await asyncio.sleep(_POLL_INTERVAL_SEC)

    def _sync_session(self) -> None:
        if self.session is None:
            return
        snapshot = self.session.snapshot()
        self.progress.value = float(snapshot.get("progress", 0) or 0)
        self.log_view.sync_entries(snapshot.get("logs", []) or [], update=False)
        status = str(snapshot.get("status") or "").upper()
        summary = snapshot.get("summary") or {}
        self._sync_progress_stage(self.progress.value, status)
        if status in {"DONE", "ERROR"}:
            self._running = False
            if KubejsFtbPipelineView._active_run is self:
                KubejsFtbPipelineView._active_run = None
            self._set_running(False)
            cancelled = summary.get("status") == "CANCELLED"
            success = status != "ERROR" and not cancelled
            if success and self._current_publish:
                self.progress_panel.set_step_running(4, "輸出 overlay")
            if status == "ERROR":
                set_chip_status(self.status_chip, "失敗", "red")
            elif cancelled:
                set_chip_status(self.status_chip, "已取消", "gold")
            elif summary.get("state") == "done_with_warnings":
                set_chip_status(self.status_chip, "完成但有警告", "gold")
            else:
                set_chip_status(self.status_chip, "完成", "em")
            self.progress_panel.finish_all(
                success,
                cancelled=cancelled,
                dry_run=self._current_dry_run,
            )
            self._render_summary(summary)
            if self._one_click_stage == "preview":
                self._one_click_stage = None
                self._offer_publish_confirmation(success, summary)
        self._safe_update()

    def _sync_progress_stage(self, progress: float, status: str) -> None:
        """把共用 session 進度映射成一鍵流水線風格的階段狀態。"""
        if not self._running or status in {"DONE", "ERROR"}:
            return
        if progress < 0.35:
            self.progress_panel.set_step_running(1, "分析來源")
        elif progress < 0.65:
            self.progress_panel.set_step_running(2, "同步 ModDB")
        elif progress < 0.95 or not self._current_publish:
            self.progress_panel.set_step_running(3, "LM 翻譯／驗證")
        else:
            self.progress_panel.set_step_running(4, "輸出 overlay")

    def _render_summary(self, summary: dict) -> None:
        if not summary:
            return
        coverage = summary.get("coverage") or {}
        kinds = coverage.get("kind_counts") or {}
        translation = summary.get("translation") or {}
        translation_line = ""
        if translation:
            translation_line = (
                f"\nLM 翻譯：{translation.get('status', '—')}　"
                f"候選 {translation.get('candidates', 0):,}　"
                f"寫入 {translation.get('written', 0):,}　"
                f"預估批次 {translation.get('planned_batches', 0):,}"
            )
        self.run_summary.value = (
            f"run_id={summary.get('run_id', '—')}　狀態={summary.get('state', '—')}\n"
            f"模式={summary.get('scan_mode', '—')}　"
            f"來源檔案 {coverage.get('files_read', 0):,}/{coverage.get('files_seen', 0):,}　"
            f"候選 {coverage.get('items_found', 0):,}　引用 {coverage.get('references_found', 0):,}\n"
            f"待同步 {((summary.get('sync') or {}).get('items_to_sync', 0)):,}　"
            f"未變更略過 {((summary.get('sync') or {}).get('items_unchanged', 0)):,}"
            f"{translation_line}"
        )
        self.source_summary.value = (
            f"來源根：{summary.get('source_root', '—')}\n"
            f"pack_scope：{summary.get('pack_scope', '—')}\n"
            f"kind：{json.dumps(kinds, ensure_ascii=False, sort_keys=True)}"
        )
        output = summary.get("output") or {}
        if output:
            self.output_summary.value = (
                f"輸出：{output.get('output_root', '—')}\n"
                f"考慮 {output.get('entries_considered', 0):,}、"
                f"套用 {output.get('entries_applied', 0):,}、"
                f"重掃 {output.get('entries_rescan_verified', 0):,}、"
                f"格式重讀 {output.get('entries_reparsed', 0):,}\n"
                f"衝突 {len(output.get('conflicts', [])):,}、未套用 {len(output.get('unapplied', [])):,}"
            )

    def _set_running(self, running: bool) -> None:
        self._set_run_buttons_disabled(running)
        self.progress_panel.set_running(running)

    def will_unmount(self) -> None:
        self._poller.stop()

    def did_mount(self) -> None:
        if self.session is not None and self._running and not self._poller.running:
            self._poller.start(self._page, self._poll)

    def _safe_update(self) -> None:
        update_regions(self)


__all__ = ["KubejsFtbPipelineView"]
