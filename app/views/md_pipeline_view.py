"""Markdown 指南的來源到資源包 overlay 流水線頁面。

與 KubeJS／FTB 流水線頁相同的工作台：左側路徑與步驟，右側狀態與日誌。流程是
分析來源 → 預覽（唯讀）→ 同步 ModDB（可選 LM 翻譯）→ 輸出資源包；來源資料夾不會被修改。
選到模組包根目錄時自動列出 ``kubejs``、``resourcepacks/*`` 供勾選；其他資料夾直接掃描。
"""

from __future__ import annotations

import asyncio

import flet as ft

from app.services_impl.md_pipeline_service import (
    discover_md_sources,
    get_md_use_zh_cn_default,
    run_md_pipeline_service,
)
from app.services_impl.moddb_service import load_db_settings
from app.services_impl.pack_version_service import (
    detect_minecraft_version,
    known_minecraft_versions,
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
from app.views.moddb.lm_db_options import LmDbOptions
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
_SUGGESTION_LIMIT = 8


class MarkdownPipelineView(ft.Column):
    """Markdown 流水線工作台；同一時間只允許一個流水線執行，避免兩個入口同時寫入 ModDB。"""

    _active_run: MarkdownPipelineView | None = None

    def __init__(
        self, page: ft.Page, file_picker: ft.FilePicker, *, embedded: bool = False
    ):
        super().__init__(expand=True, spacing=14, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self.file_picker = file_picker
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
        self._version_auto = False
        self._build_controls(page)
        self._build_layout()

    # ------------------------------------------------------------ 控制項

    def _build_controls(self, page: ft.Page) -> None:
        self.source_field = kit.text_field(
            "來源資料夾／模組包根目錄",
            hint="任意資料夾（含抽出的 jar 內容）；選模組包根目錄會列出 kubejs 與 resourcepacks",
            mono=True,
            expand=True,
            path=True,
            on_change=self._source_changed,
        )
        self.source_summary = ft.Text(
            "尚未分析來源。", size=12, color=C.MUTED, selectable=True
        )
        self.candidate_column = ft.Column([], spacing=2, tight=True)
        self.version_hint = kit.hint_text(
            "分析來源後會自動帶入版本，也可輸入（例如 1.21）。"
        )
        self.version_field = kit.dropdown(
            label="遊戲版本",
            hint_text="輸入如 1.21 篩選版本",
            editable=True,
            enable_filter=True,
            options=[],
            width=260,
            on_text_change=self._version_typed,
            on_select=self._version_selected,
        )
        kit.set_dropdown_options(
            self.version_field,
            [(v, v) for v in known_minecraft_versions()[:_SUGGESTION_LIMIT]],
        )
        self.output_field = kit.text_field(
            "輸出資料夾（正式發布必填）",
            hint="例如：D:/翻譯輸出/ATM10-guides；輸出是可放進 resourcepacks 的資料夾",
            mono=True,
            expand=True,
            path=True,
        )
        self.translate_missing = ft.Checkbox(label="LM 翻譯待處理", value=False)
        self.read_zh_cn = ft.Checkbox(
            label="讀取 zh_cn 檔（沒有 zh_tw 時當底稿）",
            value=get_md_use_zh_cn_default(),
        )
        self.lm_db_options = LmDbOptions(self._page.update)
        self.status_chip = make_status_chip("尚未開始")
        self.progress_status = ft.Text("等待任務啟動...", size=12, color=C.MUTED)
        self.progress = kit.progress_bar(0, "em", height=8)
        self.run_summary = ft.Text(
            "尚未執行。", size=12, color=C.MUTED, selectable=True
        )
        self.output_summary = ft.Text(
            "尚未產生輸出預覽。", size=12, color=C.MUTED, selectable=True
        )
        self.result_text = ft.Text("", size=12, color=C.TEXT, selectable=True)
        self.result_card = kit.section_card(
            "執行結果",
            self.result_text,
            icon=ft.Icons.FACT_CHECK_OUTLINED,
            tone="ench",
        )
        self.result_card.visible = False
        self.progress_panel = PipelineProgressPanel(
            page,
            on_cancel=self._cancel,
            step_names=["分析來源", "同步 ModDB", "LM 翻譯", "輸出資源包"],
            log_height=180,
            progress=self.progress,
        )
        self.log_view = self.progress_panel.log_view
        self.cancel_button = self.progress_panel.cancel_button
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
        status_panel = build_pipeline_status_card(
            progress=self.progress,
            progress_status=self.progress_status,
            status_chip=self.status_chip,
            summary=ft.Column([self.run_summary, self.output_summary], spacing=6),
            title="執行狀態",
            icon=ft.Icons.TIMELINE,
            tone="gold",
        )
        self.progress_panel.attach_idle_status_card(status_panel)
        self.workbench_view = build_pipeline_workbench(
            show_header=not self._embedded,
            title="Markdown 指南流水線",
            subtitle="來源分析 → ModDB 同步 → LM 翻譯 → 資源包輸出；與 KubeJS／FTB 流水線相同的操作方式",
            icon=ft.Icons.ARTICLE_OUTLINED,
            tone="em",
            left_controls=[
                self._build_paths_card(),
                self._build_options_card(),
                self._build_steps_card(),
            ],
            right_controls=[
                status_panel,
                self.progress_panel.container,
                self.result_card,
            ],
            actions=[self._one_click_button],
        )
        self.controls = [self.workbench_view]

    def _build_paths_card(self) -> ft.Control:
        return kit.section_card(
            "專案路徑",
            ft.Column(
                [
                    ft.Column(
                        [
                            kit.section_label("讀取來源 *"),
                            build_pipeline_input_row(
                                self.source_field,
                                kit.pick_button(
                                    ft.Icons.FOLDER_OPEN,
                                    "選擇來源資料夾；Web 不支援時請直接輸入路徑",
                                    self._pick_source,
                                ),
                            ),
                        ],
                        spacing=6,
                    ),
                    self.source_summary,
                    self.candidate_column,
                    ft.Column(
                        [
                            kit.section_label("輸出資料夾"),
                            build_pipeline_input_row(
                                self.output_field,
                                kit.pick_button(
                                    ft.Icons.FOLDER_OPEN,
                                    "選擇輸出資料夾",
                                    self._pick_output,
                                ),
                            ),
                        ],
                        spacing=6,
                    ),
                ],
                spacing=14,
            ),
            icon=ft.Icons.FOLDER_OPEN,
            tone="em",
        )

    def _build_options_card(self) -> ft.Control:
        return kit.section_card(
            "版本與翻譯設定",
            ft.Column(
                [
                    self.version_field,
                    self.version_hint,
                    self.read_zh_cn,
                    self.translate_missing,
                    self.lm_db_options.card,
                ],
                spacing=10,
            ),
            icon=ft.Icons.TUNE,
            tone="ench",
        )

    def _build_steps_card(self) -> ft.Control:
        return build_pipeline_steps_card(
            [
                PipelineStepSpec(
                    1,
                    "分析來源",
                    "找出可掃描的指南資料夾，並偵測遊戲版本",
                    ft.Icons.FIND_IN_PAGE_OUTLINED,
                    "dia",
                    self._discover_clicked,
                ),
                PipelineStepSpec(
                    2,
                    "預覽同步計畫",
                    "唯讀掃描並計算 ModDB 變更與輸出數量，不寫入任何東西",
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
                    "輸出資源包",
                    "把 DB 的繁中譯文輸出成資源包資料夾",
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

    # ------------------------------------------------------------ 版本

    def _version_typed(self, e=None) -> None:
        text = (
            e.control.text if e is not None and getattr(e, "control", None) else ""
        ) or ""
        self._version_auto = False
        matches = [v for v in known_minecraft_versions() if v.startswith(text.strip())]
        kit.set_dropdown_options(
            self.version_field, [(v, v) for v in matches[:_SUGGESTION_LIMIT]]
        )
        self.version_field.value = text or None
        self._safe_update()

    def _version_selected(self, e=None) -> None:
        self._version_auto = False

    def _apply_detected_version(self, version: str | None) -> None:
        """依來源偵測結果帶入版本；手動輸入過的版本不會被覆蓋。"""
        manual = bool(self.version_field.value) and not self._version_auto
        if version is None:
            self.version_hint.value = "未偵測到版本，請輸入或選擇（例如 1.21）。"
            return
        if manual:
            self.version_hint.value = f"來源偵測到 {version}，保留你手動輸入的版本。"
            return
        keys = [option.key for option in self.version_field.options]
        if version not in keys:
            kit.set_dropdown_options(
                self.version_field, [(version, version)] + [(k, k) for k in keys]
            )
        self.version_field.value = version
        self._version_auto = True
        self.version_hint.value = f"已自動偵測：{version}（可手動改）"

    # ------------------------------------------------------------ 來源

    def _source_changed(self, _e=None) -> None:
        """來源路徑變更後清除舊候選，避免執行時沿用前一個來源。"""
        self._source_generation += 1
        self._candidates = []
        self.candidate_column.controls = []
        if self._version_auto:
            self.version_field.value = None
            self._version_auto = False
        self.source_summary.value = "來源已變更，請重新分析來源。"
        self.source_summary.color = C.GOLD
        self._safe_update()

    async def _pick_source(self, _e=None) -> None:
        result = await self.file_picker.get_directory_path()
        if result:
            self.source_field.value = result
            self._source_changed()
            await self._discover()
        else:
            self.source_summary.value = "檔案選擇器沒有回傳路徑；請直接輸入來源。"
            self.source_summary.color = C.GOLD
            self._safe_update()

    async def _pick_output(self, _e=None) -> None:
        result = await self.file_picker.get_directory_path()
        if result:
            self.output_field.value = result
            self._safe_update()

    async def _discover_clicked(self, _e=None) -> None:
        await self._discover()

    def _is_current_source(self, source: str, generation: int) -> bool:
        return (
            generation == self._source_generation
            and source == (self.source_field.value or "").strip()
        )

    async def _discover(self) -> None:
        source = (self.source_field.value or "").strip()
        if not source:
            self.source_summary.value = "請先輸入來源路徑。"
            self.source_summary.color = C.RED
            self._safe_update()
            return
        generation = self._source_generation
        detected = await asyncio.to_thread(detect_minecraft_version, source)
        if not self._is_current_source(source, generation):
            return
        self._apply_detected_version(detected)
        try:
            candidates = await asyncio.to_thread(discover_md_sources, source)
        except (OSError, RuntimeError, ValueError) as exc:
            self.source_summary.value = f"來源分析失敗：{exc}"
            self.source_summary.color = C.RED
            self._safe_update()
            return
        if not self._is_current_source(source, generation):
            return
        self._candidates = candidates
        self.candidate_column.controls = [
            ft.Checkbox(label=path, value=True) for path in candidates
        ]
        self.source_summary.value = (
            f"找到 {len(candidates)} 個來源資料夾，可取消勾選不要處理的。"
            if candidates
            else "找不到可掃描的資料夾；請確認路徑存在。"
        )
        self.source_summary.color = C.EM if candidates else C.RED
        self._safe_update()

    def selected_sources(self) -> list[str]:
        """勾選的來源資料夾；還沒分析來源時用輸入框內的路徑本身。"""
        checked = [
            str(box.label)
            for box in self.candidate_column.controls
            if isinstance(box, ft.Checkbox) and box.value
        ]
        if checked:
            return checked
        if self._candidates:
            return []
        source = (self.source_field.value or "").strip()
        return [source] if source else []

    # ------------------------------------------------------------ 執行

    def translation_db_choice(self) -> tuple[bool, str | None]:
        """LM 翻譯使用的資料庫設定（與一鍵製作相同的快照）：(是否使用資料庫, 目標版本或 None)。"""
        snapshot = self.lm_db_options.snapshot_for_run(load_db_settings())
        return bool(snapshot.use_db), (snapshot.version or None)

    def _run_blocker(self, publish: bool) -> tuple[str, str] | None:
        active = MarkdownPipelineView._active_run
        if self._running or (active is not None and active is not self):
            return "已有 Markdown 流水線執行中", C.GOLD
        if not (self.source_field.value or "").strip():
            return "請先輸入來源路徑", C.RED
        if not self.selected_sources():
            return "請至少勾選一個來源資料夾", C.RED
        if not (self.version_field.value or "").strip():
            return "請先選擇或輸入遊戲版本", C.RED
        if publish and not (self.output_field.value or "").strip():
            return "正式發布前請填寫輸出資料夾", C.RED
        return None

    async def _preview_run(self, _e=None) -> None:
        await self._start(dry_run=True, publish=False)

    async def _sync_run(self, _e=None) -> None:
        await self._start(dry_run=False, publish=False)

    async def _publish_run(self, _e=None) -> None:
        await self._start(dry_run=False, publish=True)

    def _one_click_clicked(self, _e=None) -> None:
        """一鍵執行：先唯讀預覽，成功後由確認對話框決定是否發布。"""
        blocker = self._run_blocker(False)
        if blocker is not None:
            show_snack(self._page, blocker[0], blocker[1])
            return
        self._one_click_stage = "preview"
        self._page.run_task(self._run_one_click_preview)

    async def _run_one_click_preview(self) -> None:
        await self._start(dry_run=True, publish=False)
        if not self._running:
            self._one_click_stage = None

    async def _start(self, *, dry_run: bool, publish: bool) -> None:
        blocker = self._run_blocker(publish)
        if blocker is not None:
            show_snack(self._page, blocker[0], blocker[1])
            return
        output = (self.output_field.value or "").strip() or None
        sources = self.selected_sources()
        version = (self.version_field.value or "").strip()
        lm_use_db, lm_version = self.translation_db_choice()
        self.session = tag_session(
            TaskSession(max_logs=2000),
            "Markdown 流水線",
            "md_pipeline",
            page=self._page,
        )
        self._running = True
        self._current_dry_run = dry_run
        self._current_publish = publish
        self._set_running(True)
        self.progress_panel.start()
        self.result_card.visible = False
        self.progress.value = 0
        self.progress_panel.clear_logs()
        self.run_summary.value = "執行中…"
        self._safe_update()
        launched = launch_page_operation(
            self._page,
            lambda: run_md_pipeline_service(
                sources,
                version,
                self.session,
                output_root=output,
                dry_run=dry_run,
                publish=publish,
                translate_missing=bool(self.translate_missing.value),
                translation_version=lm_version,
                use_translation_db=lm_use_db,
                read_zh_cn=bool(self.read_zh_cn.value),
            ),
            name="Markdown 流水線",
            owner="md-pipeline",
            task_session=self.session,
        )
        if not launched:
            self._running = False
            MarkdownPipelineView._active_run = None
            self._set_running(False)
            show_snack(self._page, "應用程式正在關閉，未啟動流水線", C.GOLD)
            self._safe_update()
            return
        MarkdownPipelineView._active_run = self
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
            self._finish_run(status, summary)
        self._safe_update()

    def _finish_run(self, status: str, summary: dict) -> None:
        self._running = False
        if MarkdownPipelineView._active_run is self:
            MarkdownPipelineView._active_run = None
        self._set_running(False)
        cancelled = summary.get("status") == "CANCELLED"
        success = status != "ERROR" and not cancelled
        if status == "ERROR":
            set_chip_status(self.status_chip, "失敗", "red")
        elif cancelled:
            set_chip_status(self.status_chip, "已取消", "gold")
        elif summary.get("status") == "DONE_WITH_WARNINGS":
            set_chip_status(self.status_chip, "完成但有警告", "gold")
        else:
            set_chip_status(self.status_chip, "完成", "em")
        self.progress_panel.finish_all(
            success, cancelled=cancelled, dry_run=self._current_dry_run
        )
        self._render_summary(summary)
        if self._one_click_stage == "preview":
            self._one_click_stage = None
            self._offer_publish_confirmation(success, summary)

    def _sync_progress_stage(self, progress: float, status: str) -> None:
        if not self._running or status in {"DONE", "ERROR"}:
            return
        if progress < 0.35:
            self.progress_panel.set_step_running(1, "分析來源")
        elif progress < 0.65:
            self.progress_panel.set_step_running(2, "同步 ModDB")
        elif progress < 0.95 or not self._current_publish:
            self.progress_panel.set_step_running(3, "LM 翻譯／驗證")
        else:
            self.progress_panel.set_step_running(4, "輸出資源包")

    def _render_summary(self, summary: dict) -> None:
        if not summary or summary.get("status") == "CANCELLED":
            return
        translation = summary.get("translation") or {}
        translation_line = ""
        if translation:
            translation_line = (
                f"\nLM 翻譯：{translation.get('status', '—')}　"
                f"候選 {translation.get('candidates', 0):,}　"
                f"寫入 {translation.get('written', 0):,}"
                + (
                    f"　（{translation['reason']}）"
                    if translation.get("reason")
                    else ""
                )
            )
        self.run_summary.value = (
            f"狀態={summary.get('status', '—')}　"
            f"來源檔案 {summary.get('source_files', 0):,}/{summary.get('files_seen', 0):,}　"
            f"段落 {summary.get('items_found', 0):,}\n"
            f"DB 新增 {summary.get('new_entries', 0):,}　既有 {summary.get('existing_entries', 0):,}　"
            f"zh_cn 底稿 {summary.get('zh_cn_used_files', 0):,} 檔　"
            f"zh_tw 沿用 {summary.get('zh_tw_used_files', 0):,} 檔"
            f"{translation_line}"
        )
        self.output_summary.value = (
            f"輸出：計畫 {summary.get('output_planned', 0):,} 檔、"
            f"寫出 {summary.get('output_files', 0):,} 檔\n"
            f"已有譯文 {summary.get('blocks_translated', 0):,} 段、"
            f"尚無譯文 {summary.get('blocks_missing', 0):,} 段、"
            f"人工修改略過 {summary.get('skipped_manual_edit', 0):,} 檔"
        )
        self.result_text.value = (
            f"{self.run_summary.value}\n{self.output_summary.value}"
        )
        self.result_card.visible = True

    # ------------------------------------------------------------ 發布確認

    def _offer_publish_confirmation(self, success: bool, summary: dict) -> None:
        if not success or not summary:
            show_snack(self._page, "預覽未完成，未開始發布", C.GOLD)
            return
        issues = summary.get("issues") or []
        body = [
            ft.Text(line, size=13, color=C.TEXT, selectable=True)
            for line in (
                f"來源檔案 {summary.get('source_files', 0):,}　段落 {summary.get('items_found', 0):,}",
                f"DB 預計新增 {summary.get('new_entries', 0):,} 筆",
                "LM 翻譯：發布時翻譯待處理條目"
                if self.translate_missing.value
                else "LM 翻譯：未啟用（只輸出 DB 既有譯文）",
                f"輸出資料夾：{self.output_field.value or '（尚未填寫）'}",
                f"目前可輸出 {summary.get('output_planned', 0):,} 檔　掃描提醒 {len(issues):,} 項",
            )
        ]
        confirm = dialog_button(
            "確認執行並發布",
            self._confirm_publish,
            role="primary",
            icon=ft.Icons.PUBLISH,
        )
        self._publish_dialog = build_pipeline_dialog(
            self._page,
            title="確認發布資源包",
            content=ft.Column(body, spacing=10, tight=True),
            actions=[dialog_button("取消", self._close_publish_dialog), confirm],
            icon=ft.Icons.PUBLISH,
            tone="em",
        )
        present_page_dialog(self._page, self._publish_dialog)

    def _close_publish_dialog(self, _e=None) -> None:
        if self._publish_dialog is not None:
            close_page_dialog(self._page, self._publish_dialog)

    async def _confirm_publish(self, _e=None) -> None:
        self._close_publish_dialog()
        await self._start(dry_run=False, publish=True)

    # ------------------------------------------------------------ 生命週期

    def _set_running(self, running: bool) -> None:
        for button in self._run_buttons:
            button.disabled = running
        self.progress_panel.set_running(running)

    def will_unmount(self) -> None:
        self._poller.stop()

    def did_mount(self) -> None:
        if self.session is not None and self._running and not self._poller.running:
            self._poller.start(self._page, self._poll)

    def _safe_update(self) -> None:
        update_regions(self)


__all__ = ["MarkdownPipelineView"]
