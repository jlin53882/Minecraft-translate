"""app/views/extractor_view.py（JAR 提取頁）

提供兩種提取流程：
- Lang：從 mods/*.jar 提取語言檔案
- Book：從 mods/*.jar 提取 Patchouli 等手冊內容

維護重點：
- 提取屬於長時間 I/O 任務；UI 與背景執行緒透過 TaskSession 溝通。
- UI 端靠 poller 定期讀 snapshot，避免背景執行緒直接操作 UI 控制項。

本輪僅補 docstring/註解，不調整提取流程。
"""

# /minecraft_translator_flet/app/views/extractor_view.py
import flet as ft

# UI 共用元件：統一卡片樣式（避免每頁自己刻一套）
from app.ui.components import styled_card
import threading
import time
from pathlib import Path

from app.services_impl.pipelines.extract_service import (
    run_book_extraction_service,
    run_lang_extraction_service,
)
from app.task_session import TaskSession


class ExtractorView(ft.Column):
    """JAR 提取頁（UI）。

    設計概念：
    - 長任務全部寫入 TaskSession（log/progress/status）。
    - UI 只渲染 session 的快照，避免跨執行緒操作 UI 造成不穩定。

    維護注意：
    - 若新增新的提取模式，務必沿用同一套 session + poller 流程。
    - stats 欄位是 UI 顯示用途；不要在核心流程依賴它當正確性來源。
    """

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """`__init__`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`__init__`, `TaskSession`, `Event`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        super().__init__(expand=True, spacing=15)
        self.page = page
        self.file_picker = file_picker

        # ExtractorView 的長任務狀態全部收斂到 TaskSession。
        # 背景執行緒只寫 session，UI 端靠 poller 讀快照更新畫面，
        # 這樣提取流程與畫面狀態不會互相纏在一起。
        self.session = TaskSession(max_logs=2000)
        self._ui_poller_stop = threading.Event()
        self._last_rendered_log_count = 0
        
        # 提取統計
        self._extraction_stats = {
            'success': 0,
            'warnings': 0,
            'failures': 0,
            'total_files': 0
        }

        # ======================
        # UI Components
        # ======================
        
        # 1. Configuration Section Components
        self.mods_dir_textfield = ft.TextField(
            hint_text="C:\\Example\\Mods",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=15
        )
        
        self.output_dir_textfield = ft.TextField(
            hint_text="（未指定將自動產生）",
            expand=True,
            dense=True,
            border_color=ft.Colors.OUTLINE,
            text_size=14,
            content_padding=15
        )

        # 2. Action Buttons
        self.lang_button = ft.ElevatedButton(
            "提取 Lang",
            icon=ft.Icons.LANGUAGE,
            style=ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=ft.Colors.BLUE_700,
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=lambda e: self.start_extraction("lang"),
        )
        self.book_button = ft.ElevatedButton(
            "提取 Book",
            icon=ft.Icons.BOOK,
            style=ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=ft.Colors.GREEN_700,
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=lambda e: self.start_extraction("book"),
        )
        
        # 預覽按鈕
        self.preview_lang_button = ft.OutlinedButton(
            "預覽 Lang",
            icon=ft.Icons.PREVIEW,
            on_click=lambda e: self.show_preview("lang"),
        )
        self.preview_book_button = ft.OutlinedButton(
            "預覽 Book",
            icon=ft.Icons.PREVIEW,
            on_click=lambda e: self.show_preview("book"),
        )

        # 3. Status Display
        self.status_text = ft.Text("狀態：閒置", size=14, color=ft.Colors.GREY_700)
        self.progress_bar = ft.ProgressBar(value=0, visible=True, height=8, bgcolor=ft.Colors.GREY_200, color=ft.Colors.BLUE)

        # 4. Logs Console
        self.log_view = ft.ListView(
            expand=True,
            spacing=2,
            auto_scroll=True,
            padding=10,
        )

        # ======================
        # Layout Composition
        # ======================
        self.controls = [
            self._build_settings_card(),
            self._build_logs_card(),
        ]

    def _build_settings_card(self):
        """任務設定卡片。

        這裡改用共用的 styled_card：
        - 統一全 app 卡片 padding / border / divider
        - 要調整 UI 一致性時，只需要改 app.ui.components
        """

        return styled_card(
            title="任務設定",
            icon=ft.Icons.FOLDER,
            icon_color=ft.Colors.AMBER_600,
            content=ft.Column(
                spacing=20,
                controls=[
                    # Mods Folder Section
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row([
                                ft.Icon(ft.Icons.DNS, size=16, color=ft.Colors.BLUE_GREY),
                                ft.Text("Mods 資料夾", weight=ft.FontWeight.W_500),
                            ]),
                            ft.Text("包含所有 .jar 模組檔案的 Mods 資料夾", size=12, color=ft.Colors.GREY_500),
                            ft.Row(
                                controls=[
                                    ft.Container(content=self.mods_dir_textfield, expand=True),
                                    self._pick_button(self.mods_dir_textfield),
                                ],
                                spacing=5,
                            ),
                        ],
                    ),

                    # Output Folder Section
                    ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row([ft.Icon(ft.Icons.OUTPUT, size=16, color=ft.Colors.BLUE_GREY), ft.Text("輸出資料夾", weight=ft.FontWeight.W_500)]),
                            ft.Text("提取後輸出的資料夾（可留空）會根據選擇型態自動輸出對應結尾資料夾 ，如果輸出路徑中有，則優先使用", size=12, color=ft.Colors.GREY_500),
                            ft.Row(
                                controls=[
                                    ft.Container(
                                        content=self.output_dir_textfield,
                                        expand=True,
                                    ),
                                    self._pick_button(self.output_dir_textfield),
                                    ft.IconButton(
                                        icon=ft.Icons.CLEAR,
                                        icon_size=20,
                                        tooltip="清除路徑",
                                        on_click=self.clear_output_path
                                    )
                                ],
                                spacing=5,
                            )
                        ]
                    ),

                    # Buttons and Status
                    ft.Container(
                        margin=ft.margin.only(top=10),
                        content=ft.Column(
                            spacing=15,
                            controls=[
                                ft.Row(
                                    controls=[self.lang_button, self.book_button],
                                    spacing=15,
                                ),
                                ft.Row(
                                    controls=[self.preview_lang_button, self.preview_book_button],
                                    spacing=15,
                                ),
                                ft.Column(
                                    spacing=5,
                                    controls=[
                                        self.status_text,
                                        ft.Container(
                                            content=self.progress_bar,
                                            border_radius=4,
                                            clip_behavior=ft.ClipBehavior.HARD_EDGE
                                        )
                                    ]
                                )
                            ]
                        )
                    )
                ]
            )
        )

    def _build_logs_card(self):
        """執行日誌卡片。"""

        return styled_card(
            title="執行日誌",
            icon=ft.Icons.RECEIPT_LONG,
            content=ft.Container(
                content=self.log_view,
                bgcolor="#1e1e1e",  # 深色 terminal
                border_radius=8,
                expand=True,
                padding=10,
            ),
            expand=True,
        )

    # ==================================================
    # UI helpers
    # ==================================================
    def _pick_button(self, target):
        """`_pick_button`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`IconButton`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - 依實作回傳值（請見函式內 return path）。
        """
        return ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN_OUTLINED,
            icon_color=ft.Colors.BLUE_GREY_700,
            tooltip="瀏覽...",
            on_click=lambda e: self.pick_directory(target),
        )

    def pick_directory(self, target):
        """`pick_directory`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`getattr`, `_show_snack_bar`, `get_directory_path`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        label = getattr(target, "label", "資料夾")
        self._show_snack_bar(f"請選擇此欄位的資料夾", color=ft.Colors.BLUE_600)
        self.file_picker.on_result = lambda e: self._on_dir_picked(e, target)
        self.file_picker.get_directory_path()


    def _on_dir_picked(self, e, target):
        """`_on_dir_picked`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if e.path:
            target.value = e.path
            self.page.update()
        else:
            self._show_snack_bar(f"未選擇資料夾", color=ft.Colors.BLUE_600)


    def set_controls_disabled(self, disabled: bool):
        """`set_controls_disabled`
        
        用途：
        - 設定此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`update`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        for ctrl in (
            self.mods_dir_textfield,
            self.output_dir_textfield,
            self.lang_button,
            self.book_button,
        ):
            ctrl.disabled = disabled
            ctrl.opacity = 0.5 if disabled else 1.0
        self.page.update()

    def clear_output_path(self, e=None):
        """`clear_output_path`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`update`, `_append_log_line`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        if not (self.output_dir_textfield.value or "").strip():
            return
        self.output_dir_textfield.value = ""
        self.page.update()
        self._append_log_line("[系統] 已清除輸出路徑")

    # ==================================================
    # TaskSession UI Poller
    # ==================================================
    def _start_ui_poller(self, mode: str = ""):
        """`_start_ui_poller`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`clear`, `start`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        self._ui_poller_stop.clear()
        self._last_rendered_log_count = 0
        
        # 重置統計
        self._extraction_stats = {
            'success': 0,
            'warnings': 0,
            'failures': 0,
            'total_files': 0
        }

        def poll():
            """`poll`
            
            用途：
            - 處理此函式的主要流程（細節以程式碼為準）。
            
            參數：
            - 依函式簽名。
            
            回傳：
            - None
            """
            while not self._ui_poller_stop.is_set():
                snap = self.session.snapshot()
                status = snap["status"]
                progress = snap["progress"]
                logs = snap["logs"]
                is_error = snap["error"]

                # Status Text
                if status == "RUNNING":
                    self.status_text.value = "狀態：處理中..."
                elif status == "DONE":
                    self.status_text.value = "狀態：完成"
                elif status == "ERROR":
                    self.status_text.value = "狀態：發生錯誤"
                else:
                    self.status_text.value = "狀態：閒置"

                # Progress
                self.progress_bar.value = progress
                self.progress_bar.color = ft.Colors.RED if is_error else ft.Colors.BLUE

                # Logs（同時累積統計）
                if len(logs) > self._last_rendered_log_count:
                    for line in logs[self._last_rendered_log_count :]:
                        if line.strip():
                            self._append_log_line(line)
                            self._update_stats_from_log(line)
                    self._last_rendered_log_count = len(logs)
                    self.log_view.scroll_to(offset=-1, duration=100)

                if status in ("DONE", "ERROR"):
                    self.set_controls_disabled(False)
                    # 顯示摘要對話框
                    if status == "DONE" and mode:
                        self._show_extraction_summary(mode)
                    self.page.update()
                    break

                self.page.update()
                time.sleep(0.1)

        threading.Thread(target=poll, daemon=True).start()
    
    def _update_stats_from_log(self, line: str):
        """從 log 訊息中更新統計"""
        # 解析成功提取
        if "成功提取" in line and "個新檔案" in line:
            try:
                import re
                match = re.search(r'成功提取 (\d+) 個新檔案', line)
                if match:
                    count = int(match.group(1))
                    self._extraction_stats['success'] += 1
                    self._extraction_stats['total_files'] += count
            except:
                pass
        
        # 解析跳過
        elif "跳過" in line or "已存在" in line:
            self._extraction_stats['warnings'] += 1
        
        # 解析失敗
        elif "[ERROR]" in line or "失敗" in line or "錯誤" in line:
            self._extraction_stats['failures'] += 1
    
    def _show_extraction_summary(self, mode: str):
        """顯示提取結果摘要"""
        stats = self._extraction_stats
        
        # 建立摘要內容
        summary_content = ft.Column([
            ft.Text("提取結果摘要", size=18, weight=ft.FontWeight.BOLD),
            ft.Divider(),
            
            ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=24),
                ft.Text(f"成功: {stats['success']} 個 JAR", size=14),
            ], spacing=8),
            
            ft.Row([
                ft.Icon(ft.Icons.WARNING, color=ft.Colors.ORANGE, size=24),
                ft.Text(f"跳過: {stats['warnings']} 個 JAR", size=14),
            ], spacing=8),
            
            ft.Row([
                ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED, size=24),
                ft.Text(f"失敗: {stats['failures']} 個 JAR", size=14),
            ], spacing=8),
            
            ft.Divider(),
            ft.Text(f"總共提取: {stats['total_files']} 個檔案", 
                    size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700),
        ], spacing=12)
        
        # 建立對話框
        dialog = ft.AlertDialog(
            title=ft.Text(f"{mode.upper()} 提取完成"),
            content=summary_content,
            actions=[
                ft.ElevatedButton("確定", on_click=lambda e: self._close_dialog(dialog)),
            ],
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
    
    def _append_log_line(self, line: str):
        """`_append_log_line`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`append`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        color = "#e0e0e0" # default logs are light grey
        if "[ERROR]" in line:
            color = "#ff6b6b" # soft red
        elif "[系統]" in line:
            color = "#69db7c" # soft green
        elif "Translation" in line or "完成" in line:
            color = "#74c0fc" # soft blue

        self.log_view.controls.append(
            ft.Text(line, font_family="Consolas,Monospace", size=13, color=color, selectable=True)
        )

    # ==================================================
    # Worker Logic
    # ==================================================
    def start_extraction(self, mode: str):
        """`start_extraction`
        
        用途：
        - 處理此函式的主要流程（細節以程式碼為準）。
        - 主要包裝/呼叫：`snapshot`, `strip`
        
        參數：
        - 依函式簽名。
        
        回傳：
        - None
        """
        snap = self.session.snapshot()
        if snap.get("status") == "RUNNING":
            self._show_snack_bar("任務進行中...")
            return

        mods_dir = (self.mods_dir_textfield.value or "").strip()
        output_dir = (self.output_dir_textfield.value or "").strip()

        if not mods_dir:
            self._show_snack_bar("請先選擇 Mods 資料夾")
            return

        mods_path = Path(mods_dir)
        if not mods_path.exists():
            self._show_snack_bar("Mods 資料夾不存在")
            return

        # Auto-generate output if empty
        if not output_dir:
            suffix = "_提取lang_輸出" if mode == "lang" else "_提取book_輸出"
            output_dir = str(mods_path.with_name(mods_path.name + suffix))
            self.output_dir_textfield.value = output_dir
            self.page.update()
            self._append_log_line(f"[系統] 自動設定輸出路徑：{output_dir}")

        out_path = Path(output_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
        except Exception as ex:
            self._show_snack_bar(f"無法建立輸出資料夾")
            self._append_log_line(f"[ERROR] {ex}")
            return

        # Start
        self.set_controls_disabled(True)
        self.log_view.controls.clear()
        self.session.start()
        self._append_log_line(f"[系統] 開始任務 ({mode})...")
        self._start_ui_poller(mode=mode)  # 傳入 mode 以便完成後顯示摘要

        target = run_lang_extraction_service if mode == "lang" else run_book_extraction_service
        threading.Thread(
            target=target,
            args=(mods_dir, str(out_path), self.session),
            daemon=True,
        ).start()

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_400):
        """
        顯示底部的快訊通知 (SnackBar)
        
        :param message: 要顯示的文字訊息
        :param color: SnackBar 的背景顏色，預設為淺紅色 (RED_400)
        """
        # 建立 SnackBar 元件，包含文字內容與背景顏色
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        
        # 將 SnackBar 加入頁面的 overlay 層。
        # 在現代 Flet 版本中，這是顯示彈出式元件（如 SnackBar, Dialog）的標準做法。
        self.page.overlay.append(snack)
    
        # 將 open 屬性設為 True 以觸發顯示動畫
        snack.open = True
    
        # 更新頁面，讓變更立即反映在 UI 上
        self.page.update()
    
    # ==================================================
    # 預覽功能
    # ==================================================
    def show_preview(self, mode: str):
        """顯示提取預覽對話框（背景執行 + 進度條更新）。"""

        # 預覽故意不走 app.services 的 wrapper，因為這裡需要的是「逐步回報進度」：
        # UI 直接吃 generator update，比包成一次回傳的 service 更容易維持預覽進度條與結果對話框。
        # 換句話說，提取流程偏 service façade；預覽流程偏 UI orchestration。
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        
        if not mods_dir:
            self._show_snack_bar("請先選擇 Mods 資料夾")
            return
        
        mods_path = Path(mods_dir)
        if not mods_path.exists():
            self._show_snack_bar("Mods 資料夾不存在")
            return
        
        # 提示使用者
        self._show_snack_bar(f"正在掃描 {mode.upper()} 檔案...", ft.Colors.BLUE_600)
        self._append_log_line(f"[系統] 開始預覽掃描...")
        
        # 鎖定按鈕
        self.set_controls_disabled(True)
        
        # 預覽流程是「背景掃描 + 前景輪詢」：
        # do_preview() 只負責推進狀態，poll() 專心把狀態轉成 UI，避免背景執行緒直接碰太多 Flet 控制項。
        preview_state = {
            'progress': 0.0,
            'current': 0,
            'total': 0,
            'done': False,
            'result': None,
            'error': None
        }
        
        # 背景執行預覽
        def do_preview():
            """`do_preview`
            
            用途：
            - 處理此函式的主要流程（細節以程式碼為準）。
            - 主要包裝/呼叫：`preview_extraction_generator`
            
            參數：
            - 依函式簽名。
            
            回傳：
            - None
            """
            from translation_tool.core.jar_processor import preview_extraction_generator
            
            try:
                for update in preview_extraction_generator(mods_dir, mode):
                    if 'error' in update:
                        preview_state['error'] = update['error']
                        preview_state['done'] = True
                        break
                    
                    # 更新進度
                    preview_state['progress'] = update.get('progress', 0)
                    preview_state['current'] = update.get('current', 0)
                    preview_state['total'] = update.get('total', 0)
                    
                    # 如果有最終結果
                    if 'result' in update:
                        preview_state['result'] = update['result']
                        preview_state['done'] = True
                
            except Exception as ex:
                preview_state['error'] = str(ex)
                preview_state['done'] = True
        
        # 啟動背景執行緒
        threading.Thread(target=do_preview, daemon=True).start()
        
        # UI 輪詢器（類似提取時的 poller）
        def poll():
            """`poll`
            
            用途：
            - 處理此函式的主要流程（細節以程式碼為準）。
            - 主要包裝/呼叫：`set_controls_disabled`, `_append_log_line`, `update`
            
            參數：
            - 依函式簽名。
            
            回傳：
            - None
            """
            while not preview_state['done']:
                # 更新進度 UI
                self.progress_bar.value = preview_state['progress']
                self.progress_bar.color = ft.Colors.BLUE
                self.status_text.value = f"狀態：預覽掃描中 ({preview_state['current']}/{preview_state['total']})..."
                try:
                    self.page.update()
                except:
                    pass
                time.sleep(0.1)
            
            # 完成後顯示結果
            self.set_controls_disabled(False)
            self.status_text.value = "狀態：預覽完成"
            self.progress_bar.value = 1.0
            
            # 加入除錯訊息
            self._append_log_line(f"[系統] 預覽完成：error={preview_state['error'] is not None}, result={preview_state['result'] is not None}")
            
            try:
                self.page.update()
            except:
                pass
            
            # 使用 page.overlay 來顯示對話框（更可靠）
            if preview_state['error']:
                self._append_log_line(f"[ERROR] 預覽錯誤：{preview_state['error']}")
                self._show_preview_dialog_error_v2(preview_state['error'], mode)
            elif preview_state['result']:
                result = preview_state['result']
                self._append_log_line(f"[系統] 找到 {result.get('total_files', 0)} 個檔案，準備顯示預覽對話框")
                
                # 生成預覽報告（如果有設定輸出路徑）
                output_dir = (self.output_dir_textfield.value or "").strip()
                if output_dir:
                    try:
                        from translation_tool.core.jar_processor import generate_preview_report
                        
                        # 自動建立輸出資料夾（如果不存在）
                        output_path = Path(output_dir)
                        if not output_path.exists():
                            self._append_log_line(f"[系統] 輸出資料夾不存在，自動建立：{output_dir}")
                            output_path.mkdir(parents=True, exist_ok=True)
                            self._append_log_line(f"[系統] ✅ 資料夾建立成功")
                        
                        # 生成報告
                        report_path = generate_preview_report(result, mode, output_dir)
                        self._append_log_line(f"[系統] ✅ 預覽報告已成功輸出")
                        self._append_log_line(f"[系統] 📄 報告路徑：{report_path}")
                        self._show_snack_bar(f"預覽報告已生成", ft.Colors.GREEN_600)
                    except Exception as ex:
                        self._append_log_line(f"[ERROR] ❌ 生成預覽報告失敗：{ex}")
                        import traceback
                        self._append_log_line(f"[ERROR] {traceback.format_exc()}")
                else:
                    self._append_log_line(f"[系統] ⚠️ 未設定輸出路徑，跳過報告生成")
                
                self._show_preview_dialog_result_v2(result, mode)
            else:
                self._append_log_line("[WARN] 預覽無結果")
                self._show_snack_bar("預覽無結果", ft.Colors.ORANGE_400)
        
        # 啟動輪詢器
        threading.Thread(target=poll, daemon=True).start()
    
    def _show_preview_dialog_result_v2(self, result: dict, mode: str):
        """顯示預覽結果對話框（使用 overlay 方式，適合背景執行緒）"""
        preview_results = result.get('preview_results', [])
        total_files = result.get('total_files', 0)
        total_size_mb = result.get('total_size_mb', 0)
        
        # 檢查是否有生成報告
        output_dir = (self.output_dir_textfield.value or "").strip()
        has_report = output_dir and Path(output_dir).exists()
        
        # 建立預覽內容
        preview_content_controls = [
            ft.Text(
                f"預覽結果（{mode.upper()}）",
                size=16,
                weight=ft.FontWeight.BOLD
            ),
            ft.Divider(),
            ft.Text(f"共找到 {total_files} 個檔案", size=14, color=ft.Colors.BLUE_700),
            ft.Text(f"總大小：{total_size_mb:.2f} MB", size=14, color=ft.Colors.BLUE_700),
        ]
        
        # 如果有生成報告，顯示提示和路徑
        if has_report:
            # 找出最新的報告檔案
            try:
                import glob
                pattern = str(Path(output_dir) / f"preview_report_{mode}_*.md")
                report_files = glob.glob(pattern)
                if report_files:
                    latest_report = max(report_files, key=lambda p: Path(p).stat().st_mtime)
                    report_name = Path(latest_report).name
                else:
                    report_name = "(找不到報告檔案)"
            except:
                report_name = f"preview_report_{mode}_*.md"
            
            preview_content_controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Icon(ft.Icons.DESCRIPTION, size=16, color=ft.Colors.GREEN_700),
                            ft.Text("詳細報告已生成到輸出資料夾", size=12, color=ft.Colors.GREEN_700, weight=ft.FontWeight.BOLD),
                        ], spacing=4),
                        ft.Text(f"📄 {report_name}", size=11, color=ft.Colors.GREEN_900, selectable=True),
                    ], spacing=4, tight=True),
                    padding=8,
                    bgcolor=ft.Colors.GREEN_50,
                    border_radius=8,
                )
            )
        
        preview_content_controls.extend([
            ft.Divider(),
            ft.Text("詳細清單（前 20 項）：", size=13, weight=ft.FontWeight.BOLD),
        ])
        
        preview_content = ft.Column(
            preview_content_controls,
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )
        
        # 加入每個 JAR 的詳情
        for r in preview_results[:20]:  # 只顯示前 20 個避免太長
            preview_content.controls.append(
                ft.Text(
                    f"📦 {r['jar']}: {r['count']} 個檔案 ({r['size_mb']:.1f} MB)",
                    size=12
                )
            )
        
        if len(preview_results) > 20:
            preview_content.controls.append(
                ft.Text(f"... 還有 {len(preview_results) - 20} 個 JAR 檔案",
                        size=12, color=ft.Colors.GREY_700)
            )
        
        # 建立對話框
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"提取預覽 - {mode.upper()}"),
            content=ft.Container(
                content=preview_content,
                width=600,
                height=400,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog_overlay(dialog)),
                ft.ElevatedButton(
                    "確認提取",
                    icon=ft.Icons.CHECK,
                    on_click=lambda e: self._start_from_preview_overlay(dialog, mode)
                ),
            ],
        )
        
        # 使用 page.open() 方式顯示（正確的配對）
        try:
            self.page.open(dialog)
        except Exception as ex:
            self._append_log_line(f"[ERROR] 顯示對話框失敗: {ex}")
    
    def _show_preview_dialog_error_v2(self, error: str, mode: str):
        """顯示預覽錯誤對話框（使用 page.open 方式）"""
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("預覽失敗"),
            content=ft.Text(f"無法預覽 {mode.upper()} 提取：{error}"),
            actions=[
                ft.TextButton("關閉", on_click=lambda e: self._close_dialog_overlay(dialog)),
            ],
        )
        
        try:
            self.page.open(dialog)
        except Exception as ex:
            self._append_log_line(f"[ERROR] 顯示錯誤對話框失敗: {ex}")
    
    def _close_dialog_overlay(self, dialog):
        """關閉 overlay 對話框"""
        try:
            # 使用 Flet 官方推薦的關閉方式
            self.page.close(dialog)
        except Exception as ex:
            # 如果 page.close() 失敗，改用手動方式
            dialog.open = False
            if dialog in self.page.overlay:
                self.page.overlay.remove(dialog)
            try:
                self.page.update()
            except:
                pass
    
    def _start_from_preview_overlay(self, dialog, mode: str):
        """從預覽對話框開始提取（overlay 版本）"""
        self._close_dialog_overlay(dialog)
        self.start_extraction(mode)
    
    def _show_preview_dialog_result(self, result: dict, mode: str):
        """顯示預覽結果對話框"""
        preview_results = result.get('preview_results', [])
        total_files = result.get('total_files', 0)
        total_size_mb = result.get('total_size_mb', 0)
        
        # 建立預覽內容
        preview_content = ft.Column(
            [
                ft.Text(
                    f"預覽結果（{mode.upper()}）",
                    size=16,
                    weight=ft.FontWeight.BOLD
                ),
                ft.Divider(),
                ft.Text(f"共找到 {total_files} 個檔案", size=14, color=ft.Colors.BLUE_700),
                ft.Text(f"總大小：{total_size_mb:.2f} MB", size=14, color=ft.Colors.BLUE_700),
                ft.Divider(),
                ft.Text("詳細清單：", size=13, weight=ft.FontWeight.BOLD),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )
        
        # 加入每個 JAR 的詳情
        for r in preview_results[:20]:  # 只顯示前 20 個避免太長
            preview_content.controls.append(
                ft.Text(
                    f"📦 {r['jar']}: {r['count']} 個檔案 ({r['size_mb']:.1f} MB)",
                    size=12
                )
            )
        
        if len(preview_results) > 20:
            preview_content.controls.append(
                ft.Text(f"... 還有 {len(preview_results) - 20} 個 JAR 檔案",
                        size=12, color=ft.Colors.GREY_700)
            )
        
        # 建立對話框
        dialog = ft.AlertDialog(
            title=ft.Text(f"提取預覽 - {mode.upper()}"),
            content=ft.Container(
                content=preview_content,
                width=600,
                height=400,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog)),
                ft.ElevatedButton(
                    "確認提取",
                    icon=ft.Icons.CHECK,
                    on_click=lambda e: self._start_from_preview(dialog, mode)
                ),
            ],
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
    
    def _show_preview_dialog_error(self, error: str, mode: str):
        """顯示預覽錯誤對話框"""
        dialog = ft.AlertDialog(
            title=ft.Text("預覽失敗"),
            content=ft.Text(f"無法預覽 {mode.upper()} 提取：{error}"),
            actions=[
                ft.TextButton("關閉", on_click=lambda e: self._close_dialog(dialog)),
            ],
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
        self.page.update()
    
    def _close_dialog(self, dialog):
        """關閉對話框"""
        dialog.open = False
        self.page.update()
    
    def _start_from_preview(self, dialog, mode: str):
        """從預覽對話框開始提取"""
        self._close_dialog(dialog)
        self.start_extraction(mode)
