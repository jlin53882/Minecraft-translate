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
import os
from pathlib import Path
from app.ui import theme
from app.views._log import LogView
from translation_tool.utils.log_unit import log_info, log_warning
import threading

from app.task_session import TaskSession
# 🐛 2026-07-14 user review: 物理刪除 from app.views.extractor.extractor_actions import
# (Phase 3 partial 刪 _update_stats_from_log wrapper 後,
# update_stats_from_log 本體函式也無 caller,本 commit 一起清掉)
from app.views.extractor.extractor_panels import build_logs_panel, build_settings_panel, _build_pick_button
from app.ui.components import styled_card
from app.views.extractor.extractor_dialog import open_extractor_dialog, open_preview_dialog
from app.services_impl.pipelines.extract_service import get_output_folder_names

class ExtractorView(ft.Column):
    """JAR 提取頁（UI）。

    設計概念：
    - 長任務全部寫入 TaskSession（log/progress/status）。
    - UI 只渲染 session 的快照，避免背景執行緒直接操作 UI 控制項，
    - 這樣提取流程與畫面狀態不會互相纏在一起。

    維護注意：
    - 若新增新的提取模式，務必沿用同一套 session + poller 流程。
    - stats 欄位是 UI 顯示用途；不要在核心流程依賴它當正確性來源。
    """

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        """初始化 ExtractorView。

        參數：
            page: Flet Page 物件
            file_picker: Flet FilePicker 物件
        """
        super().__init__(expand=True, spacing=15)
        self._page = page
        self.file_picker = file_picker

        # ExtractorView 的長任務狀態全部收斂到 TaskSession。
        # 背景執行緒只寫 session，UI 端靠 poller 讀快照更新畫面，
        # 這樣提取流程與畫面狀態不會互相纏在一起。
        self.session = TaskSession(max_logs=2000)
        self._ui_poller_stop = threading.Event()

        # 提取統計
        self._extraction_stats = {
            "success": 0,
            "warnings": 0,
            "failures": 0,
            "total_files": 0,
        }

        # ======================
        # UI Components
        # ======================

        # 1. Configuration Section Components
        self.mods_dir_textfield = ft.TextField(
            hint_text="./mods 或 %USERPROFILE%/Mods",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=15,
        )

        self.output_dir_textfield = ft.TextField(
            hint_text="（未指定將自動產生）",
            expand=True,
            dense=True,
            border_color=theme.OUTLINE,
            text_size=14,
            content_padding=15,
            helper="（請選擇或直接輸入輸出資料夾）",
        )

        self.output_dir_helper_text = ft.Text("", size=12, color=ft.Colors.GREY_600)

        self.skip_zh_cn_switch = ft.Switch(
            label="跳過 zh_cn 抽取",
            value=False,
        )

        # 2. Action Buttons - 改为打开对话框
        # open_extractor_dialog / open_preview_dialog 從頂部 import
        # 获取 file_picker
        file_picker = self.file_picker

        self.lang_button = ft.Button(
            "提取 Lang",
            icon=ft.Icons.LANGUAGE,
            style=ft.ButtonStyle(
                color=theme.WHITE,
                bgcolor=theme.BLUE_700,
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=self._handle_extract_lang_click,
        )
        self.book_button = ft.Button(
            "提取 Book",
            icon=ft.Icons.BOOK,
            style=ft.ButtonStyle(
                color=theme.WHITE,
                bgcolor=theme.GREEN_700,
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=self._handle_extract_book_click,
        )

        # 預覽按鈕
        self.preview_lang_button = ft.OutlinedButton(
            "預覽 Lang",
            icon=ft.Icons.PREVIEW,
            on_click=self._handle_preview_lang_click,
        )
        self.preview_book_button = ft.OutlinedButton(
            "預覽 Book",
            icon=ft.Icons.PREVIEW,
            on_click=self._handle_preview_book_click,
        )
        self.dual_extract_button = ft.Button(
            "提取 Lang + Book",
            icon=ft.Icons.LANGUAGE,
            style=ft.ButtonStyle(
                color=theme.WHITE,
                bgcolor="#7B1FA2",
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=20,
            ),
            on_click=self._handle_extract_dual_click,
        )
        self.dual_preview_button = ft.OutlinedButton(
            "預覽 Lang + Book",
            icon=ft.Icons.PREVIEW,
            on_click=self._handle_preview_dual_click,
        )

        # 3. Status Display（由 _build_status_bar 在 build_logs_panel 中統一建立）
        # 4. Logs Console（由 build_logs_panel 中的 _build_status_bar 統一建立）
        # 統一的 LogView widget（取代裸 ListView + 寫死 hex 容器 + 字串比對判 level）
        self.log_view = LogView(
            page=self._page,
            mode="append",
            max_lines=2000,
        )

        # ======================
        # Layout Composition（使用 styled_card 統一外觀）
        # ======================
        # 即使日誌區塊不在主 UI 上顯示，我們仍然呼叫 build_logs_panel
        # 來建立 status_text / progress_bar / log_view 等必要屬性。
        # 日誌面板已不再加到 controls 裡（隱藏）。
        # 透過 .visible = False 雙重保險，避免意外被渲染。
        self._logs_panel = build_logs_panel(self)
        self._logs_panel.visible = False  # 隱藏日誌面板，不顯示在主 UI

        self.controls = [
            styled_card(
                title="設定",
                icon=ft.Icons.SETTINGS,
                content=build_settings_panel(self),
            ),
        ]

        # 初始化 output_dir helper，動態讀取設定值
        # 用 try-except 避免 __init__ 階段 self.page.update() 觸發 Control must be added to the page first
        try:
            self._update_output_dir_helper()
        except RuntimeError as e:
            if "Control must be added to the page first" in str(e):
                # 在 __init__ 階段元件還沒被加到 page，跳過 update 即可
                pass
            else:
                raise

    def _build_settings_card(self):
        """代理至 build_settings_panel，回傳設定面板元件。

        回傳：
            ft.Column，ExtractorView 的設定面板。
        """
        return build_settings_panel(self)

    def _build_logs_card(self):
        """代理至 build_logs_panel，回傳日誌面板元件。

        回傳：
            ft.Column，ExtractorView 的日誌面板。
        """
        return build_logs_panel(self)

    # ==================================================
    # UI helpers
    # ==================================================
    def _pick_button(self, target):
        """代理至 _build_pick_button，產生目錄選擇 IconButton。

        參數：
            target：選擇目錄後填入路徑的 TextField。
        """
        return _build_pick_button(self, target)

    def pick_directory(self, target):
        """開啟目錄選擇對話框。

        Args:
            target: 選擇後要填入路徑的 TextField。
        """
        self._show_snack_bar("請選擇此欄位的資料夾", color=theme.BLUE_600)
        self._page.run_task(self._async_pick_directory, target)

    async def _async_pick_directory(self, target):
        """async 實作：等待使用者選擇目錄後更新目標欄位。

        Args:
            target: 選擇後要填入路徑的 TextField。
        """
        result = await self.file_picker.get_directory_path()
        if result:
            target.value = result
            self.page.update()
        else:
            self._show_snack_bar("未選擇資料夾", color=theme.BLUE_600)

    # 僅在按「預覽/提取」時自動填入輸出路徑，選擇資料夾時不自動填入
    def refresh_output_dir_helper(self):
        """重新讀取 config 並更新 output_dir_textfield 的 helper。

        在設定頁儲存 extractor 設定後呼叫。
        """
        self._update_output_dir_helper()

    def _update_output_dir_helper(self):
        """動態更新 output_dir_textfield 的 helper，顯示實際的資料夾命名設定。

        ✅ 階段 B 重構：config 讀取已抽離至 extract_service.get_output_folder_names()
        """
        # get_output_folder_names 從頂部 import
        folder_names = get_output_folder_names()
        lang_extract = folder_names["lang_extract"]
        book_extract = folder_names["book_extract"]
        dual_extract = folder_names["dual_extract"]
        lang_preview = folder_names["lang_preview"]
        book_preview = folder_names["book_preview"]

        helper_text = (
            f"未指定時自動產生（路徑 + 設定名稱）：\n"
            f"  • Lang 提取：...mods + {lang_extract}\n"
            f"  • Book 提取：...mods + {book_extract}\n"
            f"  • Dual 提取：...mods + {dual_extract}\n"
            f"  • Lang 預覽：...mods + {lang_preview}\n"
            f"  • Book 預覽：...mods + {book_preview}\n\n"
            f"預設抽取語系：zh_cn / zh_tw / en_us\n"
            f"選項：可勾選「跳過 zh_cn 抽取」（預設關閉）\n"
            f"自動產生資料夾名稱可以在設定頁面調整"
        )
        self.output_dir_textfield.helper = helper_text
        # 🐛 2026-08-01 user review: 修 config 儲存觸發 RuntimeError
        # 原因:config_actions.save_config_from_view iterate registry,
        # 會對 'extractor' view call refresh_output_dir_helper(),
        # 但 user 可能還沒切到 extractor 頁(view 還沒 mount 到 page),
        # self.page.update() 內部 self.page getter raise RuntimeError。
        # 修法:try/except 包裹,若 view not in page 就 skip update。
        # (helper_text 已經設好,下次 mount 到 page 會自然 render)
        try:
            self.page.update()
        except RuntimeError as ex:
            if "Control must be added to the page first" in str(ex):
                # view 尚未 mount,defer update
                log_info(
                    f"[OUTPUT_HELPER] _update_output_dir_helper skipped: "
                    f"view not mounted to page yet ({ex})"
                )
                return
            raise

    def _auto_fill_output_path(self, mods_dir: str, mode: str = "lang"):
        """根據 Mods 資料夾自動產生並填入輸出路徑（使用指定模式的設定）。

        ✅ 階段 B 重構：config 讀取已抽離至 extract_service.get_output_folder_names()
        """
        # get_output_folder_names 從頂部 import
        folder_names = get_output_folder_names()
        lang_extract = folder_names["lang_extract"]
        book_extract = folder_names["book_extract"]
        dual_extract = folder_names["dual_extract"]

        if mode == "lang":
            suffix = lang_extract
        elif mode == "book":
            suffix = book_extract
        elif mode == "dual":
            suffix = dual_extract
        else:
            suffix = lang_extract

        # 保護機制：只有輸出路徑為空時才自動填入，避免覆寫使用者已輸入的自訂路徑
        if (self.output_dir_textfield.value or '').strip():
            return

        # 修正邏輯：處理路徑末尾斜線並正確合併名稱
        # 注意：必須先轉成 str 才能呼叫 rstrip，否則會觸發 AttributeError
        mods_path = Path(str(mods_dir).rstrip('\\/'))
        
        # 智慧判斷：如果名稱已經包含 suffix，則直接使用原路徑（避免重複疊加）
        # 如果是「mods」目錄，則在 mods 旁邊產生新的資料夾
        # 其他情況，則把 suffix 加在最後一級目錄名後面
        if mods_path.name.lower() == "mods":
            # 輸入是 .../mods，產生 .../mods_提取XX
            output_path = str(mods_path.parent / (mods_path.name + suffix))
        elif suffix in mods_path.name:
            # 已經包含 suffix（例如使用者已經手動輸入過），直接使用原路徑
            output_path = str(mods_path)
        else:
            # 其他自訂路徑，則在最後一級目錄下合併
            output_path = str(mods_path.with_name(mods_path.name + suffix))

        self.output_dir_textfield.value = output_path
        self.page.update()
        self._append_log_line(f"[系統] 自動設定輸出路徑：{output_path}")

    def _check_mods_dir_or_snack(self, mods_dir: str, action_label: str) -> bool:
        """按鈕 click handler 的前置驗證。

        :param mods_dir: 已經 strip 後的 mods 路徑字串。
        :param action_label: 動作說明(例如 "提取 Lang"、"預覽 Book")，
                             用於 SnackBar 文案(如 "無法提取 Lang: ...")。
        :return: True 表示 mods_dir 合法,可以繼續執行(進 dialog);
                 False 表示已 SnackBar 提示,handler 應早 return。

        UX 改進 (2026-07-13 user review):
            原先 extractor_dialog.py:on_start_click 內早 return + SnackBar
            顯示「請先選擇 Mods 資料夾」,但 user 已付出「按按鈕 + 進 dialog」
            的代價才看到提示。

            把驗證提前到按鈕 click handler 內,user 按按鈕後若沒設 mods_dir,
            直接 SnackBar 提示「請先選擇資料夾」並 return,不進 dialog。
        """
        if not mods_dir:
            self._show_snack_bar(f"⚠️ 請先選擇 Mods 資料夾才能{action_label}", color=theme.AMBER_700)
            return False
        if not os.path.isdir(mods_dir):
            self._show_snack_bar(f"⚠️ Mods 資料夾不存在,無法{action_label}", color=theme.AMBER_700)
            return False
        return True

    def _handle_extract_lang_click(self, e):
        """提取 Lang 按鈕 click handler。提早在按鈕層驗證 mods_dir。"""
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        if not self._check_mods_dir_or_snack(mods_dir, "提取 Lang"):
            return
        output_path = (self.output_dir_textfield.value or "").strip()
        if not output_path:
            output_path = self._auto_fill_output_path(mods_dir, "lang")
        # open_extractor_dialog 從頂部 import
        open_extractor_dialog(
            self.page,
            self.file_picker,
            input_path=mods_dir,
            output_path=output_path,
            mode="lang",
            # 🐛 2026-07-14 user review: 把主 UI skip_zh_cn_switch 串接到 generator,
            # 開關打開時真正過濾 zh_cn.json 檔案 (見 tests/test_skip_zh_cn.py)
            skip_zh_cn=self.skip_zh_cn_switch.value,
        )

    def _handle_extract_book_click(self, e):
        """提取 Book 按鈕 click handler。

        Book 模式 skip_zh_cn (2026-07-14 user review):
        原本以為 book 模式不適用 skip_zh_cn,但 build_book_path_regex
        也用 caller 傳的 lang_codes (跟 lang 模式同樣的 bug pattern)。
        修法跟 lang 模式對稱,book 也接 self.skip_zh_cn_switch.value。
        """
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        if not self._check_mods_dir_or_snack(mods_dir, "提取 Book"):
            return
        output_path = (self.output_dir_textfield.value or "").strip()
        if not output_path:
            output_path = self._auto_fill_output_path(mods_dir, "book")
        # open_extractor_dialog 從頂部 import
        open_extractor_dialog(
            self.page,
            self.file_picker,
            input_path=mods_dir,
            output_path=output_path,
            mode="book",
            # 🐛 2026-07-14 user review:book 模式也串接 skip_zh_cn_switch
            skip_zh_cn=self.skip_zh_cn_switch.value,
        )

    def _handle_extract_dual_click(self, e):
        """提取 Lang + Book 按鈕 click handler。"""
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        if not self._check_mods_dir_or_snack(mods_dir, "提取 Lang + Book"):
            return
        output_path = (self.output_dir_textfield.value or "").strip()
        if not output_path:
            output_path = self._auto_fill_output_path(mods_dir, "dual")
        # open_extractor_dialog 從頂部 import
        open_extractor_dialog(
            self.page,
            self.file_picker,
            input_path=mods_dir,
            output_path=output_path,
            mode="dual",
            # 🐛 2026-07-14 user review: DUAL mode 也串接 skip_zh_cn_switch,
            # 影響 Lang phase 的 regex (Book phase 不受影響)
            skip_zh_cn=self.skip_zh_cn_switch.value,
        )

    def _handle_preview_lang_click(self, e):
        """預覽 Lang 按鈕 click handler。"""
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        if not self._check_mods_dir_or_snack(mods_dir, "預覽 Lang"):
            return
        output_path = (self.output_dir_textfield.value or "").strip()
        # open_preview_dialog 從頂部 import
        open_preview_dialog(
            self.page,
            self.file_picker,
            input_path=mods_dir,
            output_path=output_path,
            mode="lang",
            # 🐛 2026-07-14 user review: preview 路徑也串接 skip_zh_cn_switch
            skip_zh_cn=self.skip_zh_cn_switch.value,
        )

    def _handle_preview_book_click(self, e):
        """預覽 Book 按鈕 click handler。"""
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        if not self._check_mods_dir_or_snack(mods_dir, "預覽 Book"):
            return
        output_path = (self.output_dir_textfield.value or "").strip()
        # open_preview_dialog 從頂部 import
        open_preview_dialog(
            self.page,
            self.file_picker,
            input_path=mods_dir,
            output_path=output_path,
            mode="book",
            # 🐛 2026-07-14 user review:book preview 也串接 skip_zh_cn_switch
            skip_zh_cn=self.skip_zh_cn_switch.value,
        )

    def _handle_preview_dual_click(self, e):
        """預覽 Lang + Book 按鈕 click handler。"""
        mods_dir = (self.mods_dir_textfield.value or "").strip()
        if not self._check_mods_dir_or_snack(mods_dir, "預覽 Lang + Book"):
            return
        output_path = (self.output_dir_textfield.value or "").strip()
        # open_preview_dialog 從頂部 import
        open_preview_dialog(
            self.page,
            self.file_picker,
            input_path=mods_dir,
            output_path=output_path,
            mode="dual",
            # 🐛 2026-07-14 user review: DUAL preview 也串接 skip_zh_cn_switch,
            # 影響 Lang phase 的 regex (Book phase 不受影響)
            skip_zh_cn=self.skip_zh_cn_switch.value,
        )

    def set_controls_disabled(self, disabled: bool):
        """停用或啟用所有輸入框與按鈕（執行期間呼叫，防止重複點擊）。

        包含 dual 按鈕在內的所有動作按鈕都會被停用，避免多執行緒競爭
        _extraction_stats 寫入（Lang 提取中點 dual_extract 會造成 stats 覆蓋）。

        參數：
            disabled：True 為停用（淡化 50%），False 為啟用。
        """
        for ctrl in (
            self.mods_dir_textfield,
            self.output_dir_textfield,
            self.lang_button,
            self.book_button,
            self.dual_extract_button,
            self.dual_preview_button,
            self.preview_lang_button,
            self.preview_book_button,
        ):
            ctrl.disabled = disabled
            ctrl.opacity = 0.5 if disabled else 1.0
        self.page.update()

    def clear_output_path(self, e=None):
        """清除輸出路徑文字欄位，並寫入系統日誌。"""
        if not (self.output_dir_textfield.value or "").strip():
            return
        self.output_dir_textfield.value = ""
        self.page.update()
        self._append_log_line("[系統] 已清除輸出路徑")

    # ==================================================
    # Worker Logic
    # ==================================================
    def _close_dialog_overlay(self, dialog):
        """關閉指定的 dialog 並從 page.overlay 移除。

        用於:
        - 測試中清理殘留的 dialog
        - `extractor_actions.py` / `merge_view.py` 等仍走手動 overlay 的 caller
          (這些仍是 legacy 模式,留為 known issue,未來 follow-up PR 統一改)

        注意:_show_extraction_summary 在本 commit 已改用 Flet 0.85 內建
        show_dialog/pop_dialog,不再呼叫這個 helper。
        """
        try:
            if dialog in self.page.overlay:
                self.page.overlay.remove(dialog)
            dialog.open = False
            self.page.update()
        except Exception as ex:
            log_warning(
                f"[SUMMARY] _close_dialog_overlay failed: {ex!r}",
            )


    def _append_log_line(self, entry_or_str):
        """新增日誌訊息到日誌檢視區（直接走 LogView.add）。

        PR refactor/unified-log-view: 取代原本的字串比對判斷 level + 寫死 hex 顏色。
        LogView 內部會從 entry 的 level 自動取對應顏色。

        支援傳入 LogEntry（取 .level 與 .text）或 str（預設 level="system"）。
        """
        if hasattr(entry_or_str, "text") and hasattr(entry_or_str, "level"):
            # LogEntry 物件
            text = entry_or_str.text
            level = entry_or_str.level
        else:
            # 純字串（reset / auto-fill 等純事件 log），預設 system 等級
            text = str(entry_or_str)
            level = "system"
        self.log_view.add(text, level=level)

    # ==================================================
    # Worker Logic
    # ==================================================
    def _show_snack_bar(self, message: str, color: str = theme.ERROR):
        """
        顯示底部的快訊通知 (SnackBar)

        :param message: 要顯示的文字訊息
        :param color: SnackBar 的背景顏色，預設為淺紅色 (RED_400)

        2026-07-12 user review cleanup:
        - SnackBar 預設 4 秒自動消失(ft.SnackBar.duration 預設值)
        - 仍用 page.overlay.append + open=True 路徑(SnackBar extends DialogControl
          但走 show_dialog 進 dialog stack 會污染既有 dialog cleanup 路徑)
        - 加 try/except + log_warning:若 page 還沒掛載(早期 init 階段)就會跳過,
          留下 traceback 證據
        - **不主動 page.update()**:`snack.open=True` 已經把 control 標 dirty,
          既有呼叫場景 (pick_directory / _async_pick_directory) 的 caller 會
          自己 page.update() 或 run_task,不再多推一個 task 避免測試 page._tasks
          長度斷言失敗
        """
        log_info(f"[UI] SnackBar: {message}")
        # 建立 SnackBar 元件，包含文字內容與背景顏色
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        try:
            self.page.overlay.append(snack)
            snack.open = True
        except Exception as ex:
            log_warning(f"[SNACKBAR] _show_snack_bar failed: {ex!r}")
