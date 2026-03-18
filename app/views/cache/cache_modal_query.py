# Cache 查詢 Modal
# 提供關鍵字搜尋 + 即時結果預覽

import flet as ft
import threading
from app.views.cache.cache_modal_base import CacheModalBase


class CacheQueryModal(CacheModalBase):
    """Cache 查詢 Modal，含即時搜尋 debounce"""

    def __init__(self, page, on_complete=None, on_error=None, initial_query=None):
        self.initial_query = initial_query
        self._dirty = False
        self._search_timer = None
        super().__init__(
            page=page,
            on_complete=on_complete,
            on_error=on_error,
            width=500,
            padding=20,
        )
        self._build_content()

    def _build_content(self):
        """建立 UI 內容"""
        self.query_input = ft.TextField(
            label="查詢關鍵字",
            value=self.initial_query or "",
            on_change=self._on_input,
        )
        self.result_area = ft.Column([])
        self.content = ft.Column(
            [
                ft.Text("查詢 Cache", size=20, weight=ft.FontWeight.BOLD),
                self.query_input,
                ft.Divider(),
                self.result_area,
            ]
        )

    def _on_input(self, e):
        """輸入變更時觸發搜尋"""
        self._dirty = True
        self._schedule_search()

    def _schedule_search(self):
        """Debounce 搜尋（300ms）"""
        if self._search_timer:
            self._search_timer.cancel()
        self._search_timer = threading.Timer(0.3, self._do_search)
        self._search_timer.start()

    def _do_search(self):
        """執行搜尋邏輯"""
        if not self._dirty:
            return
        try:
            query = self.query_input.value
            results = self._existing_search_logic(query)
            self.result_area.controls = [ft.Text(r) for r in results]
            self._dirty = False
            self.update()
        except Exception as e:
            print(f"[CacheQueryModal] 搜尋失敗: {e}")

    def _existing_search_logic(self, query):
        """搜尋邏輯（待實作）"""
        return []

    def _on_confirm(self):
        """確認回傳搜尋結果"""
        data = {"query": self.query_input.value}
        self._do_complete(data)

    def _on_close(self):
        """關閉時清理 timer"""
        if self._search_timer:
            self._search_timer.cancel()
            self._search_timer = None
