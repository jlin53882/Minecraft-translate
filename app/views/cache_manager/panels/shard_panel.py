"""快取分片面板。

管理快取分片、新增/補滿/輪替等功能。
"""

import flet as ft
from app.ui.components import styled_card


class CacheShardPanel(ft.Container):
    """快取分片面板"""

    def __init__(self, page: ft.Page, cache_manager):
        self.page = page
        self.cache_manager = cache_manager
        super().__init__(expand=True, content=self._build_content())

    def _build_content(self):
        # 分片列表
        self.shard_list = ft.ListView(
            expand=True,
            spacing=5,
        )

        # 操作按鈕
        actions = ft.Row(
            [
                ft.ElevatedButton("新增分片", icon=ft.Icons.ADD),
                ft.ElevatedButton("補滿舊檔", icon=ft.Icons.FILL),
                ft.ElevatedButton("輪替分片", icon=ft.Icons.SWAP_HORIZ),
            ],
            spacing=10,
        )

        return ft.Column([actions, self.shard_list], spacing=10)
