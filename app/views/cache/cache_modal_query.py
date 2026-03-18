# Cache 查詢 Modal
# 提供關鍵字搜尋 + 即時結果預覽

import flet as ft
import threading
from app.views.cache.cache_modal_base import CacheModalBase


class CacheQueryModal(CacheModalBase):
    """Cache 查詢 Modal，含即時搜尋 debounce"""

    def __init__(
        self,
        page,
        on_complete=None,
        on_error=None,
        initial_query=None,
        cache_view=None,
    ):
        """初始化查詢 Modal

        參數：
            page: Flet Page
            on_complete: 完成回調
            on_error: 錯誤回調
            initial_query: 初始搜尋字
            cache_view: 主 View 引用（用於訪問其搜尋方法）
        """
        self.initial_query = initial_query
        self.cache_view = cache_view  # 主 View 引用
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
            on_submit=self._on_search_click,
        )
        # 搜尋按鈕
        self.btn_search = ft.ElevatedButton(
            "搜尋",
            icon=ft.Icons.SEARCH,
            on_click=self._on_search_click,
        )
        # 提示文字
        self.hint_text = ft.Text(
            "請輸入關鍵字後點擊搜尋",
            size=12,
            color=ft.Colors.GREY_700,
        )
        self.result_area = ft.Column([])
        self.content = ft.Column(
            [
                ft.Text("查詢 Cache", size=20, weight=ft.FontWeight.BOLD),
                ft.Row([self.query_input, self.btn_search]),
                self.hint_text,
                ft.Divider(),
                self.result_area,
            ]
        )

    def _on_input(self, e):
        """輸入變更時顯示提示"""
        # 顯示提示
        self.hint_text.value = "請點擊上方搜尋按鈕"
        self.hint_text.color = ft.Colors.ORANGE_700
        self.update()

    def _on_search_click(self, e):
        """搜尋按鈕點擊"""
        query = self.query_input.value
        if not query:
            self.hint_text.value = "請輸入關鍵字"
            self.hint_text.color = ft.Colors.RED_700
            self.update()
            return

        self.hint_text.value = "搜尋中..."
        self.hint_text.color = ft.Colors.BLUE_700
        self.update()

        # 執行搜尋
        results = self._existing_search_logic(query)
        self.result_area.controls = [ft.Text(r) for r in results]

        if results:
            self.hint_text.value = f"找到 {len(results)} 筆結果"
            self.hint_text.color = ft.Colors.GREEN_700
        else:
            self.hint_text.value = "無搜尋結果"
            self.hint_text.color = ft.Colors.GREY_700
        self.update()

    def _schedule_search(self):
        """Debounce 搜尋（已停用，改用按鈕）"""
        pass

    def _existing_search_logic(self, query):
        """搜尋邏輯：使用主 View 的搜索服務"""
        if not query:
            return []
        try:
            # 直接調用主 View 的搜索功能
            if self.cache_view:
                # 臨時設定主 View 的輸入框
                self.cache_view.tf_query_input.value = query
                # 調用主 View 的搜索（已優化為局部更新）
                self.cache_view._on_query_search(None)
                # 取得結果
                results = self.cache_view.query_results
                if results:
                    # 只顯示前 20 筆，避免 Modal 卡頓
                    return [f"{r.get('cache_type', '')}: {r.get('key', '')}" for r in results[:20]]
                return ["無搜尋結果"]
            return [f"搜尋: {query}"]
        except Exception as e:
            return [f"搜尋失敗: {e}"]

    def _on_confirm(self):
        """確認回傳搜尋結果"""
        data = {"query": self.query_input.value}
        self._do_complete(data)

    def _on_close(self):
        """關閉時清理 timer"""
        if self._search_timer:
            self._search_timer.cancel()
            self._search_timer = None
