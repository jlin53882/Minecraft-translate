"""快取查詢面板。

提供依 key / dst / 關鍵字搜尋功能。
"""

import flet as ft
from app.ui.components import styled_card


class CacheQueryPanel(ft.Container):
    """快取查詢面板"""

    def __init__(self, page: ft.Page, cache_manager):
        self.page = page
        self.cache_manager = cache_manager
        self.search_field = ft.TextField(
            hint_text="輸入關鍵字搜尋...",
            prefix_icon=ft.Icons.SEARCH,
            on_submit=self._on_search,
        )
        super().__init__(expand=True, content=self._build_content())

    def _build_content(self):
        # 搜尋列
        search_bar = ft.Row(
            [self.search_field, ft.IconButton(ft.Icons.SEARCH, on_click=self._on_search)],
            spacing=5,
        )

        # 搜尋結果
        self.results_list = ft.ListView(
            expand=True,
            spacing=5,
        )

        return ft.Column([search_bar, self.results_list], spacing=10)

    def _on_search(self, e):
        query = self.search_field.value
        # TODO: 實作搜尋邏輯
        self.results_list.controls.clear()
        self.results_list.controls.append(ft.Text(f"搜尋: {query}"))
        self.page.update()
