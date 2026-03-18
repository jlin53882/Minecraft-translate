# CacheQueryView - 查詢功能獨立元件
# 提供關鍵字搜尋、歷史記錄、詳情顯示

import flet as ft
from app.views.theme import WHITE, BLUE_300, BLUE_50, BLUE_700, GREY_700, OUTLINE_VARIANT, WARNING
from app.ui.components import styled_card


class CacheQueryView(ft.Container):
    """查詢功能視圖（可獨立測試）"""

    def __init__(self, page: ft.Page, cache_view):
        """
        初始化查詢視圖

        參數：
            page: Flet Page
            cache_view: 主 CacheView 引用（用於訪問狀態和回調）
        """
        self.page = page
        self.cache_view = cache_view  # 主 View 引用

        # 狀態
        self._last_query_value = ""

        # 建立 UI
        super().__init__(
            expand=True,
            bgcolor=WHITE,
            padding=8,
        )
        self.content = self._build_content()

    def _build_content(self):
        """建立查詢 UI"""
        # 搜尋列
        self.tf_query_input = ft.TextField(
            label="輸入 key / dst / 關鍵字",
            width=360,
            tooltip="輸入要搜尋的 key、dst 或關鍵字",
            on_submit=lambda e: self._on_query_search(),
            on_change=lambda e: self._on_input_change(),
        )

        # PR5-7: 查詢變更提示
        self.query_change_hint = ft.Text("", size=11, color=WARNING, selectable=True)

        self.query_input_row = ft.Row(
            [self.tf_query_input, self.query_change_hint],
            spacing=10,
        )

        self.dd_query_mode = ft.Dropdown(
            width=130,
            value="ALL",
            tooltip="搜尋模式：Key（鍵名）、DST（翻譯文字）、全部",
            options=[
                ft.dropdown.Option("KEY", "Key"),
                ft.dropdown.Option("DST", "DST"),
                ft.dropdown.Option("ALL", "全部"),
            ],
            on_change=lambda e: self._on_mode_change(),
        )

        self.dd_query_type = ft.Dropdown(
            width=180,
            value="ALL",
            tooltip="選擇要查詢的分類（例如 lang / patchouli）",
            options=[ft.dropdown.Option("ALL", "全部")],
            on_change=lambda e: self._on_type_change(),
        )

        self.btn_query_search = ft.ElevatedButton(
            "搜尋", icon=ft.Icons.SEARCH, on_click=lambda e: self._on_query_search()
        )

        self.btn_query_clear = ft.OutlinedButton(
            "清空", icon=ft.Icons.CLEAR, on_click=lambda e: self._on_query_clear()
        )

        self.query_search_hint = ft.Text(
            "請輸入關鍵字開始搜尋", size=11, color=GREY_700
        )

        self.query_result_list = ft.ListView(
            expand=True,
            spacing=6,
            auto_scroll=False,
        )

        # 詳情區
        self.query_detail_key = ft.Text(
            "Key: -",
            weight=ft.FontWeight.BOLD,
            selectable=True,
            text_align=ft.TextAlign.LEFT,
        )
        self.query_detail_type = ft.Text("類型: -", text_align=ft.TextAlign.LEFT)
        self.query_detail_shard = ft.Text("Shard: -", text_align=ft.TextAlign.LEFT)
        self.query_detail_status = ft.Text(
            "Cache 狀態: -", text_align=ft.TextAlign.LEFT
        )
        self.query_detail_src = ft.Text(
            "-", selectable=True, no_wrap=False, text_align=ft.TextAlign.LEFT
        )
        self.query_detail_dst = ft.TextField(
            value="",
            multiline=True,
            min_lines=4,
            max_lines=8,
            text_align=ft.TextAlign.LEFT,
        )

        # 歷史記錄相關
        self.btn_open_history_drawer = ft.OutlinedButton(
            "歷史紀錄",
            icon=ft.Icons.HISTORY,
            on_click=lambda e: self._on_open_history(),
        )

        # 搜尋卡片
        search_section = styled_card(
            title="關鍵字搜尋",
            icon=ft.Icons.SEARCH,
            content=ft.Column(
                [
                    self.query_input_row,
                    ft.Row(
                        [self.dd_query_mode, self.dd_query_type],
                        spacing=10,
                    ),
                    ft.Row(
                        [self.btn_query_search, self.btn_query_clear, self.btn_open_history_drawer],
                        spacing=10,
                    ),
                    self.query_search_hint,
                    ft.Container(
                        expand=True,
                        content=self.query_result_list,
                        border=ft.border.all(1, OUTLINE_VARIANT),
                        border_radius=8,
                        padding=4,
                    ),
                ],
                spacing=8,
            ),
            collapsible=True,
            default_collapsed=False,
            page=self.page,
        )

        # 詳情卡片
        self.query_detail_card = styled_card(
            title="詳情",
            icon=ft.Icons.INFO_OUTLINE,
            content=ft.Column(
                [
                    self.query_detail_key,
                    self.query_detail_type,
                    self.query_detail_shard,
                    self.query_detail_status,
                    ft.Text("SRC:", weight=ft.FontWeight.BOLD, size=12),
                    self.query_detail_src,
                    ft.Text("DST:", weight=ft.FontWeight.BOLD, size=12),
                    self.query_detail_dst,
                ],
                spacing=4,
            ),
            collapsible=True,
            default_collapsed=False,
            page=self.page,
        )

        return ft.Column(
            [search_section, self.query_detail_card],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )

    # ==================== 事件處理 ====================

    def _on_input_change(self):
        """輸入框內容變更"""
        new_value = self.tf_query_input.value or ""
        if new_value != self._last_query_value:
            self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
            self.query_change_hint.color = WARNING
            self.cache_view.update()

    def _on_mode_change(self):
        """搜尋模式變更"""
        self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
        self.query_change_hint.color = WARNING
        self.cache_view.update()

    def _on_type_change(self):
        """分類變更"""
        self.query_change_hint.value = "⚠️ 偵測到變更，請重新搜尋"
        self.query_change_hint.color = WARNING
        self.cache_view.update()

    def _on_query_search(self):
        """執行搜尋（代理到主 View）"""
        if self.cache_view.ui_busy:
            self.cache_view._notify("目前忙碌中，暫停搜尋", "warn")
            return

        query = (self.tf_query_input.value or "").strip()
        if not query:
            self.cache_view._notify("請輸入查詢內容", "warn")
            return

        # 代理到主 View 的搜尋方法
        self.cache_view._on_query_search_proxy(self.tf_query_input.value)

    def _on_query_clear(self):
        """清除搜尋結果"""
        self.tf_query_input.value = ""
        self.query_change_hint.value = ""
        self.query_result_list.controls.clear()
        self.query_detail_key.value = "Key: -"
        self.query_detail_type.value = "類型: -"
        self.query_detail_shard.value = "Shard: -"
        self.query_detail_status.value = "Cache 狀態: -"
        self.query_detail_src.value = "-"
        self.query_detail_dst.value = ""
        self.cache_view.update()

    def _on_open_history(self):
        """開啟歷史記錄"""
        self.cache_view._on_open_history_window_proxy("query")
