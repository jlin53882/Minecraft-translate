"""app/views/lookup_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

# /minecraft_translator_flet/app/views/lookup_view.py (加入「查詢中...」功能的修正版)

# 待修待測試

import flet as ft
import threading
from app.services_impl.pipelines.lookup_service import (
    run_batch_lookup_service,
    run_manual_lookup_service,
)


class LookupView(ft.Column):
    """LookupView 類別。

    用途：封裝與 LookupView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, page: ft.Page):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`__init__`, `TextField`, `ElevatedButton`

        回傳：None
        """
        super().__init__(scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=15)
        self.page = page

        # --- 單筆查詢 UI 元件 ---
        self.single_input = ft.TextField(
            label="輸入單一學名", expand=True, tooltip="例如：Felis catus"
        )
        self.single_button = ft.ElevatedButton(
            "查詢", icon=ft.Icons.SEARCH, on_click=self.single_lookup_clicked
        )
        self.single_result_text = ft.Text("查詢結果將顯示在這裡。", selectable=True)
        self.single_progress_ring = ft.ProgressRing(
            visible=False, width=16, height=16, stroke_width=2
        )

        # --- 批次查詢 UI 元件 ---
        self.batch_input = ft.TextField(
            label="輸入 JSON 格式的學名列表",
            multiline=True,
            min_lines=5,
            expand=True,
            tooltip='例如：["Felis catus", "Canis lupus familiaris"]',
        )
        self.batch_result_textfield = ft.TextField(
            label="批次查詢結果 (JSON)",
            multiline=True,
            min_lines=5,
            read_only=True,
            expand=True,
        )
        self.batch_button = ft.ElevatedButton(
            "批次查詢", icon=ft.Icons.SEARCH, on_click=self.batch_lookup_clicked
        )
        self.batch_progress_bar = ft.ProgressBar(visible=False)

        # --- UI 佈局 ---
        self.controls = [
            ft.Card(
                content=ft.Container(
                    padding=15,
                    content=ft.Column(
                        [
                            ft.Text(
                                "單筆學名查詢", theme_style=ft.TextThemeStyle.TITLE_MEDIUM
                            ),
                            ft.Row([self.single_input, self.single_button]),
                            ft.Divider(),
                            ft.Row(
                                controls=[
                                    self.single_progress_ring,
                                    self.single_result_text,
                                ],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ]
                    ),
                )
            ),
            ft.Card(
                content=ft.Container(
                    padding=15,
                    content=ft.Column(
                        [
                            ft.Text(
                                "批次學名查詢", theme_style=ft.TextThemeStyle.TITLE_MEDIUM
                            ),
                            ft.Row(
                                [self.batch_input, self.batch_result_textfield],
                                vertical_alignment=ft.CrossAxisAlignment.START,
                                expand=True,
                            ),
                            self.batch_button,
                            self.batch_progress_bar,
                        ],
                        spacing=10,
                    ),
                )
            ),
        ]

    # --- 單筆查詢邏輯 ---
    def single_lookup_clicked(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`Thread`, `start`

        回傳：None
        """
        search_term = self.single_input.value
        if not search_term:
            self.single_result_text.value = "錯誤：請輸入要查詢的學名。"
            self.single_result_text.color = ft.Colors.RED
            self.page.update()
            return

        # 1. 更新 UI 進入「查詢中」狀態
        self.single_button.disabled = True
        self.single_input.disabled = True
        self.single_progress_ring.visible = True
        self.single_result_text.value = "查詢中..."
        self.single_result_text.color = ft.Colors.GREY_500
        self.page.update()

        # 2. 在背景執行緒中執行查詢
        thread = threading.Thread(target=self.single_lookup_worker, args=(search_term,))
        thread.start()

    def single_lookup_worker(self, name: str):
        # 3. 呼叫後端服務
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`run_manual_lookup_service`

        回傳：None
        """
        result = run_manual_lookup_service(name)

        # 4. 在 UI 執行緒中更新最終結果
        self.single_result_text.value = result
        self.single_result_text.color = None  # 恢復預設顏色

        # 5. 在 finally 區塊中恢復 UI 狀態，確保無論成功或失敗都會執行
        self.single_button.disabled = False
        self.single_input.disabled = False
        self.single_progress_ring.visible = False
        self.page.update()

    # --- 批次查詢邏輯 ---
    def batch_lookup_clicked(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`Thread`, `start`

        回傳：None
        """
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

        thread = threading.Thread(target=self.batch_lookup_worker, args=(json_text,))
        thread.start()

    def batch_lookup_worker(self, json_text):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`run_batch_lookup_service`

        回傳：None
        """
        try:
            for update in run_batch_lookup_service(json_text):
                if update.get("error"):
                    self.batch_result_textfield.value = update.get("log")
                    break
                if update.get("result"):
                    self.batch_result_textfield.value = update.get("result")
                if update.get("progress"):
                    self.batch_progress_bar.value = update.get("progress")
                self.page.update()
        finally:
            self.batch_button.disabled = False
            self.batch_progress_bar.visible = False
            self.page.update()

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_600):
        """
        (新) 統一的 SnackBar 觸發函式 (使用您提供的 Overlay 方案)
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()
