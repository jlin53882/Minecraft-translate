# CacheQueryView - 查詢功能獨立元件
# 可獨立創建 widgets、處理事件、回調結果

import flet as ft
from app.ui import theme


class CacheQueryView(ft.Container):
    """查詢功能視圖（完全獨立）"""

    def __init__(self, page: ft.Page, cache_view):
        """
        初始化查詢視圖

        參數：
            page: Flet Page
            cache_view: 主 CacheView 實例（用於回調）
        """
        self.page = page
        self.cache_view = cache_view

        # 狀態
        self._last_query_value = ""

        # 建立 UI
        super().__init__(
            expand=True,
            bgcolor=theme.WHITE,
            padding=8,
        )
        self.content = self._build_content()

    def _build_content(self):
        """建立查詢 UI"""
        # 搜尋輸入
        self.tf_query_input = ft.TextField(
            label="輸入 key / dst / 關鍵字",
            width=360,
            tooltip="輸入要搜尋的 key、dst 或關鍵字",
            on_submit=lambda e: self._on_search(),
        )

        # 查詢變更提示
        self.query_change_hint = ft.Text("", size=11, color=theme.WARNING, visible=False)

        # 模式選擇
        self.dd_query_mode = ft.Dropdown(
            width=130,
            value="ALL",
            tooltip="搜尋模式",
            options=[
                ft.dropdown.Option("KEY", "Key"),
                ft.dropdown.Option("DST", "DST"),
                ft.dropdown.Option("ALL", "全部"),
            ],
        )
        self.dd_query_mode.on_change = lambda e: self._on_mode_change()

        # 分類選擇
        self.dd_query_type = ft.Dropdown(
            width=180,
            value="ALL",
            tooltip="選擇要查詢的分類",
            options=[ft.dropdown.Option("ALL", "全部")],
        )
        self.dd_query_type.on_change = lambda e: self._on_type_change()

        # 按鈕
        self.btn_query_search = ft.ElevatedButton(
            "搜尋", icon=ft.Icons.SEARCH, on_click=lambda e: self._on_search()
        )
        self.btn_query_clear = ft.OutlinedButton(
            "清空", icon=ft.Icons.CLEAR, on_click=lambda e: self._on_clear()
        )

        # 提示文字
        self.query_search_hint = ft.Text(
            "請輸入關鍵字開始搜尋", size=11, color=theme.GREY_700
        )

        # 結果列表
        self.query_result_list = ft.ListView(
            expand=True,
            spacing=6,
            auto_scroll=False,
        )

        return ft.Column(
            [
                ft.Row([self.tf_query_input, self.query_change_hint], spacing=10),
                ft.Row([self.dd_query_mode, self.dd_query_type], spacing=10),
                ft.Row([self.btn_query_search, self.btn_query_clear], spacing=10),
                self.query_search_hint,
                ft.Container(
                    expand=True,
                    content=self.query_result_list,
                    border=ft.Border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    padding=4,
                ),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

    def set_type_options(self, options):
        """設定分類下拉選項（由外部呼叫）"""
        self.dd_query_type.options = options
        self.dd_query_type.value = "ALL"
        if hasattr(self, 'page') and self.page:
            self.update()

    # ==================== 事件處理 ====================

    def _on_input_change(self, e):
        """輸入框內容變更"""
        new_value = self.tf_query_input.value or ""
        if new_value != self._last_query_value:
            self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
            self.query_change_hint.visible = True
            self.cache_view.update()

    def _on_mode_change(self, e=None):
        """搜尋模式變更"""
        self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
        self.query_change_hint.visible = True
        self.cache_view.update()

    def _on_type_change(self, e=None):
        """分類變更"""
        self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
        self.query_change_hint.visible = True
        self.cache_view.update()

    def _on_search(self, e=None):
        """觸發搜尋（回調到 cache_view）"""
        if self.cache_view.ui_busy:
            self.cache_view._notify("目前忙碌中，暫停搜尋", "warn")
            return

        query = (self.tf_query_input.value or "").strip()
        if not query:
            self.cache_view._notify("請輸入查詢內容", "warn")
            return

        # 設置髒標記
        self._last_query_value = self.tf_query_input.value or ""
        self.query_change_hint.visible = False
        self.cache_view.update()

        # 回調到 cache_view
        if hasattr(self.cache_view, '_on_query_view_search'):
            self.cache_view._on_query_view_search(
                query=query,
                mode=self.dd_query_mode.value,
                dtype=self.dd_query_type.value,
            )

    def _on_clear(self, e=None):
        """清除搜尋"""
        self.tf_query_input.value = ""
        self.query_change_hint.visible = False
        self.query_result_list.controls.clear()
        self.query_search_hint.value = "請輸入關鍵字開始搜尋"
        self.query_search_hint.color = theme.GREY_700
        self.cache_view.update()

        if hasattr(self.cache_view, '_on_query_view_clear'):
            self.cache_view._on_query_view_clear()
