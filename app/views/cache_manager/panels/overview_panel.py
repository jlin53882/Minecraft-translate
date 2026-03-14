"""快取總覽面板。

顯示快取統計資訊、重載、儲存等功能。
"""

import flet as ft
from app.ui.components import styled_card


class CacheOverviewPanel(ft.Container):
    """快取總覽面板"""

    def __init__(self, page: ft.Page, cache_manager):
        self.page = page
        self.cache_manager = cache_manager
        super().__init__(expand=True, content=self._build_content())

    def _build_content(self):
        # 統計資訊
        stats = ft.Row(
            [
                ft.Container(
                    content=ft.Column([
                        ft.Text("總筆數", size=12),
                        ft.Text("0", size=24, weight=ft.FontWeight.BOLD),
                    ]),
                    padding=10,
                    bgcolor=ft.Colors.SURFACE_VARIANT,
                    border_radius=8,
                ),
                ft.Container(
                    content=ft.Column([
                        ft.Text("有變更", size=12),
                        ft.Text("0", size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE),
                    ]),
                    padding=10,
                    bgcolor=ft.Colors.SURFACE_VARIANT,
                    border_radius=8,
                ),
            ],
            spacing=10,
        )

        # 操作按鈕
        actions = ft.Row(
            [
                ft.ElevatedButton("重新載入", icon=ft.Icons.REFRESH),
                ft.OutlinedButton("儲存", icon=ft.Icons.SAVE),
            ],
            spacing=10,
        )

        return ft.Column([stats, actions], spacing=20)
