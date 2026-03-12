"""app/views/translation_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import threading
import time

import flet as ft

# UI 共用元件：抽出重複的卡片/按鈕樣式，集中在 app.ui
from app.ui.components import primary_button, secondary_button, styled_card

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
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`__init__`, `Chip`, `ProgressBar`
        
        回傳：None
        """
        super().__init__(expand=True, spacing=16)
        self.page = page
        self.file_picker = file_picker
        self._picker_target_field: ft.TextField | None = None

        self.session = None
        self._ui_timer_running = False

        # 右側共用狀態與日誌
        self.status_chip = ft.Chip(label=ft.Text("尚未開始"), bgcolor=ft.Colors.GREY_200)
        self.progress = ft.ProgressBar(value=0, height=8, bgcolor=ft.Colors.GREY_200, color=ft.Colors.BLUE)
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
            bgcolor=ft.Colors.WHITE,
            border=ft.border.all(1, ft.Colors.BLACK12),
            content=ft.Row(
                [
                    #ft.Icon(ft.Icons.INFO_OUTLINE, size=18, color=ft.Colors.BLUE_GREY_700),
                    #ft.Text("本頁已與 Extractor 風格對齊；僅調整 UI 樣式，不影響流程邏輯。"),
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
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Row`
        
        回傳：依函式內 return path。
        """
        return ft.Row(
            [
                ft.Container(expand=True, content=field),
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                    icon_color=ft.Colors.BLUE_GREY_700,
                    tooltip="選擇資料夾",
                    on_click=lambda e: self._pick_directory_into(field),
                ),
            ],
            spacing=6,
        )

    def _action_row(
        self,
        *,
        on_start,
        on_dry_run,
        on_reset,
        trailing: list[ft.Control] | None = None,
    ) -> ft.Control:
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Row`
        
        回傳：依函式內 return path。
        """
        controls = [
            primary_button(
                "開始翻譯",
                icon=ft.Icons.PLAY_ARROW,
                tooltip="依照目前設定執行完整翻譯流程",
                on_click=on_start,
            ),
            secondary_button(
                "Dry-run 開始模擬翻譯",
                icon=ft.Icons.SEARCH,
                tooltip="依照目前設定執行翻譯流程，但不實際修改檔案",
                on_click=on_dry_run,
            ),
            ft.TextButton(
                "Reset",
                icon=ft.Icons.REFRESH,
                tooltip="重置輸入與輸出資料夾，並恢復所有步驟為預設值",
                on_click=on_reset,
            ),
        ]
        if trailing:
            controls.extend(trailing)
        return ft.Row(controls=controls, wrap=True, spacing=10)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------
    def _build_ftb_tab(self) -> ft.Control:
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`TextField`, `Checkbox`
        
        回傳：依函式內 return path。
        """
        self.ftb_in_dir = ft.TextField(
            label="輸入資料夾（模組包根目錄）",
            hint_text="例如：C:\\\\Modpack",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER,
        )
        self.ftb_out_dir = ft.TextField(
            label="輸出資料夾（可選）",
            hint_text="留空使用 <input>/Output",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        self.ftb_step_export = ft.Checkbox(label="Step 1：Export Raw（抽取）", value=True)
        self.ftb_step_clean = ft.Checkbox(label="Step 2：Clean（補洞/產生待翻譯）", value=True)
        self.ftb_step_translate = ft.Checkbox(label="Step 3：LM 翻譯（待翻譯 JSON）", value=True)
        self.ftb_step_inject = ft.Checkbox(label="Step 4：Inject（寫回 zh_tw/*.snbt）", value=True)
        self.ftb_write_new_cache = ft.Switch(label="寫入新快取（write_new_cache）", value=True)

        return ft.Column(
            [
                styled_card(
                    title="路徑設定",
                    icon=ft.Icons.FOLDER,
                    content=ft.Column(
                        [
                            self._path_row(self.ftb_in_dir),
                            self._path_row(self.ftb_out_dir),
                        ],
                        spacing=10,
                    ),
                ),
                styled_card(
                    title="步驟與選項",
                    icon=ft.Icons.FACT_CHECK,
                    content=ft.Column(
                        [
                            self.ftb_step_export,
                            self.ftb_step_clean,
                            self.ftb_step_translate,
                            self.ftb_step_inject,
                        ],
                        spacing=6,
                    ),
                ),
                self._action_row(
                    on_start=lambda e: self._run_ftb(dry_run=False),
                    on_dry_run=lambda e: self._run_ftb(dry_run=True),
                    on_reset=lambda e: self._reset_ftb_inputs(),
                    trailing=[self.ftb_write_new_cache],
                ),
            ],
            spacing=12,
            expand=True,
        )

    def _build_kjs_tab(self) -> ft.Control:
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`TextField`, `Checkbox`
        
        回傳：依函式內 return path。
        """
        self.kjs_in_dir = ft.TextField(
            label="輸入資料夾（模組包根目錄）",
            hint_text="例如：C:\\\\Modpack",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER,
        )
        self.kjs_out_dir = ft.TextField(
            label="輸出資料夾（可選）",
            hint_text="留空使用 <input>/Output",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        self.kjs_step_extract = ft.Checkbox(label="Step 1：Export Raw + Clean", value=True)
        self.kjs_step_translate = ft.Checkbox(label="Step 2：LM 翻譯（待翻譯 JSON）", value=True)
        self.kjs_step_inject = ft.Checkbox(label="Step 3：Inject 回 scripts", value=True)
        self.kjs_write_new_cache = ft.Switch(label="寫入新快取（write_new_cache）", value=True)

        return ft.Column(
            [
                styled_card(
                    title="路徑設定",
                    icon=ft.Icons.FOLDER,
                    content=ft.Column(
                        [
                            self._path_row(self.kjs_in_dir),
                            self._path_row(self.kjs_out_dir),
                        ],
                        spacing=10,
                    ),
                ),
                styled_card(
                    title="步驟與選項",
                    icon=ft.Icons.FACT_CHECK,
                    content=ft.Column(
                        [
                            self.kjs_step_extract,
                            self.kjs_step_translate,
                            self.kjs_step_inject,
                        ],
                        spacing=6,
                    ),
                ),
                self._action_row(
                    on_start=lambda e: self._run_kjs(dry_run=False),
                    on_dry_run=lambda e: self._run_kjs(dry_run=True),
                    on_reset=lambda e: self._reset_kjs_inputs(),
                    trailing=[self.kjs_write_new_cache],
                ),
            ],
            spacing=12,
            expand=True,
        )

    def _build_md_tab(self) -> ft.Control:
        """建立此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`TextField`, `Checkbox`
        
        回傳：依函式內 return path。
        """
        self.md_in_dir = ft.TextField(
            label="輸入資料夾（遞迴掃描 .md）",
            hint_text="例如：C:\\\\Modpack\\\\config\\\\patchouli_books",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER,
        )
        self.md_out_dir = ft.TextField(
            label="輸出資料夾（可選）",
            hint_text="留空使用 <input>/Output/md",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=14,
            prefix_icon=ft.Icons.FOLDER_COPY,
        )

        self.md_step_extract = ft.Checkbox(label="Step 1：Extract（產生待翻譯）", value=True)
        self.md_step_translate = ft.Checkbox(label="Step 2：LM 翻譯（待翻譯 JSON）", value=True)
        self.md_step_inject = ft.Checkbox(label="Step 3：Inject（寫回 md）", value=True)
        self.md_write_new_cache = ft.Switch(label="寫入新快取（write_new_cache）", value=True)
        self.md_lang_mode = ft.Dropdown(
            label="抽取語言模式（lang_mode）",
            value="non_cjk_only",
            dense=True,
            options=[
                ft.dropdown.Option(key="non_cjk_only", text="僅抽取非中文（non_cjk_only）"),
                ft.dropdown.Option(key="cjk_only", text="僅抽取中文（cjk_only）"),
                ft.dropdown.Option(key="all", text="抽取全部（all）"),
            ],
        )

        return ft.Column(
            [
                styled_card(
                    title="路徑設定",
                    icon=ft.Icons.FOLDER,
                    content=ft.Column(
                        [
                            self._path_row(self.md_in_dir),
                            self._path_row(self.md_out_dir),
                            self.md_lang_mode,
                        ],
                        spacing=10,
                    ),
                ),
                styled_card(
                    title="步驟與選項",
                    icon=ft.Icons.FACT_CHECK,
                    content=ft.Column(
                        [
                            self.md_step_extract,
                            self.md_step_translate,
                            self.md_step_inject,
                        ],
                        spacing=6,
                    ),
                ),
                self._action_row(
                    on_start=lambda e: self._run_md(dry_run=False),
                    on_dry_run=lambda e: self._run_md(dry_run=True),
                    on_reset=lambda e: self._reset_md_inputs(),
                    trailing=[self.md_write_new_cache],
                ),
            ],
            spacing=12,
            expand=True,
        )

    # ------------------------------------------------------------------
    # directory picker
    # ------------------------------------------------------------------
    def _pick_directory_into(self, target: ft.TextField):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`get_directory_path`
        
        回傳：None
        """
        self._picker_target_field = target
        self.file_picker.on_result = self._on_dir_picked
        self.file_picker.get_directory_path()

    def _on_dir_picked(self, e: ft.FilePickerResultEvent):
        """處理此函式的工作（細節以程式碼為準）。
        
        回傳：None
        """
        if not e.path:
            return
        if self._picker_target_field is not None:
            self._picker_target_field.value = e.path
        self.page.update()

    # ------------------------------------------------------------------
    # runners
    # ------------------------------------------------------------------
    def _run_ftb(self, *, dry_run: bool):
        """執行此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`strip`, `_set_status`, `clear`
        
        回傳：None
        """
        in_dir = (self.ftb_in_dir.value or "").strip()
        if not in_dir:
            self._show_snack("請先選擇輸入資料夾", ft.Colors.RED_600)
            return
        if run_ftb_translation_service is None:
            self._show_snack("FTB service 尚未可用", ft.Colors.RED_600)
            return
        if TaskSession is None:
            self._show_snack("TaskSession 尚未可用", ft.Colors.RED_600)
            return

        out_dir = (self.ftb_out_dir.value or "").strip() or None
        self._set_status("模擬執行" if dry_run else "執行中", ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200)
        self.progress.value = 0
        self.log_view.controls.clear()
        self.page.update()

        self.session = TaskSession()
        try:
            self.session.start()
        except Exception:
            pass

        def worker():
            """處理此函式的工作（細節以程式碼為準）。
            
            - 主要包裝：`run_ftb_translation_service`
            
            回傳：None
            """
            try:
                run_ftb_translation_service(
                    in_dir,
                    self.session,
                    output_dir=out_dir,
                    dry_run=dry_run,
                    step_export=bool(self.ftb_step_export.value),
                    step_clean=bool(self.ftb_step_clean.value),
                    step_translate=bool(self.ftb_step_translate.value),
                    step_inject=bool(self.ftb_step_inject.value),
                    write_new_cache=bool(self.ftb_write_new_cache.value),
                )
            except Exception as ex:
                try:
                    if hasattr(self.session, "add_log"):
                        self.session.add_log(f"[UI] 服務執行失敗：{ex}")
                    if hasattr(self.session, "set_error"):
                        self.session.set_error(str(ex))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        self._start_ui_timer()

    def _run_kjs(self, *, dry_run: bool):
        """執行此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`strip`, `_set_status`, `clear`
        
        回傳：None
        """
        in_dir = (self.kjs_in_dir.value or "").strip()
        if not in_dir:
            self._show_snack("請先選擇輸入資料夾", ft.Colors.RED_600)
            return
        if run_kubejs_tooltip_service is None:
            self._show_snack("KubeJS service 尚未可用", ft.Colors.RED_600)
            return
        if TaskSession is None:
            self._show_snack("TaskSession 尚未可用", ft.Colors.RED_600)
            return

        out_dir = (self.kjs_out_dir.value or "").strip() or None
        self._set_status("模擬執行" if dry_run else "執行中", ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200)
        self.progress.value = 0
        self.log_view.controls.clear()
        self.page.update()

        self.session = TaskSession()
        try:
            self.session.start()
        except Exception:
            pass

        def worker():
            """處理此函式的工作（細節以程式碼為準）。
            
            - 主要包裝：`run_kubejs_tooltip_service`
            
            回傳：None
            """
            try:
                run_kubejs_tooltip_service(
                    in_dir,
                    self.session,
                    output_dir=out_dir,
                    dry_run=dry_run,
                    step_extract=bool(self.kjs_step_extract.value),
                    step_translate=bool(self.kjs_step_translate.value),
                    step_inject=bool(self.kjs_step_inject.value),
                    write_new_cache=bool(self.kjs_write_new_cache.value),
                )
            except Exception as ex:
                try:
                    if hasattr(self.session, "add_log"):
                        self.session.add_log(f"[UI] 服務執行失敗：{ex}")
                    if hasattr(self.session, "set_error"):
                        self.session.set_error(str(ex))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        self._start_ui_timer()

    def _run_md(self, *, dry_run: bool):
        """執行此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`strip`, `_set_status`, `clear`
        
        回傳：None
        """
        in_dir = (self.md_in_dir.value or "").strip()
        if not in_dir:
            self._show_snack("請先選擇輸入資料夾", ft.Colors.RED_600)
            return
        if run_md_translation_service is None:
            self._show_snack("MD service 尚未可用", ft.Colors.RED_600)
            return
        if TaskSession is None:
            self._show_snack("TaskSession 尚未可用", ft.Colors.RED_600)
            return

        out_dir = (self.md_out_dir.value or "").strip() or None
        self._set_status("模擬執行" if dry_run else "執行中", ft.Colors.AMBER_200 if dry_run else ft.Colors.BLUE_200)
        self.progress.value = 0
        self.log_view.controls.clear()
        self.page.update()

        self.session = TaskSession()
        try:
            self.session.start()
        except Exception:
            pass

        def worker():
            """處理此函式的工作（細節以程式碼為準）。
            
            - 主要包裝：`run_md_translation_service`
            
            回傳：None
            """
            try:
                run_md_translation_service(
                    input_dir=in_dir,
                    session=self.session,
                    output_dir=out_dir,
                    dry_run=dry_run,
                    step_extract=bool(self.md_step_extract.value),
                    step_translate=bool(self.md_step_translate.value),
                    step_inject=bool(self.md_step_inject.value),
                    write_new_cache=bool(self.md_write_new_cache.value),
                    lang_mode=str(self.md_lang_mode.value or "non_cjk_only"),
                )
            except Exception as ex:
                try:
                    if hasattr(self.session, "add_log"):
                        self.session.add_log(f"[UI] 服務執行失敗：{ex}")
                    if hasattr(self.session, "set_error"):
                        self.session.set_error(str(ex))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        self._start_ui_timer()

    # ------------------------------------------------------------------
    # ui poller
    # ------------------------------------------------------------------
    def _start_ui_timer(self):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`start`
        
        回傳：None
        """
        if self._ui_timer_running:
            return
        self._ui_timer_running = True

        def loop():
            """處理此函式的工作（細節以程式碼為準）。
            
            回傳：None
            """
            while self._ui_timer_running:
                time.sleep(0.1)
                if self.session is None:
                    continue

                try:
                    snap = self.session.snapshot()
                except Exception:
                    continue

                try:
                    self.progress.value = float(snap.get("progress", 0) or 0)
                except Exception:
                    self.progress.value = 0

                logs = snap.get("logs", []) or []
                try:
                    tail = logs[-250:]
                    self.log_view.controls = [
                        ft.Text(line, size=13, color=ft.Colors.GREY_100) for line in tail
                    ]
                except Exception:
                    pass

                status = (snap.get("status") or "").upper()
                if status == "DONE":
                    self._set_status("任務完成", ft.Colors.GREEN_200)
                    self._ui_timer_running = False
                elif status == "ERROR":
                    self._set_status("任務發生錯誤", ft.Colors.RED_200)
                    self._ui_timer_running = False

                self.page.update()

        threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str):
        """設定此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`Text`
        
        回傳：None
        """
        self.status_chip.label = ft.Text(text)
        self.status_chip.bgcolor = color
        self.page.update()

    def _append_log(self, line: str):
        """處理此函式的工作（細節以程式碼為準）。
        
        回傳：None
        """
        self.log_view.controls.append(ft.Text(line, size=13, color=ft.Colors.GREY_100))
        if len(self.log_view.controls) > 400:
            self.log_view.controls = self.log_view.controls[-300:]
        self.page.update()

    def _clear_logs(self):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`clear`
        
        回傳：None
        """
        self.log_view.controls.clear()
        self.page.update()

    # ------------------------------------------------------------------
    # reset actions
    # ------------------------------------------------------------------
    def _reset_ftb_inputs(self):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`_set_status`, `_append_log`
        
        回傳：None
        """
        self.ftb_in_dir.value = ""
        self.ftb_out_dir.value = ""
        self.ftb_step_export.value = True
        self.ftb_step_clean.value = True
        self.ftb_step_translate.value = True
        self.ftb_step_inject.value = True
        self.ftb_write_new_cache.value = True
        self._set_status("尚未開始", ft.Colors.GREY_200)
        self.progress.value = 0
        self._append_log("[UI] 已重置：FTB Quests 輸入已清空")
        self.page.update()

    def _reset_kjs_inputs(self):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`_set_status`, `_append_log`
        
        回傳：None
        """
        self.kjs_in_dir.value = ""
        self.kjs_out_dir.value = ""
        self.kjs_step_extract.value = True
        self.kjs_step_translate.value = True
        self.kjs_step_inject.value = True
        self.kjs_write_new_cache.value = True
        self._set_status("尚未開始", ft.Colors.GREY_200)
        self.progress.value = 0
        self._append_log("[UI] 已重置：KubeJS 輸入已清空")
        self.page.update()

    def _reset_md_inputs(self):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`_set_status`, `_append_log`
        
        回傳：None
        """
        self.md_in_dir.value = ""
        self.md_out_dir.value = ""
        self.md_step_extract.value = True
        self.md_step_translate.value = True
        self.md_step_inject.value = True
        self.md_write_new_cache.value = True
        self.md_lang_mode.value = "non_cjk_only"
        self._set_status("尚未開始", ft.Colors.GREY_200)
        self.progress.value = 0
        self._append_log("[UI] 已重置：Markdown 輸入已清空")
        self.page.update()

    def _show_snack(self, message: str, color: str = ft.Colors.RED_600):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`SnackBar`
        
        回傳：None
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
