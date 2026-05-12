"""快取總覵面板。

顯示快取統計資訊、重載、儲存等功能。
"""

import flet as ft
from app.ui.components import styled_card


class CacheOverviewPanel(ft.Container):
    """快取總覵面板"""

    def __init__(self, page: ft.Page, cache_manager):
        self._page = page
        self.cache_manager = cache_manager
        super().__init__(expand=True, content=self._build_content())

    def _build_content(self):
        # 統計資訊區塊內容
        stats_content = ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Column([
                        ft.Text("總筆數", size=12),
                        ft.Text("0", size=24, weight=ft.FontWeight.BOLD),
                    ], spacing=2),
                    padding=15,
                    bgcolor=ft.Colors.SURFACE,
                    border_radius=8,
                    expand=True,
                ),
                ft.Container(
                    content=ft.Column([
                        ft.Text("有變更", size=12),
                        ft.Text("0", size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE),
                    ], spacing=2),
                    padding=15,
                    bgcolor=ft.Colors.SURFACE,
                    border_radius=8,
                    expand=True,
                ),
            ], spacing=10),
        ], spacing=10)

        # 操作按鈕區塊內容
        actions_content = ft.Column([
            ft.Row([
                ft.ElevatedButton("重新載入", icon=ft.Icons.REFRESH),
                ft.OutlinedButton("儲存", icon=ft.Icons.SAVE),
            ], spacing=10),
        ], spacing=10)

        # 使用 styled_card 包裝，支援收合
        return ft.Column([
            styled_card(
                title="統計資訊",
                icon=ft.Icons.ANALYTICS,
                content=stats_content,
                collapsible=True,
                default_collapsed=False,
                page=self.page,
            ),
            styled_card(
                title="快速操作",
                icon=ft.Icons.SETTINGS,
                content=actions_content,
                collapsible=True,
                default_collapsed=False,
                page=self.page,
            ),
        ], spacing=15)
