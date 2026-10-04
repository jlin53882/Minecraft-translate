"""app/views/lookup_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/app/views/lookup_view.py (加入「查詢中...」功能的修正版)

import threading

import flet as ft

from app.services_impl.pipelines.lookup_service import (
    run_batch_lookup_service,
    run_manual_lookup_service,
)
from app.ui import kit
from app.ui.design import C

RECENT_LIMIT = 6  # 「最近查詢」最多顯示幾筆（只存在這次開啟的程式內）


class LookupView(ft.Column):
    """學名查詢頁：單筆查詢（含最近查詢）與 JSON 批次查詢。

    維護注意：查詢本身由 ``lookup_service`` 提供；本類別只負責畫面與背景執行緒的結果套用。
    """

    def __init__(self, page: ft.Page):
        """初始化 LookupView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=20)
        self._init_lookup_inputs(page)

        # --- UI 佈局 ---
        single_card = kit.section_card(
            "單筆查詢",
            ft.Column(
                [
                    ft.Row([self.single_input, self.single_button], spacing=10),
                    self.recent_row,
                ],
                spacing=12,
            ),
            icon=ft.Icons.SEARCH,
            tone="dia",
        )
        result_card = kit.section_card(
            "查詢結果",
            ft.Row(
                [self.single_progress_ring, self.single_result_text],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            icon=ft.Icons.AUTO_STORIES_OUTLINED,
            tone="ench",
            actions=[self.copy_button],
        )
        batch_card = kit.section_card(
            "批次查詢",
            ft.Column(
                [
                    self.batch_input,
                    ft.Row([self.batch_button, self.batch_progress_bar], spacing=12),
                    self.batch_result_textfield,
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            icon=ft.Icons.CHECKLIST,
            tone="ench",
        )
        self.controls = [
            kit.page_header(
                "學名查詢",
                "將生物英文名稱對應成學名與繁體中文；先查本機快取，未命中再連線維基百科",
                icon=ft.Icons.SCIENCE_OUTLINED,
                tone="ench",
            ),
            ft.Row(
                [
                    ft.Column([single_card, result_card], spacing=16, expand=5),
                    ft.Column([batch_card], spacing=16, expand=6),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

    def _init_lookup_inputs(self, page) -> None:
        """查詢頁的狀態與輸入控制項。"""
        self._page = page
        self._recent: list[str] = []

        # --- 單筆查詢 UI 元件 ---
        self.single_input = kit.text_field(
            hint="輸入單一學名，例如：Felis catus",
            icon=ft.Icons.SEARCH,
            expand=True,
            on_submit=self.single_lookup_clicked,
            tooltip="例如：Felis catus",
        )
        self.single_button = kit.button(
            "查詢", "primary", icon=ft.Icons.SEARCH, on_click=self.single_lookup_clicked
        )
        self.single_result_text = ft.Text(
            "查詢結果將顯示在這裡。", selectable=True, size=14, color=C.DIM
        )
        self.single_progress_ring = ft.ProgressRing(
            visible=False, width=16, height=16, stroke_width=2, color=C.EM
        )
        self.recent_row = ft.Row(wrap=True, spacing=8, run_spacing=8, visible=False)
        self.copy_button = kit.button(
            "複製結果",
            "ghost",
            icon=ft.Icons.CONTENT_COPY,
            size="sm",
            on_click=self.copy_result_clicked,
        )

        # --- 批次查詢 UI 元件 ---
        self.batch_input = kit.text_field(
            hint='輸入 JSON 格式的學名列表，例如：["Felis catus", "Canis lupus familiaris"]',
            multiline=True,
            min_lines=7,
            max_lines=7,
            mono=True,
            tooltip='例如：["Felis catus", "Canis lupus familiaris"]',
        )
        self.batch_result_textfield = kit.text_field(
            "批次查詢結果 (JSON)",
            multiline=True,
            min_lines=9,
            max_lines=9,
            read_only=True,
            mono=True,
        )
        self.batch_button = kit.button(
            "批次查詢",
            "primary",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self.batch_lookup_clicked,
        )
        self.batch_progress_bar = kit.progress_bar(None, "em")
        self.batch_progress_bar.visible = False

    # --- 最近查詢 / 複製 ---
    def _remember(self, name: str) -> None:
        """記住最近查過的名稱（去重、最新在前），並重畫晶片列。"""
        if name in self._recent:
            self._recent.remove(name)
        self._recent.insert(0, name)
        del self._recent[RECENT_LIMIT:]
        self.recent_row.controls = [self._recent_chip(n) for n in self._recent]
        self.recent_row.visible = True

    def _recent_chip(self, name: str) -> ft.Control:
        chip = kit.chip(name, "neutral")
        chip.ink = True
        chip.on_click = lambda _e, n=name: self._lookup_recent(n)
        return chip

    def _lookup_recent(self, name: str) -> None:
        if not self.single_input.disabled:
            self.single_input.value = name
            self.single_lookup_clicked(None)

    def copy_result_clicked(self, _e=None):
        """把單筆查詢結果複製到剪貼簿。"""
        text = self.single_result_text.value or ""
        if not text:
            return

        async def _copy():
            await ft.Clipboard().set(text)

        self.page.run_task(_copy)

    # --- 單筆查詢邏輯 ---
    def single_lookup_clicked(self, e):
        """單筆查詢點擊事件。"""
        search_term = self.single_input.value
        if not search_term:
            self.single_result_text.value = "錯誤：請輸入要查詢的學名。"
            self.single_result_text.color = C.RED
            self.page.update()
            return

        # 1. 更新 UI 進入「查詢中」狀態
        self.single_button.disabled = True
        self.single_input.disabled = True
        self.single_progress_ring.visible = True
        self.single_result_text.value = "查詢中..."
        self.single_result_text.color = C.DIM
        self._remember(search_term.strip())
        self.page.update()

        # 2. 在背景執行緒中執行查詢
        thread = threading.Thread(
            target=self.single_lookup_worker, args=(search_term,), daemon=True
        )
        thread.start()

    def _run_on_ui(self, fn):
        """把 UI 更新排到 Flet event loop（背景執行緒直接 page.update 不安全）。"""

        async def _apply():
            fn()

        self.page.run_task(_apply)

    def single_lookup_worker(self, name: str):
        """執行單筆查詢工作（背景執行緒）；結果交給 event loop 套用。"""
        try:
            result = run_manual_lookup_service(name)
            color = C.TEXT
        except Exception as ex:  # noqa: BLE001 - 失敗也要恢復按鈕並顯示原因
            result = f"查詢失敗：{ex}"
            color = C.RED

        def apply():
            self.single_result_text.value = result
            self.single_result_text.color = color
            self.single_button.disabled = False
            self.single_input.disabled = False
            self.single_progress_ring.visible = False
            self.page.update()

        self._run_on_ui(apply)

    # --- 批次查詢邏輯 ---
    def batch_lookup_clicked(self, e):
        """處理批次查詢按鈕點擊事件"""
        json_text = self.batch_input.value
        if not json_text:
            self.batch_result_textfield.value = "錯誤：請貼上 JSON 內容"
            self.page.update()
            return

        self.batch_button.disabled = True
        self.batch_progress_bar.visible = True
        self.batch_progress_bar.value = None  # 不確定進度
        self.batch_result_textfield.value = "批次查詢中，請稍候..."
        self.page.update()

        thread = threading.Thread(
            target=self.batch_lookup_worker, args=(json_text,), daemon=True
        )
        thread.start()

    def batch_lookup_worker(self, json_text):
        """執行批次查詢翻譯服務（背景執行緒）；結果交給 event loop 套用。"""
        state = {"text": None, "progress": None}
        try:
            for update in run_batch_lookup_service(json_text):
                if update.get("error"):
                    state["text"] = update.get("log")
                    break
                if update.get("result"):
                    state["text"] = update.get("result")
                if update.get("progress"):
                    state["progress"] = update.get("progress")
        except Exception as ex:  # noqa: BLE001 - 失敗也要恢復按鈕並顯示原因
            state["text"] = f"批次查詢失敗：{ex}"
        finally:

            def apply():
                if state["text"] is not None:
                    self.batch_result_textfield.value = state["text"]
                if state["progress"] is not None:
                    self.batch_progress_bar.value = state["progress"]
                self.batch_button.disabled = False
                self.batch_progress_bar.visible = False
                self.page.update()

            self._run_on_ui(apply)

    @property
    def page(self):
        """回傳 Flet Page 實例 (2026-08-01 PR #85 重構補 @property)。

        之前 def page(self) 沒 @property decorator,變成 bound method reference,
        PR #85 改用 show_snack(self.page, ...) 直接呼叫時,
        self.page 是 method object 而非 Page 實例,SnackBar 永遠跳不出來。
        加 @property 後 self.page 才是 Page 實例。
        """
        return self._page
