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
from app.ui.poller import PollerHandle
from app.ui.snack import show_snack
from app.ui.status_chip import make_status_chip, set_chip_status
from app.ui.update_scope import update_regions
from app.views._log import LogView

_POLL_INTERVAL_SEC = 0.2


class KubejsFtbPipelineView(ft.Column):
    """以四個區域呈現來源、執行、輸出與紀錄的單一工作流。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=14, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self.file_picker = file_picker
        self.session: TaskSession | None = None
        self._poller = PollerHandle()
        self._running = False
        self._candidates: list[str] = []
        self._source_generation = 0

        self.source_field = kit.text_field(
            "來源資料夾／模組包根目錄",
            hint="Web 可直接輸入完整路徑；也可輸入 kubejs 或 quests 資料夾",
            mono=True,
            expand=True,
            path=True,
            on_change=self._source_changed,
        )
        self.version_field = kit.text_field(
            "遊戲版本", value="1.21.1", mono=True, width=180
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
            "Overlay 輸出目錄（可選）",
            hint="例如：D:/翻譯輸出/ATM10-overlay",
            mono=True,
            expand=True,
            path=True,
        )
        self.include_kubejs = ft.Checkbox(label="KubeJS", value=True)
        self.include_ftb = ft.Checkbox(label="FTB Quests", value=True)
        self.translate_missing = ft.Checkbox(label="LM 翻譯待處理", value=False)
        self.execution_hint = kit.hint_text(
            "預覽按鈕只讀來源與目前 DB；正式按鈕才會同步 DB 並發布 overlay。"
        )
        self.status_chip = make_status_chip("尚未開始")
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
        self.log_view = LogView(page=page, mode="tail", tail_lines=300, height=300)
        self.cancel_button = kit.button(
            "取消",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            disabled=True,
            on_click=self._cancel,
            tooltip="在目前安全檢查點取消；未完成 overlay 不會發布",
        )
        self.preview_button = kit.button(
            "預覽來源／同步計畫",
            "secondary",
            icon=ft.Icons.SEARCH,
            on_click=self._preview_run,
        )
        self.publish_button = kit.button(
            "正式同步並發布 overlay",
            "primary",
            icon=ft.Icons.PUBLISH,
            on_click=self._publish_run,
            tooltip="先以同一 scanner 驗證，再原子發布至指定 overlay 目錄",
        )
        self.discover_button = kit.button(
            "分析來源",
            "secondary",
            icon=ft.Icons.FIND_IN_PAGE_OUTLINED,
            on_click=self._discover_clicked,
        )
        self.tabs = kit.Segmented(
            [
                ("source", "來源", ft.Icons.FOLDER_OUTLINED),
                ("execute", "執行", ft.Icons.PLAY_ARROW),
                ("output", "輸出", ft.Icons.FOLDER_ZIP_OUTLINED),
                ("logs", "紀錄", ft.Icons.TERMINAL),
            ],
            "source",
            self._select_tab,
        )
        self._source_panel = self._build_source_panel()
        self._execute_panel = self._build_execute_panel()
        self._output_panel = self._build_output_panel()
        self._logs_panel = self._build_logs_panel()
        self._panels = {
            "source": self._source_panel,
            "execute": self._execute_panel,
            "output": self._output_panel,
            "logs": self._logs_panel,
        }
        self.controls = [
            kit.page_header(
                "KubeJS / FTB 流水線",
                "共用來源掃描、ModDB 同步與模組包根目錄 overlay；不把腳本或 config 偽裝成 Resource Pack。",
                icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
                tone="em",
            ),
            ft.Row(
                [self.tabs, self.status_chip],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            self._source_panel,
            self._execute_panel,
            self._output_panel,
            self._logs_panel,
        ]
        self._select_tab("source")

    def _source_changed(self, _e=None) -> None:
        """來源路徑變更後清除舊候選，避免執行時沿用前一個模組包根目錄。"""
        self._source_generation += 1
        self._candidates = []
        self.discover_button.disabled = False
        self.root_dropdown.value = None
        kit.set_dropdown_options(self.root_dropdown, [])
        self.source_summary.value = "來源已變更，請重新分析來源。"
        self.source_summary.color = C.GOLD
        self._safe_update()

    def _build_source_panel(self) -> ft.Control:
        path_row = ft.Row(
            [
                self.source_field,
                kit.pick_button(
                    ft.Icons.FOLDER_OPEN_OUTLINED,
                    "選擇資料夾；Web 不支援時請直接輸入路徑",
                    self._pick_source,
                ),
                self.discover_button,
            ],
            spacing=8,
        )
        return kit.section_card(
            "來源辨識",
            ft.Column(
                [
                    path_row,
                    ft.Row([self.version_field, self.scan_mode], spacing=10),
                    self.root_dropdown,
                    self.source_summary,
                    kit.hint_text(
                        "會有限制搜尋深度、拒絕 symlink/junction/reparse point；多個根目錄不會自動合併。"
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="dia",
        )

    def _build_execute_panel(self) -> ft.Control:
        return kit.section_card(
            "執行",
            ft.Column(
                [
                    ft.Row([self.include_kubejs, self.include_ftb], spacing=16),
                    ft.Row([self.translate_missing], spacing=16),
                    self.execution_hint,
                    ft.Row(
                        [self.preview_button, self.publish_button, self.cancel_button],
                        wrap=True,
                        spacing=10,
                    ),
                    self.progress,
                    self.run_summary,
                    kit.hint_text(
                        "啟用「LM 翻譯待處理」會在同一個流水線 session 呼叫共用 ModDB LM 翻譯入口；Smart Review 仍沿用既有校對頁。"
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
            tone="ench",
        )

    def _build_output_panel(self) -> ft.Control:
        return kit.section_card(
            "輸出",
            ft.Column(
                [
                    self.output_field,
                    self.output_summary,
                    kit.hint_text(
                        "輸出是可覆蓋模組包根目錄的 overlay：kubejs/... 與 config/ftbquests/...；不會產生 Resource Pack zip。"
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.FOLDER_ZIP_OUTLINED,
            tone="gold",
        )

    def _build_logs_panel(self) -> ft.Control:
        return kit.section_card(
            "紀錄",
            ft.Column(
                [
                    self.log_view,
                    ft.Text(
                        "每次執行會產生 run_id 與 manifest.json，保存來源 scope、kind 統計、DB 結果、輸出驗證與問題分類。",
                        size=12,
                        color=C.MUTED,
                    ),
                ],
                spacing=10,
            ),
            icon=ft.Icons.TERMINAL,
            tone="gold",
        )

    def _select_tab(self, key: str) -> None:
        for name, panel in self._panels.items():
            panel.visible = name == key
        update_regions(self)

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
        self.discover_button.disabled = True
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
                self.discover_button.disabled = False
                return
            self.source_summary.value = f"來源分析失敗：{exc}"
            self.source_summary.color = C.RED
        if (
            generation != self._source_generation
            or source != (self.source_field.value or "").strip()
        ):
            self.discover_button.disabled = False
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
        self.discover_button.disabled = False
        self._safe_update()

    async def _preview_run(self, _e=None) -> None:
        await self._start(dry_run=True, publish=False)

    async def _publish_run(self, _e=None) -> None:
        await self._start(dry_run=False, publish=True)

    async def _start(self, *, dry_run: bool, publish: bool) -> None:
        if self._running:
            show_snack(self._page, "已有 KubeJS／FTB 流水線執行中", C.GOLD)
            return
        if not (self.source_field.value or "").strip():
            show_snack(self._page, "請先輸入來源路徑", C.RED)
            return
        if not self._candidates:
            await self._discover()
        if len(self._candidates) > 1 and not self.root_dropdown.value:
            show_snack(self._page, "來源有多個候選，請先明確選擇根目錄", C.GOLD)
            self._select_tab("source")
            return
        if not (self.include_kubejs.value or self.include_ftb.value):
            show_snack(self._page, "至少選擇 KubeJS 或 FTB Quests", C.RED)
            return
        output = (self.output_field.value or "").strip() or None
        if publish and not output:
            show_snack(self._page, "正式發布前請填寫 overlay 輸出目錄", C.RED)
            self._select_tab("output")
            return
        self.session = tag_session(
            TaskSession(max_logs=2000),
            "KubeJS / FTB 流水線",
            "kubejs_ftb_pipeline",
            page=self._page,
        )
        self._running = True
        self._set_running(True)
        self.progress.value = 0
        self.log_view.clear()
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
            self._set_running(False)
            show_snack(self._page, "應用程式正在關閉，未啟動流水線", C.GOLD)
            self._safe_update()
            return
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
        if status in {"DONE", "ERROR"}:
            self._running = False
            self._set_running(False)
            if status == "ERROR":
                set_chip_status(self.status_chip, "失敗", "red")
            elif summary.get("status") == "CANCELLED":
                set_chip_status(self.status_chip, "已取消", "gold")
            elif summary.get("state") == "done_with_warnings":
                set_chip_status(self.status_chip, "完成但有警告", "gold")
            else:
                set_chip_status(self.status_chip, "完成", "em")
            self._render_summary(summary)
        self._safe_update()

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
        self._select_tab("output" if output else "logs")

    def _set_running(self, running: bool) -> None:
        self.preview_button.disabled = running
        self.publish_button.disabled = running
        self.discover_button.disabled = running
        self.cancel_button.disabled = not running

    def will_unmount(self) -> None:
        self._poller.stop()

    def did_mount(self) -> None:
        if self.session is not None and self._running and not self._poller.running:
            self._poller.start(self._page, self._poll)

    def _safe_update(self) -> None:
        update_regions(self)


__all__ = ["KubejsFtbPipelineView"]
