# Cache 分片管理 Modal
# 提供 Key/SRC/DST 三標籤頁面編輯

import flet as ft
from app.views.cache.cache_modal_base import CacheModalBase


class CacheShardModal(CacheModalBase):
    """分片管理 Modal"""

    def __init__(
        self,
        page,
        on_complete=None,
        on_error=None,
        initial_data=None,
        cache_view=None,
    ):
        """初始化分片 Modal

        參數：
            page: Flet Page
            on_complete: 完成回調
            on_error: 錯誤回調
            initial_data: 初始資料
            cache_view: 主 View 引用（用於訪問其分片方法）
        """
        self.initial_data = initial_data
        self.cache_view = cache_view  # 主 View 引用
        self._current_tab = "key"
        # TODO: 自動儲存功能（未實作）
        super().__init__(
            page=page,
            on_complete=on_complete,
            on_error=on_error,
            width=600,
            height=500,
        )
        self._build_content()

    def _build_content(self):
        """建立分頁 UI"""
        self.tabs = ft.Tabs(
            selected_index=0,
            on_change=self._on_tab_change,
            tabs=[
                ft.Tab(text="Key 列表", content=self._build_key_tab()),
                ft.Tab(text="SRC 編輯", content=self._build_src_tab()),
                ft.Tab(text="DST 編輯", content=self._build_dst_tab()),
            ],
        )
        self.content = ft.Column(
            [
                ft.Text("分片管理", size=20, weight=ft.FontWeight.BOLD),
                self.tabs,
            ]
        )

    def _build_key_tab(self):
        """建立 Key 列表標籤"""
        return ft.Container(content=ft.Column([ft.Text("目前分片 Key")]))

    def _build_src_tab(self):
        """建立 SRC 編輯標籤"""
        return ft.Container(content=ft.Column([ft.Text("來源語言編輯")]))

    def _build_dst_tab(self):
        """建立 DST 編輯標籤"""
        return ft.Container(content=ft.Column([ft.Text("目標語言編輯")]))

    def _on_tab_change(self, e):
        """切換標籤"""
        self._current_tab = ["key", "src", "dst"][e.control.selected_index]

    def _on_confirm(self):
        """確認回傳當前標籤"""
        data = {"tab": self._current_tab}
        self._do_complete(data)

    def _on_close(self):
        """關閉時處理（預留自動儲存）"""
        # TODO: 自動儲存功能（未實作）
        pass
