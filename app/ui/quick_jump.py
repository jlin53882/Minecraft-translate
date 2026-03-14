"""快速跳轉面板模組。

提供快速跳轉功能，讓使用者可以快速搜尋並跳轉到目標頁面。
"""

import flet as ft


class QuickJumpPanel(ft.Container):
    """快速跳轉搜尋面板"""

    def __init__(
        self,
        page: ft.Page,
        view_registry: list,
        on_jump_callback,
        on_close_callback,
    ):
        """初始化快速跳轉面板

        參數：
            page: Flet Page 物件
            view_registry: View 註冊表
            on_jump_callback: 跳轉回調函數，接收 view_index
            on_close_callback: 關閉面板回調函數
        """
        self.page = page
        self.view_registry = view_registry
        self.on_jump = on_jump_callback
        self.on_close = on_close_callback
        self.search_field = None
        self.results_list = None
        self.all_items = []

        super().__init__(
            expand=True,
            bgcolor=ft.Colors.SURFACE_VARIANT,
            padding=10,
            content=self._build_content(),
        )

    def _build_content(self):
        """建立面板內容"""
        # 搜尋框
        self.search_field = ft.TextField(
            hint_text="輸入頁面名稱或關鍵字...",
            autofocus=True,
            on_change=self._on_search_change,
            on_submit=self._on_submit,
            prefix_icon=ft.Icons.SEARCH,
        )

        # 結果列表
        self.results_list = ft.ListView(
            expand=True,
            spacing=5,
        )

        # 建立所有頁面列表
        self._build_all_items()

        # 關閉按鈕
        close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            on_click=lambda _: self.on_close(),
            tooltip="關閉",
        )

        # 標題列
        header = ft.Row(
            [
                ft.Text("快速跳轉", weight=ft.FontWeight.BOLD, size=16),
                close_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # 快捷鍵提示
        hint = ft.Text(
            "按 Enter 跳轉 / Esc 關閉",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )

        return ft.Column(
            [
                header,
                self.search_field,
                hint,
                ft.Divider(),
                self.results_list,
            ],
            spacing=10,
            expand=True,
        )

    def _build_all_items(self):
        """建立所有頁面列表"""
        self.all_items = []
        self.results_list.controls.clear()

        for idx, item in enumerate(self.view_registry):
            label = item.get('label', '')
            key = item.get('key', '')

            # 計算快捷鍵（如果有）
            shortcut = ""
            if idx < 9:
                shortcut = f"Ctrl+{idx + 1}"

            # 建立列表項目
            list_item = ft.ListTile(
                leading=ft.Icon(item.get('icon', ft.Icons.PAGEVIEW)),
                title=ft.Text(label),
                subtitle=ft.Text(f"key: {key}", size=10),
                trailing=ft.Text(shortcut, color=ft.Colors.PRIMARY) if shortcut else None,
                on_click=lambda _, i=idx: self._jump_to(i),
                data=idx,
            )

            self.all_items.append(list_item)
            self.results_list.controls.append(list_item)

        self.page.update()

    def _on_search_change(self, e):
        """搜尋文字變更處理"""
        query = e.control.value.lower().strip()

        if not query:
            # 恢復顯示全部
            self.results_list.controls.clear()
            for item in self.all_items:
                self.results_list.controls.append(item)
        else:
            # 過濾結果
            self.results_list.controls.clear()
            for idx, item in enumerate(self.all_items):
                title = item.title.value.lower() if item.title else ""
                subtitle = item.subtitle.value.lower() if item.subtitle else ""

                if query in title or query in subtitle:
                    self.results_list.controls.append(item)

        self.page.update()

    def _on_submit(self, e):
        """按下 Enter 鍵"""
        # 跳轉到第一個結果
        if self.results_list.controls:
            first_item = self.results_list.controls[0]
            if hasattr(first_item, 'data'):
                self._jump_to(first_item.data)

    def _jump_to(self, index: int):
        """跳轉到指定索引的頁面"""
        self.on_jump(index)
        self.on_close()

    def focus(self):
        """聚焦搜尋框"""
        if self.search_field:
            self.search_field.focus()


def show_quick_jump_panel(page: ft.Page, view_registry: list, change_view_callback):
    """顯示快速跳轉面板

    參數：
        page: Flet Page 物件
        view_registry: View 註冊表
        change_view_callback: 切換頁面的回調函數
    """
    # 建立面板
    panel = QuickJumpPanel(
        page=page,
        view_registry=view_registry,
        on_jump_callback=change_view_callback,
        on_close_callback=lambda: close_quick_jump_panel(page),
    )

    # 建立背景遮罩
    overlay = ft.Container(
        expand=True,
        bgcolor=ft.Colors.BLACK38,
        on_click=lambda _: close_quick_jump_panel(page),
        content=panel,
        alignment=ft.alignment.center,
        width=400,
    )

    # 添加到頁面
    page.overlay.append(overlay)
    page.update()

    # 聚焦搜尋框
    panel.focus()


def close_quick_jump_panel(page: ft.Page):
    """關閉快速跳轉面板"""
    if page.overlay:
        # 找到最後添加的 QuickJumpPanel 並移除
        for i in range(len(page.overlay) - 1, -1, -1):
            if isinstance(page.overlay[i], ft.Container):
                # 檢查是否是快速跳轉面板（透過 content 類型判斷）
                overlay_item = page.overlay[i]
                if hasattr(overlay_item, 'content') and isinstance(overlay_item.content, QuickJumpPanel):
                    page.overlay.pop(i)
                    break

    page.update()
