"""app/views/cache_view.py（快取管理頁）

本頁是快取系統的 UI 入口，功能包含：
- 總覽：各 cache_type 的統計、重載、儲存、新分片/補滿舊檔、輪替分片
- 查詢：依 key / dst / 關鍵字搜尋（含全文索引）
- 編輯：針對單筆 dst 做調整並寫回快取
- 歷史：查看並套用歷史版本（查詢區與分片區共用一套浮動視窗）

維護注意：
- 這個檔案偏大是歷史因素；PR 期間已把「總覽頁 UI 組裝」抽到
  `app.views.cache_manager.cache_overview_panel` 以降低閱讀負擔。
- 本頁大量事件回呼會觸發背景操作；若要避免舊任務覆蓋新狀態，
  需配合 CacheController 的 action_id 機制。
- 本輪只補註解/docstring，不改任何 UI 行為。
"""

import json
import re
import time
import traceback
from pathlib import Path

import flet as ft
from app.ui import theme

# UI 共用元件：統一按鈕樣式（先套用在總覽區，避免一次改動過大）
from app.ui.components import primary_button, secondary_button

from app.views.cache_manager.cache_actions import run_cache_action
from app.views.cache_manager.cache_history_store import (
    history_active_default,
    history_append_event,
    history_dirs,
    history_load_active,
    history_load_recent,
    history_now_ts,
    history_save_active,
)
from app.views.cache_manager.cache_overview_panel import build_overview_page
from app.views.cache_manager.cache_state import (
    CacheHistoryState,
    CacheQueryState,
    CacheShardState,
)
from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_get_overview_service,
    cache_reload_service,
    cache_reload_type_service,
    cache_rotate_service,
    cache_save_all_service,
    cache_search_service,
    cache_update_dst_service,
    cache_rebuild_index_service,  # A3 搜尋功能
)
from translation_tool.utils.log_unit import log_error, log_info, log_warning

class CacheView(ft.Column):
    """快取管理器（UI）。

    本類別是 UI 組裝與事件處理的集中點。

    維護重點（避免踩坑）：
    - 任何會跑背景任務的操作（reload/save/rebuild index/search）都應
      更新 ui_busy/busy_reason，並透過統一的 log 訊息回饋給使用者。
    - 查詢結果的顯示/分頁/選取狀態彼此耦合，改動時要注意同步更新
      `query_results/query_selected_result/query_page`。
    - 歷史視窗同時支援 query 與 shard 兩種來源，靠 history_window_source
      切換；擴充時避免分叉出兩套近似 UI。
    """

    def __init__(self, page: ft.Page):
        """初始化 CacheView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=10)
        self.page = page

        # -------------------- Global state --------------------
        self.ui_busy = False
        self.busy_reason = ""
        self._all_logs: list[str] = []
        self._only_error = True  # UI 預設只看 WARN+
        self._last_overview_data: dict = {}

        # -------------------- Overview state --------------------
        self.overview_text = ft.Text("", selectable=True)
        self.overview_status = ft.Text(
            "狀態：就緒", color=theme.GREEN_700, weight=ft.FontWeight.BOLD
        )
        self.overview_trace = ft.Text(
            "trace: init", size=11, color=theme.GREY_700, selectable=True
        )

        # top actions（總覽區先統一成共用按鈕樣式）
        self.btn_reload_all = primary_button(
            "重新載入全部",
            icon=ft.Icons.REFRESH,
            tooltip="重新載入各類型 cache",
            on_click=self._on_reload_all,
        )
        self.btn_refresh_stats = secondary_button(
            "刷新統計",
            icon=ft.Icons.ANALYTICS,
            tooltip="更新總覽統計數據",
            on_click=self._on_refresh_stats,
        )
        self.btn_rebuild_index = secondary_button(
            "重建搜尋索引",
            icon=ft.Icons.SEARCH,
            tooltip="重建全文搜尋索引（提升搜尋速度）",
            on_click=self._on_rebuild_index,
        )

        # list + log controls
        self.type_list = ft.ListView(expand=True, spacing=6, auto_scroll=True)
        self.log_list = ft.ListView(expand=True, spacing=2, auto_scroll=True)
        self.btn_log_clear = ft.TextButton(
            "清空", icon=ft.Icons.DELETE_SWEEP, on_click=lambda e: self._clear_logs()
        )
        self.btn_log_copy = ft.TextButton(
            "複製全部", icon=ft.Icons.CONTENT_COPY, on_click=lambda e: self._copy_logs()
        )
        self.sw_log_only_error = ft.Switch(
            label="只看警告以上", value=True, on_change=self._on_log_filter_changed
        )

        # -------------------- Query: Search / Explorer --------------------
        self._query_state = CacheQueryState()
        self.query_results = self._query_state.query_results
        self.query_selected_result = self._query_state.query_selected_result
        self.query_original_dst = self._query_state.query_original_dst
        self.query_page = self._query_state.query_page
        self.query_page_size = self._query_state.query_page_size
        self.query_total_pages = self._query_state.query_total_pages

        self.tf_query_input = ft.TextField(
            label="輸入 key / dst / 關鍵字",
            width=360,
            tooltip="輸入要搜尋的 key、dst 或關鍵字",
            on_submit=self._on_query_search,
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
        )
        self.dd_query_type = ft.Dropdown(
            width=180,
            value="ALL",
            tooltip="選擇要查詢的分類（例如 lang / patchouli）",
            options=[ft.dropdown.Option("ALL", "全部")],
        )
        self.btn_query_search = ft.ElevatedButton(
            "搜尋", icon=ft.Icons.SEARCH, on_click=self._on_query_search
        )
        self.btn_query_clear = ft.OutlinedButton(
            "清空", icon=ft.Icons.CLEAR, on_click=self._on_query_clear
        )

        self.query_search_hint = ft.Text(
            "請輸入關鍵字開始搜尋", size=11, color=theme.GREY_700
        )
        self.query_result_list = ft.ListView(
            expand=True,
            spacing=6,
            auto_scroll=False,
        )

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

        # 歷史還原（持久化）- 改成可拖曳浮動視窗（查詢區 + 分類分片共用）
        self._history_state = CacheHistoryState()
        self.history_window_source = self._history_state.history_window_source
        self.query_history_records = self._history_state.query_records
        self.query_history_selected_event = self._history_state.query_selected_event
        self.query_history_selected_text = ft.Text(
            "未選取歷史紀錄", size=11, color=theme.GREY_700
        )
        self.query_history_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
        self.query_history_preview = ft.TextField(
            read_only=True,
            multiline=True,
            min_lines=3,
            max_lines=5,
            text_align=ft.TextAlign.LEFT,
            value="",
        )
        self.btn_apply_history_old = ft.ElevatedButton(
            "套用選取舊值",
            icon=ft.Icons.HISTORY,
            on_click=self._on_apply_selected_history,
        )
        self.btn_restore_latest_query = ft.OutlinedButton(
            "還原最新",
            icon=ft.Icons.RESTORE,
            on_click=self._on_restore_latest_query,
            tooltip="載入最新歷史紀錄（不立即寫入快取）",
        )
        self.query_history_key_text = ft.Text(
            "Key: -", size=11, color=theme.GREY_700
        )
        self.btn_open_history_drawer = ft.OutlinedButton(
            "歷史紀錄",
            icon=ft.Icons.HISTORY,
            on_click=lambda e: self._on_open_history_window(e, source="query"),
        )

        # 可拖曳浮動歷史紀錄視窗（查詢區）
        self.query_history_window = ft.Container(
            visible=False,
            left=100,
            top=100,
            width=420,
            height=480,
            bgcolor=theme.WHITE,
            border=ft.border.all(2, theme.BLUE_300),
            border_radius=10,
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.3, theme.BLACK),
                offset=ft.Offset(2, 2),
            ),
            content=ft.Stack(
                controls=[
                    ft.Column(
                        spacing=0,
                        controls=[
                            # 標題列（可拖曳）
                            ft.Container(
                                bgcolor=theme.BLUE_50,
                                padding=10,
                                border_radius=ft.border_radius.only(
                                    top_left=10, top_right=10
                                ),
                                content=ft.Row(
                                    [
                                        ft.GestureDetector(
                                            expand=True,
                                            mouse_cursor=ft.MouseCursor.MOVE,
                                            on_pan_update=self._on_query_history_window_drag,
                                            content=ft.Row(
                                                [
                                                    ft.Icon(
                                                        ft.Icons.HISTORY,
                                                        size=20,
                                                        color=theme.BLUE_700,
                                                    ),
                                                    ft.Text(
                                                        "版本歷史紀錄",
                                                        weight=ft.FontWeight.BOLD,
                                                        size=14,
                                                    ),
                                                ],
                                                spacing=8,
                                            ),
                                        ),
                                        ft.IconButton(
                                            icon=ft.Icons.CLOSE,
                                            icon_size=20,
                                            tooltip="關閉",
                                            on_click=self._on_close_history_window,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ),
                            # 內容區域
                            ft.Container(
                                expand=True,
                                padding=12,
                                content=ft.Column(
                                    [
                                        self.query_history_key_text,
                                        self.query_history_selected_text,
                                        ft.Container(
                                            height=200,
                                            padding=6,
                                            border=ft.border.all(
                                                1, theme.OUTLINE_VARIANT
                                            ),
                                            border_radius=8,
                                            bgcolor=theme.WHITE,
                                            content=self.query_history_list,
                                        ),
                                        ft.Text(
                                            "預覽", weight=ft.FontWeight.BOLD, size=12
                                        ),
                                        self.query_history_preview,
                                        ft.Row(
                                            [
                                                ft.TextButton(
                                                    "關閉",
                                                    on_click=self._on_close_history_window,
                                                ),
                                                self.btn_apply_history_old,
                                            ],
                                            alignment=ft.MainAxisAlignment.END,
                                            spacing=8,
                                        ),
                                    ],
                                    spacing=8,
                                    scroll=ft.ScrollMode.AUTO,
                                ),
                            ),
                        ],
                    ),
                    # 右下角調整大小標記
                    ft.Container(
                        right=0,
                        bottom=0,
                        width=20,
                        height=20,
                        content=ft.GestureDetector(
                            mouse_cursor=ft.MouseCursor.RESIZE_DOWN_RIGHT,
                            on_pan_update=self._on_query_history_window_resize,
                            content=ft.Icon(
                                ft.Icons.DRAG_HANDLE, size=16, color=theme.GREY_400
                            ),
                        ),
                    ),
                ],
            ),
        )

        # C3 歷史紀錄功能（與查詢區相同）- 改成可拖曳浮動視窗
        self.shard_history_records: list[dict] = []
        self.shard_history_selected_event: dict | None = None
        self.shard_history_selected_text = ft.Text(
            "未選取歷史紀錄", size=11, color=theme.GREY_700
        )
        self.shard_history_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
        self.shard_history_preview = ft.TextField(
            read_only=True,
            multiline=True,
            min_lines=3,
            max_lines=5,
            text_align=ft.TextAlign.LEFT,
            value="",
        )
        self.btn_shard_apply_history_old = ft.ElevatedButton(
            "套用選取舊值",
            icon=ft.Icons.HISTORY,
            on_click=self._on_shard_apply_selected_history,
        )
        self.shard_history_key_text = ft.Text(
            "Key: -", size=11, color=theme.GREY_700
        )
        self.btn_open_shard_history_drawer = ft.OutlinedButton(
            "歷史紀錄",
            icon=ft.Icons.HISTORY,
            on_click=lambda e: self._on_open_history_window(e, source="shard"),
        )

        # 可拖曳浮動歷史紀錄視窗（分片區）
        self.shard_history_window = ft.Container(
            visible=False,
            left=150,
            top=150,
            width=420,
            height=480,
            bgcolor=theme.WHITE,
            border=ft.border.all(2, theme.BLUE_300),
            border_radius=10,
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.3, theme.BLACK),
                offset=ft.Offset(2, 2),
            ),
            content=ft.Stack(
                controls=[
                    ft.Column(
                        spacing=0,
                        controls=[
                            ft.Container(
                                bgcolor=theme.BLUE_50,
                                padding=10,
                                border_radius=ft.border_radius.only(
                                    top_left=10, top_right=10
                                ),
                                content=ft.Row(
                                    [
                                        ft.GestureDetector(
                                            expand=True,
                                            mouse_cursor=ft.MouseCursor.MOVE,
                                            on_pan_update=self._on_shard_history_window_drag,
                                            content=ft.Row(
                                                [
                                                    ft.Icon(
                                                        ft.Icons.HISTORY,
                                                        size=20,
                                                        color=theme.BLUE_700,
                                                    ),
                                                    ft.Text(
                                                        "分片歷史紀錄",
                                                        weight=ft.FontWeight.BOLD,
                                                        size=14,
                                                    ),
                                                ],
                                                spacing=8,
                                            ),
                                        ),
                                        ft.IconButton(
                                            icon=ft.Icons.CLOSE,
                                            icon_size=20,
                                            tooltip="關閉",
                                            on_click=self._on_close_shard_history_window,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                padding=12,
                                content=ft.Column(
                                    [
                                        self.shard_history_key_text,
                                        self.shard_history_selected_text,
                                        ft.Container(
                                            height=200,
                                            padding=6,
                                            border=ft.border.all(
                                                1, theme.OUTLINE_VARIANT
                                            ),
                                            border_radius=8,
                                            bgcolor=theme.WHITE,
                                            content=self.shard_history_list,
                                        ),
                                        ft.Text(
                                            "預覽", weight=ft.FontWeight.BOLD, size=12
                                        ),
                                        self.shard_history_preview,
                                        ft.Row(
                                            [
                                                ft.TextButton(
                                                    "關閉",
                                                    on_click=self._on_close_shard_history_window,
                                                ),
                                                self.btn_shard_apply_history_old,
                                            ],
                                            alignment=ft.MainAxisAlignment.END,
                                            spacing=8,
                                        ),
                                    ],
                                    spacing=8,
                                    scroll=ft.ScrollMode.AUTO,
                                ),
                            ),
                        ],
                    ),
                    # 右下角調整大小標記
                    ft.Container(
                        right=0,
                        bottom=0,
                        width=20,
                        height=20,
                        content=ft.GestureDetector(
                            mouse_cursor=ft.MouseCursor.RESIZE_DOWN_RIGHT,
                            on_pan_update=self._on_shard_history_window_resize,
                            content=ft.Icon(
                                ft.Icons.DRAG_HANDLE, size=16, color=theme.GREY_400
                            ),
                        ),
                    ),
                ],
            ),
        )
        self.query_src_tile = ft.ExpansionTile(
            title=ft.Text("SRC（可展開）", weight=ft.FontWeight.BOLD),
            controls=[
                ft.Container(
                    alignment=ft.alignment.top_left,
                    padding=8,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    content=ft.Column(
                        [self.query_detail_src],
                        spacing=0,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                        tight=True,
                    ),
                )
            ],
        )

        self.query_dst_tile = ft.ExpansionTile(
            title=ft.Text("DST（可展開，可編輯）", weight=ft.FontWeight.BOLD),
            controls=[
                ft.Container(
                    alignment=ft.alignment.top_left,
                    padding=8,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    content=ft.Column(
                        [self.query_detail_dst],
                        spacing=0,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                        tight=True,
                    ),
                )
            ],
        )

        self.btn_apply_dst = ft.ElevatedButton(
            "套用", icon=ft.Icons.SAVE, on_click=self._on_apply_dst
        )
        self.btn_revert_dst = ft.OutlinedButton(
            "還原",
            icon=ft.Icons.UNDO,
            on_click=self._on_revert_dst,
            tooltip="還原到原始值",
        )

        self.btn_page_first = ft.OutlinedButton("<<", on_click=self._on_page_first)
        self.btn_page_prev = ft.OutlinedButton("<", on_click=self._on_page_prev)
        self.btn_page_next = ft.OutlinedButton(">", on_click=self._on_page_next)
        self.btn_page_last = ft.OutlinedButton(">>", on_click=self._on_page_last)
        self.tf_page_jump = ft.TextField(
            width=70,
            value="1",
            text_align=ft.TextAlign.CENTER,
            on_submit=self._on_page_jump,
        )
        self.dd_page_size = ft.Dropdown(
            width=110,
            value="50",
            options=[
                ft.dropdown.Option("50", "50"),
                ft.dropdown.Option("100", "100"),
                ft.dropdown.Option("200", "200"),
            ],
            on_change=self._on_page_size_change,
        )
        self.query_page_info = ft.Text("第 1 頁 / 共 1 頁")
        self.query_total_info = ft.Text("共 0 筆")

        self.query_search_card = ft.Container(
            expand=True,
            padding=14,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=10,
            bgcolor=theme.WHITE,
            alignment=ft.alignment.top_left,
            content=ft.Column(
                [
                    ft.Text("查詢區塊（Explorer）", size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "關鍵字輸入（可輸入 key / dst / 關鍵字）",
                        size=11,
                        color=theme.GREY_700,
                    ),
                    ft.Row(
                        [
                            self.tf_query_input,
                            self.btn_query_search,
                            self.btn_query_clear,
                        ],
                        wrap=True,
                    ),
                    ft.Text("查詢模式與分類選擇", size=11, color=theme.GREY_700),
                    ft.Row([self.dd_query_mode, self.dd_query_type], wrap=True),
                    self.query_search_hint,
                    ft.Container(
                        expand=True,
                        content=ft.ResponsiveRow(
                            expand=True,
                            controls=[
                                ft.Container(
                                    col={"xs": 12, "md": 5},
                                    expand=True,
                                    content=ft.Column(
                                        [
                                            ft.Text(
                                                "結果列表（左）",
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            ft.Container(
                                                expand=True,
                                                padding=8,
                                                border=ft.border.all(
                                                    1, theme.OUTLINE_VARIANT
                                                ),
                                                border_radius=8,
                                                bgcolor=theme.WHITE,
                                                content=self.query_result_list,
                                            ),
                                        ],
                                        expand=True,
                                        spacing=6,
                                        horizontal_alignment=ft.CrossAxisAlignment.START,
                                    ),
                                ),
                                ft.Container(
                                    col={"xs": 12, "md": 7},
                                    expand=True,
                                    content=ft.Column(
                                        [
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        "內容檢視（右）",
                                                        weight=ft.FontWeight.BOLD,
                                                    ),
                                                    self.btn_open_history_drawer,
                                                ],
                                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                            ),
                                            ft.Container(
                                                expand=True,
                                                padding=8,
                                                border=ft.border.all(
                                                    1, theme.OUTLINE_VARIANT
                                                ),
                                                border_radius=8,
                                                bgcolor=theme.WHITE,
                                                alignment=ft.alignment.top_left,
                                                content=ft.Column(
                                                    [
                                                        self.query_detail_key,
                                                        self.query_detail_type,
                                                        self.query_detail_shard,
                                                        self.query_detail_status,
                                                        self.query_src_tile,
                                                        self.query_dst_tile,
                                                    ],
                                                    expand=True,
                                                    spacing=6,
                                                    scroll=ft.ScrollMode.ALWAYS,
                                                    horizontal_alignment=ft.CrossAxisAlignment.START,
                                                ),
                                            ),
                                        ],
                                        expand=True,
                                        spacing=6,
                                        horizontal_alignment=ft.CrossAxisAlignment.START,
                                    ),
                                ),
                            ],
                        ),
                    ),
                    ft.Container(
                        padding=ft.padding.only(top=4),
                        content=ft.Row(
                            [
                                self.btn_page_first,
                                self.btn_page_prev,
                                ft.Text("第", size=12),
                                self.tf_page_jump,
                                self.query_page_info,
                                self.btn_page_next,
                                self.btn_page_last,
                                ft.Container(width=10),
                                ft.Text("每頁:", size=12),
                                self.dd_page_size,
                                ft.Text("|", size=12, color=theme.GREY_500),
                                self.query_total_info,
                                ft.Container(width=14),
                                self.btn_apply_dst,
                                self.btn_revert_dst,
                                self.btn_restore_latest_query,
                            ],
                            wrap=True,
                            spacing=6,
                        ),
                    ),
                ],
                expand=True,
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        # 分離：分類/分片獨立分頁
        self.query_type_shard_hint = ft.Text(
            "分類 / 分片清單（獨立分頁）", size=11, color=theme.GREY_700
        )
        self.query_type_shard_col = ft.Column(
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )
        self.query_type_shard_list_container = ft.Container(
            expand=True,
            padding=8,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=theme.WHITE,
            alignment=ft.alignment.top_left,
            content=self.query_type_shard_col,
        )

        # C1：ShardDetail - KeyListCard
        self._shard_state = CacheShardState()
        self.shard_detail_selected_type = self._shard_state.selected_type
        self.shard_detail_selected_file = self._shard_state.selected_file
        self.shard_detail_selected_key = self._shard_state.selected_key
        self.shard_detail_keys = self._shard_state.keys
        self.shard_detail_page = self._shard_state.page
        self.shard_detail_page_size = self._shard_state.page_size
        self.shard_detail_total_pages = self._shard_state.total_pages

        # C2：SRC 預覽模式
        self.shard_detail_src_mode = self._shard_state.src_mode  # preview | raw

        self.shard_detail_meta = ft.Text(
            "尚未選擇分片", size=11, color=theme.GREY_700
        )
        self.tf_shard_key_filter = ft.TextField(
            label="過濾 key",
            hint_text="輸入關鍵字快速過濾",
            dense=True,
            on_change=self._on_shard_key_filter_change,
        )
        self.shard_detail_key_list = ft.ListView(
            expand=True,
            spacing=4,
            auto_scroll=False,
        )
        self.btn_shard_page_first = ft.OutlinedButton(
            "<<", on_click=self._on_shard_page_first
        )
        self.btn_shard_page_prev = ft.OutlinedButton(
            "<", on_click=self._on_shard_page_prev
        )
        self.btn_shard_page_next = ft.OutlinedButton(
            ">", on_click=self._on_shard_page_next
        )
        self.btn_shard_page_last = ft.OutlinedButton(
            ">>", on_click=self._on_shard_page_last
        )
        self.shard_page_info = ft.Text("第 1 頁 / 共 1 頁")
        self.shard_total_info = ft.Text("共 0 keys | 每頁 50")

        self.shard_detail_key_list_container = ft.Container(
            expand=True,
            padding=6,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=theme.WHITE,
            alignment=ft.alignment.top_left,
            content=self.shard_detail_key_list,
        )

        self.shard_src_meta = ft.Text(
            "SRC：請先選擇 key", size=11, color=theme.GREY_700
        )
        self.btn_shard_src_preview = ft.OutlinedButton(
            "👁️ 預覽", on_click=self._on_shard_src_preview_mode
        )
        self.btn_shard_src_raw = ft.OutlinedButton(
            "</> 原始碼", on_click=self._on_shard_src_raw_mode
        )
        self.shard_src_field = ft.TextField(
            value="",
            read_only=True,
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_align=ft.TextAlign.LEFT,
            text_style=ft.TextStyle(font_family="Consolas", size=12, height=1.45),
        )
        self.shard_src_container = ft.Container(
            expand=True,
            padding=6,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=theme.WHITE,
            alignment=ft.alignment.top_left,
            content=self.shard_src_field,
        )

        # C3：DST 編輯
        self.shard_dst_loaded_sig = self._shard_state.dst_loaded_sig
        self.shard_dst_original = self._shard_state.dst_original
        self.shard_dst_meta = ft.Text(
            "DST：請先選擇 key", size=11, color=theme.GREY_700
        )
        self.shard_dst_field = ft.TextField(
            value="",
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_align=ft.TextAlign.LEFT,
            text_style=ft.TextStyle(font_family="Consolas", size=12, height=1.45),
        )
        self.btn_shard_dst_apply = ft.ElevatedButton(
            "套用 DST", icon=ft.Icons.SAVE, on_click=self._on_shard_dst_apply
        )
        self.btn_shard_dst_revert = ft.OutlinedButton(
            "還原", icon=ft.Icons.UNDO, on_click=self._on_shard_dst_revert
        )
        self.btn_shard_dst_copy = ft.OutlinedButton(
            "複製", icon=ft.Icons.CONTENT_COPY, on_click=self._on_shard_dst_copy
        )
        self.btn_shard_dst_restore_latest = ft.OutlinedButton(
            "還原最新",
            icon=ft.Icons.RESTORE,
            on_click=self._on_shard_dst_restore_latest,
            tooltip="載入最新歷史紀錄（不立即寫入快取）",
        )

        self.shard_dst_container = ft.Container(
            expand=True,
            padding=6,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=8,
            bgcolor=theme.WHITE,
            alignment=ft.alignment.top_left,
            content=self.shard_dst_field,
        )

        self.shard_nav_column = ft.Container(
            expand=True,
            padding=10,
            content=ft.Column(
                [
                    ft.Text("分類 / 分片", size=15, weight=ft.FontWeight.BOLD),
                    self.query_type_shard_hint,
                    self.query_type_shard_list_container,
                ],
                spacing=8,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self.shard_nav_view = ft.Container(
            expand=True,
            visible=True,
            content=self.shard_nav_column,
        )

        self.btn_back_to_shard_list = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            tooltip="回分類 / 分片清單",
            on_click=self._on_back_to_shard_list,
        )
        self.shard_workspace_meta = ft.Text(
            "尚未選擇分片", size=11, color=theme.GREY_700
        )

        self.shard_key_column = ft.Container(
            width=self._dynamic_shard_key_panel_width(),
            padding=10,
            border=ft.border.only(
                right=ft.border.BorderSide(1, theme.OUTLINE_VARIANT)
            ),
            content=ft.Column(
                [
                    ft.Text("C1 KeyListCard", weight=ft.FontWeight.BOLD),
                    self.shard_detail_meta,
                    self.tf_shard_key_filter,
                    self.shard_detail_key_list_container,
                    ft.Row(
                        [
                            self.btn_shard_page_first,
                            self.btn_shard_page_prev,
                            self.shard_page_info,
                            self.btn_shard_page_next,
                            self.btn_shard_page_last,
                            ft.Text("|", size=12, color=theme.GREY_500),
                            self.shard_total_info,
                        ],
                        wrap=True,
                        spacing=6,
                    ),
                ],
                spacing=8,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self.shard_editor_column = ft.Container(
            expand=True,
            padding=12,
            content=ft.Column(
                [
                    ft.Text("編輯工作區", size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "右側：上 SRC（唯讀）/ 下 DST（可編輯）",
                        size=11,
                        color=theme.GREY_700,
                    ),
                    ft.Text("C2 SRC 預覽", weight=ft.FontWeight.BOLD),
                    self.shard_src_meta,
                    ft.Row(
                        [self.btn_shard_src_preview, self.btn_shard_src_raw],
                        wrap=True,
                        spacing=6,
                    ),
                    self.shard_src_container,
                    ft.Divider(height=8),
                    ft.Row(
                        [
                            ft.Text("C3 DST 編輯", weight=ft.FontWeight.BOLD),
                            self.btn_open_shard_history_drawer,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self.shard_dst_meta,
                    self.shard_dst_container,
                    ft.Row(
                        [
                            self.btn_shard_dst_apply,
                            self.btn_shard_dst_revert,
                            self.btn_shard_dst_restore_latest,
                            self.btn_shard_dst_copy,
                        ],
                        wrap=True,
                        spacing=6,
                    ),
                ],
                spacing=8,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self.shard_workspace_card = ft.Container(
            expand=True,
            visible=False,
            padding=0,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=10,
            bgcolor=theme.WHITE,
            content=ft.Column(
                [
                    ft.Container(
                        padding=ft.padding.symmetric(horizontal=10, vertical=8),
                        border=ft.border.only(
                            bottom=ft.border.BorderSide(1, theme.OUTLINE_VARIANT)
                        ),
                        content=ft.Row(
                            [
                                ft.Row(
                                    [
                                        self.btn_back_to_shard_list,
                                        ft.Column(
                                            [
                                                ft.Text(
                                                    "C1 / C2 / C3 工作區",
                                                    weight=ft.FontWeight.BOLD,
                                                ),
                                                self.shard_workspace_meta,
                                            ],
                                            spacing=2,
                                            tight=True,
                                        ),
                                    ],
                                    spacing=6,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.START,
                        ),
                    ),
                    ft.Row(
                        expand=True,
                        spacing=0,
                        controls=[
                            self.shard_key_column,
                            self.shard_editor_column,
                        ],
                    ),
                ],
                expand=True,
                spacing=0,
            ),
        )

        self.query_type_shard_card = ft.Container(
            expand=True,
            padding=0,
            border=ft.border.all(1, theme.OUTLINE_VARIANT),
            border_radius=10,
            bgcolor=theme.WHITE,
            content=ft.Column(
                controls=[self.shard_nav_view, self.shard_workspace_card],
                expand=True,
                spacing=0,
            ),
        )

        self.overview_page = self._build_overview_page()
        self.query_entry_page = self._build_query_entry_page()

        self.main_tabs = ft.Tabs(
            selected_index=0,
            expand=True,
            on_change=self._on_tab_change,  # 監聽 tab 切換，自動關閉歷史紀錄視窗
            tabs=[
                ft.Tab(text="總覽 / 管理", content=self.overview_page),
                ft.Tab(text="查詢", content=self.query_entry_page),
            ],
        )

        # 主布局改成 Stack，支援浮動視窗
        self.controls = [
            ft.Stack(
                expand=True,
                controls=[
                    # 主要內容區（原本的 Column）
                    ft.Column(
                        expand=True,
                        controls=[
                            ft.Container(
                                padding=ft.padding.only(bottom=6),
                                content=ft.Row(
                                    [
                                        ft.Text(
                                            "快取管理器 (Cache Manager)",
                                            size=24,
                                            weight=ft.FontWeight.BOLD,
                                        )
                                    ]
                                ),
                            ),
                            self.main_tabs,
                        ],
                    ),
                    # 歷史紀錄浮動視窗（查詢區）
                    self.query_history_window,
                    # 歷史紀錄浮動視窗（分片區）
                    self.shard_history_window,
                ],
            ),
        ]

    # =========================================================
    # Lifecycle
    # =========================================================
    def did_mount(self):
        """

        回傳：None
        """
        try:
            self._load_overview()
            self._refresh_query_type_options()
            self._render_query_type_shard_page()
            self._render_query_results()
            # 防護：確保 page 存在
            if self.page is not None:
                self.page.on_resized = self._on_page_resized
            self._render_query_detail()
            self._refresh_disabled_state()
            if self.page is not None:
                self.page.update()
        except Exception as ex:
            log_error(f"CacheView did_mount failed: {ex}")
            log_error(traceback.format_exc())
            self.overview_status.value = "狀態：初始化失敗"
            self.overview_status.color = theme.RED_700
            self.overview_trace.value = f"trace: did_mount error -> {ex}"
            try:
                self.page.update()
            except Exception:
                pass

    # =========================================================
    # Shared helpers
    # =========================================================
    def _dynamic_shard_list_height(self) -> int:
        """計算 shard list height 高度（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 180
        - 正常：int(page.height * 0.24)，clamp 120..360
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:
            h = 0

        if h <= 0:
            return 180

        # 動態高度：跟視窗高度走，但做上下限保護避免 Flet 撐高異常
        return max(120, min(360, int(h * 0.24)))

    def _dynamic_type_shard_panel_height(self) -> int:
        """計算 type shard panel height（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 260
        - 正常：int(page.height * 0.30)，clamp 180..420
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:
            h = 0

        if h <= 0:
            return 260

        # 上方分類清單固定高度，避免下方 C1/C2 卡撐掉可視區
        return max(180, min(420, int(h * 0.30)))

    def _dynamic_shard_key_list_height(self) -> int:
        """計算 shard key list height 高度（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 220
        - 正常：int(page.height * 0.30)，clamp 140..420
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:
            h = 0

        if h <= 0:
            return 220

        return max(140, min(420, int(h * 0.30)))

    def _dynamic_shard_src_height(self) -> int:
        """計算 shard src height（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 180
        - 正常：int(page.height * 0.24)，clamp 120..320
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:
            h = 0

        if h <= 0:
            return 180

        return max(120, min(320, int(h * 0.24)))

    def _dynamic_shard_dst_height(self) -> int:
        """計算 shard dst height（跟視窗 height 自適應）。

        規則：
        - 若 page.height 取得失敗或 <= 0 → 180
        - 正常：int(page.height * 0.24)，clamp 120..320
        """
        try:
            h = float(getattr(self.page, "height", 0) or 0)
        except Exception:
            h = 0

        if h <= 0:
            return 180

        return max(120, min(320, int(h * 0.24)))

    def _dynamic_shard_key_panel_width(self) -> int:
        """計算 shard key panel width（跟視窗 width 自適應）。

        規則：
        - 若 page.width 取得失敗或 <= 0 → 360
        - 正常：int(page.width * 0.30)，clamp 280..560
        """
        try:
            w = float(getattr(self.page, "width", 0) or 0)
        except Exception:
            w = 0

        if w <= 0:
            return 360

        return max(280, min(560, int(w * 0.30)))

    def _on_page_resized(self, e):
        # 重繪分類/分片與 C1 KeyListCard，讓大小可跟視窗動態變更
        """

        回傳：None
        """
        try:
            if hasattr(self, "shard_key_column"):
                self.shard_key_column.width = self._dynamic_shard_key_panel_width()
            self._render_query_type_shard_page()
            self._render_shard_detail_keys()
            self.page.update()
        except Exception:
            pass

    def _set_state(self, busy: bool, reason: str, trace: str):
        """

        回傳：None
        """
        self.ui_busy = busy
        self.busy_reason = reason

        if busy:
            if reason == "RELOADING":
                label = "重新載入中"
            elif reason == "SAVING":
                label = "儲存中"
            elif reason == "ROTATING":
                label = "輪替分片中"
            else:
                label = "處理中"
            self.overview_status.value = f"狀態：{label}..."
            self.overview_status.color = theme.BLUE_700
        else:
            self.overview_status.value = "狀態：就緒"
            self.overview_status.color = theme.GREEN_700

        self.overview_trace.value = trace
        if hasattr(self, "query_status"):
            self._recalc_query_status()
        self._refresh_disabled_state()
        self.page.update()

    def _refresh_disabled_state(self):
        """

        回傳：None
        """
        if hasattr(self, "btn_reload_all"):
            self.btn_reload_all.disabled = self.ui_busy
        if hasattr(self, "btn_refresh_stats"):
            self.btn_refresh_stats.disabled = self.ui_busy
        if hasattr(self, "btn_rebuild_index"):
            self.btn_rebuild_index.disabled = self.ui_busy

        if hasattr(self, "btn_query_refresh_index"):
            self.btn_query_refresh_index.disabled = self.ui_busy
        if hasattr(self, "btn_query_search"):
            self.btn_query_search.disabled = self.ui_busy
        if hasattr(self, "btn_query_clear"):
            self.btn_query_clear.disabled = self.ui_busy
        if hasattr(self, "btn_page_first"):
            self.btn_page_first.disabled = (
                self.ui_busy or getattr(self, "query_page", 1) <= 1
            )
        if hasattr(self, "btn_page_prev"):
            self.btn_page_prev.disabled = (
                self.ui_busy or getattr(self, "query_page", 1) <= 1
            )
        if hasattr(self, "btn_page_next"):
            self.btn_page_next.disabled = self.ui_busy or getattr(
                self, "query_page", 1
            ) >= getattr(self, "query_total_pages", 1)
        if hasattr(self, "btn_page_last"):
            self.btn_page_last.disabled = self.ui_busy or getattr(
                self, "query_page", 1
            ) >= getattr(self, "query_total_pages", 1)
        if hasattr(self, "dd_page_size"):
            self.dd_page_size.disabled = self.ui_busy
        if hasattr(self, "tf_page_jump"):
            self.tf_page_jump.read_only = self.ui_busy
        if hasattr(self, "btn_apply_dst"):
            self.btn_apply_dst.disabled = (
                self.ui_busy or getattr(self, "query_selected_result", None) is None
            )
        if hasattr(self, "btn_revert_dst"):
            self.btn_revert_dst.disabled = (
                self.ui_busy or getattr(self, "query_selected_result", None) is None
            )
        if hasattr(self, "btn_apply_history_old"):
            self.btn_apply_history_old.disabled = (
                self.ui_busy
                or getattr(self, "query_history_selected_event", None) is None
            )
        if hasattr(self, "btn_back_to_shard_list"):
            self.btn_back_to_shard_list.disabled = self.ui_busy

        if hasattr(self, "btn_shard_page_first"):
            self.btn_shard_page_first.disabled = (
                self.ui_busy or getattr(self, "shard_detail_page", 1) <= 1
            )
        if hasattr(self, "btn_shard_page_prev"):
            self.btn_shard_page_prev.disabled = (
                self.ui_busy or getattr(self, "shard_detail_page", 1) <= 1
            )
        if hasattr(self, "btn_shard_page_next"):
            self.btn_shard_page_next.disabled = self.ui_busy or getattr(
                self, "shard_detail_page", 1
            ) >= getattr(self, "shard_detail_total_pages", 1)
        if hasattr(self, "btn_shard_page_last"):
            self.btn_shard_page_last.disabled = self.ui_busy or getattr(
                self, "shard_detail_page", 1
            ) >= getattr(self, "shard_detail_total_pages", 1)
        if hasattr(self, "tf_shard_key_filter"):
            self.tf_shard_key_filter.read_only = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_file", "")
            )
        if hasattr(self, "btn_shard_src_preview"):
            self.btn_shard_src_preview.disabled = (
                self.ui_busy
                or not bool(getattr(self, "shard_detail_selected_key", ""))
                or getattr(self, "shard_detail_src_mode", "preview") == "preview"
            )
        if hasattr(self, "btn_shard_src_raw"):
            self.btn_shard_src_raw.disabled = (
                self.ui_busy
                or not bool(getattr(self, "shard_detail_selected_key", ""))
                or getattr(self, "shard_detail_src_mode", "preview") == "raw"
            )
        if hasattr(self, "btn_shard_dst_apply"):
            self.btn_shard_dst_apply.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_shard_dst_revert"):
            self.btn_shard_dst_revert.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_shard_dst_copy"):
            self.btn_shard_dst_copy.disabled = (
                self.ui_busy
                or not bool(getattr(self, "shard_detail_selected_key", ""))
                or not bool(
                    str(
                        getattr(getattr(self, "shard_dst_field", None), "value", "")
                        or ""
                    ).strip()
                )
            )
        if hasattr(self, "btn_shard_dst_restore_latest"):
            self.btn_shard_dst_restore_latest.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_open_shard_history_drawer"):
            self.btn_open_shard_history_drawer.disabled = self.ui_busy or not bool(
                getattr(self, "shard_detail_selected_key", "")
            )
        if hasattr(self, "btn_shard_apply_history_old"):
            self.btn_shard_apply_history_old.disabled = (
                self.ui_busy
                or getattr(self, "shard_history_selected_event", None) is None
            )

    # 與舊測試相容：集中提交 UI 更新
    def commit_ui(self, controls=None):
        """

        回傳：None
        """
        try:
            for c in controls or []:
                if hasattr(c, "update"):
                    c.update()
            if hasattr(self, "page") and self.page:
                self.page.update()
        except Exception as ex:
            self._append_log(f"[WARN] UI refresh 異常: {ex}")

    # 與舊測試相容：run_id guard 的狀態更新入口
    def set_ui_state(
        self, busy: bool, reason: str, trace: str, run_id: int | None = None
    ):
        """

        回傳：None
        """
        current_action_id = getattr(
            getattr(self, "_controller", None), "current_action_id", None
        )
        if (
            run_id is not None
            and current_action_id is not None
            and run_id < current_action_id
        ):
            self._append_log(
                f"[WARN] 忽略過期狀態更新 run_id={run_id} < current_action_id={current_action_id}"
            )
            return

        self.ui_busy = bool(busy)
        self.busy_reason = reason or ""

        if hasattr(self, "_ui_state") and self._ui_state is not None:
            self._ui_state.busy = self.ui_busy
            self._ui_state.reason = self.busy_reason
            self._ui_state.trace = trace

        if hasattr(self, "overview_trace"):
            self.overview_trace.value = trace

        if hasattr(self, "overview_status"):
            if (
                hasattr(self, "_presenter")
                and hasattr(self._presenter, "status_text")
                and hasattr(self, "_ui_state")
            ):
                self.overview_status.value = self._presenter.status_text(self._ui_state)
            else:
                self.overview_status.value = (
                    "狀態：忙碌" if self.ui_busy else "狀態：就緒"
                )

        self._refresh_disabled_state()
        self.commit_ui(
            [
                getattr(self, "overview_status", None),
                getattr(self, "overview_trace", None),
            ]
        )

    def _show_snack_bar(self, message: str, color: str = theme.RED_400):
        """
        顯示底部的快訊通知 (SnackBar)
        :param message: 要顯示的文字訊息
        :param color: SnackBar 的背景顏色，預設為 RED_400
        """
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _append_log(self, text: str):
        """

        回傳：None
        """
        if text.startswith("[ERROR"):
            log_error(text)
        elif text.startswith("[WARN"):
            log_warning(text)
        else:
            log_info(text)

        self._all_logs.append(text)
        if len(self._all_logs) > 1500:
            self._all_logs = self._all_logs[-1500:]
        self._render_logs()

    def _notify(self, message: str, level: str = "info"):
        """

        回傳：None
        """
        lv = (level or "info").lower()
        if lv == "error":
            self._append_log(f"[ERROR/錯誤] {message}")
            self._show_snack_bar(message, theme.RED_400)
        elif lv == "warn":
            self._append_log(f"[WARN/警告] {message}")
            self._show_snack_bar(message, theme.AMBER_700)
        else:
            self._append_log(f"[INFO/資訊] {message}")
            self._show_snack_bar(message, theme.BLUE_400)

    # =========================================================
    # Overview page
    # =========================================================
    def _build_overview_page(self):
        """總覽頁組裝（非查詢區）。

        已抽到 cache_manager/cache_overview_panel.py：
        - 本檔保留事件路由與資料狀態
        - 大段 UI 結構移到 panel，降低主檔閱讀負擔
        """
        return build_overview_page(
            overview_text=self.overview_text,
            type_list=self.type_list,
            overview_status=self.overview_status,
            overview_trace=self.overview_trace,
            btn_reload_all=self.btn_reload_all,
            btn_refresh_stats=self.btn_refresh_stats,
            btn_rebuild_index=self.btn_rebuild_index,  # A3 搜尋功能
            sw_log_only_error=self.sw_log_only_error,
            btn_log_copy=self.btn_log_copy,
            btn_log_clear=self.btn_log_clear,
            log_list=self.log_list,
        )

    def _build_query_entry_page(self):
        """

    
        """
        self.query_sub_tabs = ft.Tabs(
            selected_index=0,
            animation_duration=200,
            expand=True,
            on_change=self._on_query_sub_tab_change,
            tabs=[
                ft.Tab(text="查詢區", content=self.query_search_card),
                ft.Tab(text="分類/分片", content=self.query_type_shard_card),
            ],
        )

        return ft.Container(
            expand=True,
            bgcolor=theme.WHITE,
            padding=8,
            alignment=ft.alignment.top_left,
            content=self.query_sub_tabs,
        )

    def _render_logs(self):
        """

        回傳：None
        """
        self.log_list.controls.clear()
        rows = self._all_logs
        if self._only_error:
            rows = [x for x in rows if ("[ERROR" in x or "[WARN" in x)]
        for line in rows[-800:]:
            self.log_list.controls.append(ft.Text(line, size=12, selectable=True))
        self.page.update()

    def _on_log_filter_changed(self, e):
        """

        回傳：None
        """
        self._only_error = bool(self.sw_log_only_error.value)
        self._render_logs()

    def _clear_logs(self):
        """

        回傳：None
        """
        self._all_logs.clear()
        self._render_logs()

    def _copy_logs(self):
        """

        回傳：None
        """
        txt = "\n".join(self._all_logs)
        try:
            self.page.set_clipboard(txt)
            self._show_snack_bar("已複製日誌", theme.BLUE_400)
        except Exception:
            self._show_snack_bar("複製失敗", theme.RED_400)

    def _iter_type_states(self, data: dict):
        """

    
        """
        raw_types = data.get("types") or {}
        if isinstance(raw_types, dict):
            return raw_types.items()
        if isinstance(raw_types, list):
            pairs = []
            for item in raw_types:
                if isinstance(item, dict):
                    ctype = (
                        item.get("cache_type") or item.get("type") or item.get("name")
                    )
                    if ctype:
                        pairs.append((ctype, item))
            return pairs
        return []

    def _render_type_list(self, data: dict):
        """

        回傳：None
        """
        self.type_list.controls.clear()

        for ctype, st in self._iter_type_states(data):
            entries_count = st.get("entries_count", 0)
            new_count = st.get("session_new_count", 0)
            dirty = bool(st.get("is_dirty", False))
            shard = st.get("active_shard_id", "-")
            shard_entries = int(st.get("active_shard_entries", 0) or 0)
            shard_capacity = int(st.get("shard_capacity", 2500) or 2500)
            usage_ratio = (
                min(1.0, shard_entries / shard_capacity) if shard_capacity > 0 else 0.0
            )

            if usage_ratio >= 1.0:
                usage_color = theme.RED_500
                usage_text_color = theme.RED_700
            elif usage_ratio >= 0.9:
                usage_color = theme.AMBER_500
                usage_text_color = theme.AMBER_800
            else:
                usage_color = theme.BLUE_400
                usage_text_color = theme.BLUE_700

            status_chip = ft.Container(
                padding=ft.padding.symmetric(horizontal=8, vertical=2),
                border_radius=20,
                bgcolor=theme.AMBER_100 if dirty else theme.GREEN_100,
                content=ft.Text("有變更" if dirty else "無變更", size=11),
            )

            actions = ft.Row(
                [
                    ft.TextButton(
                        "重新載入",
                        icon=ft.Icons.REFRESH,
                        on_click=lambda e, t=ctype: self._on_reload_one(t),
                    ),
                    ft.TextButton(
                        "新分片",
                        icon=ft.Icons.SAVE,
                        on_click=lambda e, t=ctype: self._on_save_one_new(t),
                    ),
                    ft.TextButton(
                        "補滿舊檔",
                        icon=ft.Icons.SAVE_AS,
                        on_click=lambda e, t=ctype: self._on_save_one_fill(t),
                    ),
                    ft.TextButton(
                        "輪替分片",
                        icon=ft.Icons.ROTATE_RIGHT,
                        on_click=lambda e, t=ctype: self._on_rotate_one(t),
                    ),
                    ft.TextButton(
                        "分析",
                        icon=ft.Icons.INSIGHTS,
                        on_click=lambda e, t=ctype: self._on_analyze_one(t),
                    ),
                    ft.TextButton(
                        "切換查詢",
                        icon=ft.Icons.MANAGE_SEARCH,
                        on_click=lambda e, t=ctype: self._on_jump_to_query_type(t),
                    ),
                ],
                wrap=True,
            )

            self.type_list.controls.append(
                ft.Container(
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=10,
                    padding=10,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(ctype, weight=ft.FontWeight.BOLD),
                                    ft.Container(expand=True),
                                    status_chip,
                                ]
                            ),
                            ft.Text(
                                f"筆數: {entries_count} | 新增: {new_count} | 分片: {shard}",
                                size=12,
                                color=theme.GREY_700,
                            ),
                            ft.Text(
                                f"分片使用率: {shard_entries}/{shard_capacity}",
                                size=11,
                                color=usage_text_color,
                            ),
                            ft.ProgressBar(
                                value=usage_ratio,
                                height=6,
                                color=usage_color,
                                bgcolor=theme.BLUE_50,
                            ),
                            actions,
                        ],
                        spacing=6,
                    ),
                )
            )

        if not self.type_list.controls:
            self.type_list.controls.append(
                ft.Text("目前沒有可顯示的分類資料", color=theme.GREY_600)
            )

        self.page.update()

    def _refresh_overview_ui(self, data: dict):
        """

        回傳：None
        """
        self._last_overview_data = data or {}
        ts = time.strftime("%H:%M:%S")
        self.overview_text.value = (
            f"總筆數: {data.get('total_entries', 0)} | "
            f"有變更的類型: {data.get('dirty_type_count', 0)} | "
            f"最近重新載入: {data.get('last_reload_at', '-') or '-'} | "
            f"最近儲存: {data.get('last_save_at', '-') or '-'} | "
            f"快取根目錄: {data.get('cache_root', '-') or '-'} | "
            f"UI更新: {ts}"
        )
        self._render_type_list(data)

    def _load_overview(self):
        """

        回傳：None
        """
        try:
            data = cache_get_overview_service()
        except Exception as ex:
            self._append_log(f"[WARN] 讀取總覽失敗：{ex}")
            self._append_log(traceback.format_exc())
            data = {}

        self._refresh_overview_ui(data)
        self._refresh_query_type_options()
        self._render_query_type_shard_page()

    def _run_action(self, reason: str, work_fn, success_msg: str):
        """"""
        return run_cache_action(self, reason, work_fn, success_msg)

    # top actions
    def _on_reload_all(self, e):
        """

        回傳：None
        """
        self._run_action(
            "RELOADING", lambda: cache_reload_service(), "已重新載入全部快取"
        )

    def _on_save_all_new(self, e):
        """

        回傳：None
        """
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(write_new_shard=True),
            "已儲存全部新分片",
        )

    def _on_save_all_fill(self, e):
        """

        回傳：None
        """
        if hasattr(self, "chk_danger_confirm") and not bool(
            getattr(self.chk_danger_confirm, "value", False)
        ):
            self._notify("尚未勾選高風險確認", "warn")
            return
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(write_new_shard=False),
            "已補滿活躍分片",
        )

    def _on_refresh_stats(self, e):
        """

        回傳：None
        """
        self._load_overview()
        self._notify("已刷新統計", "info")

    def _on_rebuild_index(self, e):
        """重建搜尋索引（A3 功能）"""
        if self.ui_busy:
            self._notify("目前正在處理，請稍候", "warn")
            return

        self._set_state(True, "INDEXING", "trace: 正在重建搜尋索引...")

        try:
            result = cache_rebuild_index_service()

            if result.get("success"):
                msg = result.get("message", "重建完成")
                self._append_log(f"[INFO] {msg}")
                self._notify(msg, "info")
            else:
                error = result.get("error", "未知錯誤")
                self._append_log(f"[ERROR] 重建索引失敗: {error}")
                self._notify(f"重建失敗: {error}", "error")

        except Exception as ex:
            self._append_log(f"[ERROR] 重建索引異常: {ex}")
            self._append_log(traceback.format_exc())
            self._notify(f"重建失敗: {ex}", "error")

        finally:
            self._set_state(False, "READY", "trace: 重建完成")

    # overview 集中操作已移除，功能保留在分類卡按鈕

    # per-type actions
    def _on_reload_one(self, cache_type: str):
        """

        回傳：None
        """
        self._run_action(
            "RELOADING",
            lambda: cache_reload_type_service(cache_type),
            f"已重新載入單一分類：{cache_type}",
        )

    def _on_save_one_new(self, cache_type: str):
        """

        回傳：None
        """
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(
                write_new_shard=True, only_types=[cache_type]
            ),
            f"已儲存新分片：{cache_type}",
        )

    def _on_save_one_fill(self, cache_type: str):
        """

        回傳：None
        """
        if hasattr(self, "chk_danger_confirm") and not bool(
            getattr(self.chk_danger_confirm, "value", False)
        ):
            self._notify("尚未勾選高風險確認", "warn")
            return
        self._run_action(
            "SAVING",
            lambda: cache_save_all_service(
                write_new_shard=False, only_types=[cache_type]
            ),
            f"已補滿舊檔：{cache_type}",
        )

    def _on_rotate_one(self, cache_type: str):
        """

    
        """

        def _work():
            """

        
            """
            ok = cache_rotate_service(cache_type)
            if not ok:
                raise RuntimeError(f"輪替失敗: {cache_type}")
            return cache_get_overview_service()

        self._run_action("ROTATING", _work, f"已輪替分片：{cache_type}")

    def _on_analyze_one(self, cache_type: str):
        """

        回傳：None
        """
        target = None
        for ctype, st in self._iter_type_states(self._last_overview_data):
            if ctype == cache_type:
                target = st
                break

        if not target:
            self._notify(f"找不到分類資料：{cache_type}", "warn")
            return

        entries_count = target.get("entries_count", 0)
        new_count = target.get("session_new_count", 0)
        dirty = "有變更" if bool(target.get("is_dirty", False)) else "無變更"
        shard = target.get("active_shard_id", "-")
        shard_entries = int(target.get("active_shard_entries", 0) or 0)
        shard_capacity = int(target.get("shard_capacity", 2500) or 2500)
        message = f"分析 {cache_type}：筆數={entries_count}，新增={new_count}，狀態={dirty}，分片={shard}，使用率={shard_entries}/{shard_capacity}"
        self._append_log(f"[ANALYZE] {message}")
        self._notify(message, "info")

    def _on_jump_to_query_type(self, cache_type: str):
        # 切到查詢頁 -> 查詢區，並預先設定 KEY + 指定分類
        """

        回傳：None
        """
        if hasattr(self, "main_tabs"):
            self.main_tabs.selected_index = 1
        if hasattr(self, "query_sub_tabs"):
            self.query_sub_tabs.selected_index = 0

        if hasattr(self, "dd_query_mode"):
            self.dd_query_mode.value = "KEY"

        if hasattr(self, "dd_query_type"):
            options = [str(opt.key) for opt in (self.dd_query_type.options or [])]
            self.dd_query_type.value = cache_type if cache_type in options else "ALL"

        self.query_search_hint.value = (
            f"已切換到查詢區：模式=KEY，分類={self.dd_query_type.value or 'ALL'}"
        )
        self.query_search_hint.color = theme.BLUE_700
        self.page.update()

    # =========================================================
    # Query phase-2: search block
    # =========================================================
    def _refresh_query_type_options(self):
        """

        回傳：None
        """
        types = sorted(
            [ctype for ctype, _ in self._iter_type_states(self._last_overview_data)]
        )

        if hasattr(self, "dd_query_type"):
            self.dd_query_type.options = [ft.dropdown.Option("ALL", "全部")]
            self.dd_query_type.options.extend([ft.dropdown.Option(t, t) for t in types])
            if not self.dd_query_type.value:
                self.dd_query_type.value = "ALL"

        # overview 分類下拉已移除（按鈕維持在分類卡）

    def _load_shard_rows(
        self, cache_type: str, active_shard_id: str, shard_capacity: int
    ) -> list[dict]:
        """

    
        """
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return []

        type_dir = Path(root) / cache_type
        if not type_dir.exists():
            return []

        def _sort_key(path: Path):
            """

        
            """
            stem = path.stem
            m = re.search(r"(\d+)$", stem)
            seq = int(m.group(1)) if m else -1
            return (seq, stem.lower())

        active_filename = (
            f"{cache_type}_{str(active_shard_id)}.json"
            if str(active_shard_id or "").strip()
            else ""
        )

        shard_files: list[Path] = []
        for fp in type_dir.glob("*.json"):
            name = fp.name.lower()
            # 排除非 shard 參考檔
            if name == f"{cache_type.lower()}_cache_main.json":
                continue
            shard_files.append(fp)

        rows: list[dict] = []
        for fp in sorted(shard_files, key=_sort_key, reverse=True):
            key_count = 0
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    key_count = len(raw)
                elif isinstance(raw, list):
                    key_count = len(raw)
            except Exception:
                key_count = 0

            rows.append(
                {
                    "filename": fp.name,
                    "key_count": key_count,
                    "is_active": fp.name == active_filename,
                    "capacity": shard_capacity,
                }
            )
        return rows

    def _load_shard_keys(self, cache_type: str, filename: str) -> list[str]:
        """

    
        """
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return []

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return []

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return []

        if isinstance(raw, dict):
            return sorted([str(k) for k in raw.keys()])

        if isinstance(raw, list):
            out = []
            for idx, item in enumerate(raw):
                if isinstance(item, dict) and item.get("key"):
                    out.append(str(item.get("key")))
                else:
                    out.append(f"[{idx}]")
            return out

        return []

    def _set_shard_detail_page(self, page: int):
        """

        回傳：None
        """
        total = len(self.shard_detail_keys)
        self.shard_detail_total_pages = max(
            1, (total + self.shard_detail_page_size - 1) // self.shard_detail_page_size
        )
        self.shard_detail_page = max(1, min(page, self.shard_detail_total_pages))

    def _render_shard_detail_keys(self):
        """

        回傳：None
        """
        if not hasattr(self, "shard_detail_key_list"):
            return

        self.shard_detail_key_list.controls.clear()

        if not self.shard_detail_selected_type or not self.shard_detail_selected_file:
            self.shard_detail_selected_key = ""
            self.shard_detail_meta.value = "尚未選擇分片"
            if hasattr(self, "shard_workspace_meta"):
                self.shard_workspace_meta.value = "尚未選擇分片"
            self.shard_page_info.value = "第 1 頁 / 共 1 頁"
            self.shard_total_info.value = "共 0 keys | 每頁 50"
            self.shard_dst_loaded_sig = None
            self.shard_dst_original = ""
            self.shard_detail_key_list.controls.append(
                ft.Text(
                    "請先在上方分片清單點選一個 shard",
                    size=11,
                    color=theme.GREY_600,
                )
            )
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._refresh_disabled_state()
            return

        all_keys = list(self.shard_detail_keys)
        keyword = (
            str(
                (
                    self.tf_shard_key_filter.value
                    if hasattr(self, "tf_shard_key_filter")
                    else ""
                )
                or ""
            )
            .strip()
            .lower()
        )
        filtered_keys = (
            [k for k in all_keys if keyword in k.lower()] if keyword else all_keys
        )

        total_filtered = len(filtered_keys)
        self.shard_detail_total_pages = max(
            1,
            (total_filtered + self.shard_detail_page_size - 1)
            // self.shard_detail_page_size,
        )
        self.shard_detail_page = max(
            1, min(self.shard_detail_page, self.shard_detail_total_pages)
        )

        start = (self.shard_detail_page - 1) * self.shard_detail_page_size
        end = start + self.shard_detail_page_size
        page_keys = filtered_keys[start:end]

        if (
            self.shard_detail_selected_key
            and self.shard_detail_selected_key not in filtered_keys
        ):
            self.shard_detail_selected_key = ""
        if not self.shard_detail_selected_key and filtered_keys:
            self.shard_detail_selected_key = filtered_keys[0]

        self.shard_detail_meta.value = (
            f"{self.shard_detail_selected_type} / {self.shard_detail_selected_file}"
        )
        if hasattr(self, "shard_workspace_meta"):
            self.shard_workspace_meta.value = f"目前分片：{self.shard_detail_selected_type} / {self.shard_detail_selected_file}"
        if not page_keys:
            if keyword and all_keys:
                self.shard_detail_key_list.controls.append(
                    ft.Text(
                        "此篩選條件沒有符合的 key", size=11, color=theme.GREY_600
                    )
                )
            else:
                self.shard_detail_key_list.controls.append(
                    ft.Text("此分片目前沒有 key", size=11, color=theme.GREY_600)
                )
        else:
            for idx, key in enumerate(page_keys, start=start + 1):
                selected = key == self.shard_detail_selected_key
                self.shard_detail_key_list.controls.append(
                    ft.Container(
                        padding=6,
                        border=ft.border.all(
                            1,
                            theme.BLUE_300
                            if selected
                            else theme.OUTLINE_VARIANT,
                        ),
                        border_radius=6,
                        bgcolor=theme.BLUE_50 if selected else None,
                        tooltip=key,
                        on_click=lambda e, k=key: self._on_select_shard_key(k),
                        content=ft.Text(
                            f"{idx}. {key}",
                            size=11,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            max_lines=1,
                        ),
                    )
                )

        self.shard_page_info.value = (
            f"第 {self.shard_detail_page} 頁 / 共 {self.shard_detail_total_pages} 頁"
        )
        if keyword:
            self.shard_total_info.value = f"共 {total_filtered}/{len(all_keys)} keys | 每頁 {self.shard_detail_page_size}"
        else:
            self.shard_total_info.value = (
                f"共 {len(all_keys)} keys | 每頁 {self.shard_detail_page_size}"
            )
        self._render_shard_src_panel()
        self._render_shard_dst_panel()
        self._refresh_disabled_state()

    def _on_shard_key_filter_change(self, e):
        """

        回傳：None
        """
        self.shard_detail_page = 1
        self._render_shard_detail_keys()
        self.page.update()

    def _set_shard_workspace_visible(self, visible: bool):
        """

        回傳：None
        """
        show_workspace = bool(visible)
        if hasattr(self, "shard_nav_view"):
            self.shard_nav_view.visible = not show_workspace
        if hasattr(self, "shard_workspace_card"):
            self.shard_workspace_card.visible = show_workspace

    def _open_shard_workspace_tab(self):
        """

        回傳：None
        """
        self._set_shard_workspace_visible(True)

    def _on_back_to_shard_list(self, e):
        """

        回傳：None
        """
        self._set_shard_workspace_visible(False)
        self.page.update()

    def _on_select_shard_row(self, cache_type: str, filename: str):
        """

        回傳：None
        """
        self.shard_detail_selected_type = cache_type
        self.shard_detail_selected_file = filename
        self.shard_detail_keys = self._load_shard_keys(cache_type, filename)
        self.shard_detail_selected_key = (
            self.shard_detail_keys[0] if self.shard_detail_keys else ""
        )
        self.shard_detail_src_mode = "preview"
        self.shard_detail_page = 1
        if hasattr(self, "tf_shard_key_filter"):
            self.tf_shard_key_filter.value = ""
        self.shard_dst_loaded_sig = None
        self._render_query_type_shard_page()
        self._open_shard_workspace_tab()
        self.page.update()

    def _on_select_shard_key(self, key: str):
        """

        回傳：None
        """
        if key != self.shard_detail_selected_key:
            self.shard_dst_loaded_sig = None
        self.shard_detail_selected_key = key
        self._render_shard_detail_keys()
        # 如果歷史紀錄視窗已開啟，自動更新
        if hasattr(self, "shard_history_window") and self.shard_history_window.visible:
            self._render_shard_history()
        self.page.update()

    def _load_shard_entry(
        self, cache_type: str, filename: str, key: str
    ) -> dict | None:
        """

    
        """
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        if not root:
            return None

        fp = Path(root) / cache_type / filename
        if not fp.exists():
            return None

        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return None

        if isinstance(raw, dict):
            entry = raw.get(key)
            return entry if isinstance(entry, dict) else None

        return None

    def _format_shard_src_text(self, src_text: str, mode: str) -> str:
        """

    
        """
        src = str(src_text or "")
        if mode == "raw":
            return json.dumps(src, ensure_ascii=False)
        return src.replace("\\r\\n", "\n").replace("\\n", "\n")

    def _render_shard_src_panel(self):
        """

        回傳：None
        """
        if not hasattr(self, "shard_src_field"):
            return

        if (
            not self.shard_detail_selected_type
            or not self.shard_detail_selected_file
            or not self.shard_detail_selected_key
        ):
            self.shard_src_meta.value = "SRC：請先選擇 key"
            self.shard_src_field.value = ""
            self._refresh_disabled_state()
            return

        ctype = self.shard_detail_selected_type
        key = self.shard_detail_selected_key
        filename = self.shard_detail_selected_file

        entry = cache_get_entry_service(ctype, key)
        if not isinstance(entry, dict):
            entry = self._load_shard_entry(ctype, filename, key)

        src_text = ""
        if isinstance(entry, dict):
            src_text = str(entry.get("src", ""))

        mode_text = (
            "👁️ 預覽" if self.shard_detail_src_mode == "preview" else "</> 原始碼"
        )
        self.shard_src_meta.value = f"SRC：{key} | 模式：{mode_text}"
        self.shard_src_field.value = self._format_shard_src_text(
            src_text, self.shard_detail_src_mode
        )
        self._refresh_disabled_state()

    def _on_shard_src_preview_mode(self, e):
        """

        回傳：None
        """
        self.shard_detail_src_mode = "preview"
        self._render_shard_src_panel()
        self.page.update()

    def _on_shard_src_raw_mode(self, e):
        """

        回傳：None
        """
        self.shard_detail_src_mode = "raw"
        self._render_shard_src_panel()
        self.page.update()

    def _normalize_cache_text(self, text: str) -> str:
        """

    
        """
        return str(text or "").replace("\\r\\n", "\n").replace("\\n", "\n")

    def _render_shard_dst_panel(self):
        """

        回傳：None
        """
        if not hasattr(self, "shard_dst_field"):
            return

        ctype = str(self.shard_detail_selected_type or "")
        filename = str(self.shard_detail_selected_file or "")
        key = str(self.shard_detail_selected_key or "")

        if not ctype or not filename or not key:
            self.shard_dst_loaded_sig = None
            self.shard_dst_original = ""
            self.shard_dst_meta.value = "DST：請先選擇 key"
            self.shard_dst_field.value = ""
            self._refresh_disabled_state()
            return

        current_sig = (ctype, filename, key)
        if self.shard_dst_loaded_sig != current_sig:
            entry = cache_get_entry_service(ctype, key)
            if not isinstance(entry, dict):
                entry = self._load_shard_entry(ctype, filename, key)

            dst_text = ""
            if isinstance(entry, dict):
                dst_text = self._normalize_cache_text(str(entry.get("dst", "")))

            self.shard_dst_original = dst_text
            self.shard_dst_field.value = dst_text
            self.shard_dst_loaded_sig = current_sig

        self.shard_dst_meta.value = f"DST：{key}"
        self._refresh_disabled_state()

    def _on_shard_dst_apply(self, e):
        """

        回傳：None
        """
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        filename = str(self.shard_detail_selected_file or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not filename or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        old_dst = str(self.shard_dst_original or "")
        new_dst = str(self.shard_dst_field.value or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": filename,
                "old_dst": old_dst,
                "new_dst": new_dst,
                "action": "apply_from_shard_detail",
                "actor": "cache_shard_detail_ui",
            }
            self._history_append_event(ctype, history_event)

            self.shard_dst_original = new_dst
            if self.shard_dst_loaded_sig != (ctype, filename, key):
                self.shard_dst_loaded_sig = (ctype, filename, key)

            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._notify("已套用 C3 DST 並寫入快取", "info")
            self.page.update()
        except Exception as ex:
            self._notify(f"套用 DST 失敗：{ex}", "error")

    def _on_shard_dst_revert(self, e):
        """還原 DST 到原始值（分類分片）"""
        if not self.shard_detail_selected_key:
            self._show_snack_bar("請先選擇 key", theme.AMBER_700)
            return

        self.shard_dst_field.value = str(self.shard_dst_original or "")
        self._show_snack_bar("已還原到原始值", theme.BLUE_400)
        self._refresh_disabled_state()
        self.page.update()

    def _on_shard_dst_copy(self, e):
        """

        回傳：None
        """
        if not self.shard_detail_selected_key:
            self._notify("請先選擇 key", "warn")
            return

        try:
            self.page.set_clipboard(str(self.shard_dst_field.value or ""))
            self._notify("已複製 C3 DST 內容", "info")
        except Exception:
            self._notify("複製失敗", "error")

    def _on_shard_dst_restore_latest(self, e):
        """還原最新歷史紀錄（不立即寫入快取）"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停還原", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        # 載入最新歷史紀錄
        records = self._history_load_recent(ctype, key, limit=1)
        if not records:
            self._notify("此 key 目前沒有歷史紀錄", "warn")
            return

        latest = records[0]
        old_dst = str(latest.get("old_dst", ""))

        # 只填入 DST 輸入框，不寫入快取
        self.shard_dst_field.value = old_dst
        self._notify(
            "已載入最新歷史紀錄到 DST（尚未寫入快取，請點「套用 DST」儲存）", "info"
        )
        self.page.update()

    def _on_select_shard_history_event(self, event: dict):
        """選擇歷史事件"""
        self.shard_history_selected_event = event
        self._render_shard_history()
        self.page.update()

    def _render_shard_history(self):
        """渲染分類分片的歷史紀錄列表"""
        if not hasattr(self, "shard_history_list"):
            return

        self.shard_history_list.controls.clear()
        self.shard_history_preview.value = ""

        ctype = str(self.shard_detail_selected_type or "")
        key = str(self.shard_detail_selected_key or "")

        if not ctype or not key:
            self.shard_history_records = []
            self.shard_history_selected_event = None
            self.shard_history_key_text.value = "Key: -"
            self.shard_history_selected_text.value = "未選取歷史紀錄"
            self.shard_history_list.controls.append(
                ft.Text("請先選擇 key", size=11, color=theme.GREY_600)
            )
            self._refresh_disabled_state()
            return

        self.shard_history_key_text.value = f"Key: {key} ({ctype})"

        self.shard_history_records = self._history_load_recent(ctype, key, limit=20)
        if not self.shard_history_records:
            self.shard_history_selected_event = None
            self.shard_history_selected_text.value = "此 key 目前沒有歷史紀錄"
            self.shard_history_list.controls.append(
                ft.Text("尚無歷史紀錄", size=11, color=theme.GREY_600)
            )
            self._refresh_disabled_state()
            return

        # 若當前選取不在新清單中，就預設第一筆
        def _ev_id(ev: dict):
            """

        
            """
            return (
                str(ev.get("ts", "")),
                str(ev.get("old_dst", "")),
                str(ev.get("new_dst", "")),
            )

        selected_id = (
            _ev_id(self.shard_history_selected_event)
            if self.shard_history_selected_event
            else None
        )
        found = None
        for ev in self.shard_history_records:
            if selected_id and _ev_id(ev) == selected_id:
                found = ev
                break
        self.shard_history_selected_event = found or self.shard_history_records[0]

        for ev in self.shard_history_records:
            ts = str(ev.get("ts", ""))
            action = str(ev.get("action", "apply"))
            old_dst = str(ev.get("old_dst", ""))
            new_dst = str(ev.get("new_dst", ""))
            is_selected = _ev_id(ev) == _ev_id(self.shard_history_selected_event)

            self.shard_history_list.controls.append(
                ft.Container(
                    padding=6,
                    border=ft.border.all(
                        1,
                        theme.BLUE_200
                        if is_selected
                        else theme.OUTLINE_VARIANT,
                    ),
                    border_radius=8,
                    bgcolor=theme.BLUE_50 if is_selected else None,
                    on_click=lambda e, item=ev: self._on_select_shard_history_event(
                        item
                    ),
                    content=ft.Column(
                        [
                            ft.Text(
                                f"{ts} | {action}", size=10, color=theme.GREY_700
                            ),
                            ft.Text(f"old: {old_dst[:60]}", size=11, no_wrap=False),
                            ft.Text(f"new: {new_dst[:60]}", size=11, no_wrap=False),
                        ],
                        spacing=2,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                )
            )

        self._update_shard_history_preview()
        self._refresh_disabled_state()

    def _update_shard_history_preview(self):
        """更新歷史預覽"""
        ev = self.shard_history_selected_event
        if not ev:
            self.shard_history_selected_text.value = "未選取歷史紀錄"
            self.shard_history_preview.value = ""
            return

        ts = str(ev.get("ts", ""))
        action = str(ev.get("action", "apply"))
        old_dst = str(ev.get("old_dst", ""))
        new_dst = str(ev.get("new_dst", ""))
        self.shard_history_selected_text.value = f"已選取：{ts} | {action}"
        self.shard_history_preview.value = f"old:\n{old_dst}\n\nnew:\n{new_dst}"

    def _on_shard_apply_selected_history(self, e):
        """套用選取的歷史舊值（分類分片版）"""
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        ctype = str(self.shard_detail_selected_type or "")
        key = str(self.shard_detail_selected_key or "")
        if not ctype or not key:
            self._notify("請先選擇分片與 key", "warn")
            return

        ev = self.shard_history_selected_event
        if not ev:
            self._notify("請先選擇一筆歷史紀錄", "warn")
            return

        new_dst = str(ev.get("old_dst", ""))
        old_dst_now = str(self.shard_dst_original or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用舊值失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": str(self.shard_detail_selected_file or "-"),
                "old_dst": old_dst_now,
                "new_dst": new_dst,
                "action": "revert_from_shard_history",
                "actor": "cache_shard_detail_ui",
            }
            self._history_append_event(ctype, history_event)

            self.shard_dst_original = new_dst
            self.shard_dst_field.value = new_dst
            if self.shard_dst_loaded_sig != (
                ctype,
                self.shard_detail_selected_file,
                key,
            ):
                self.shard_dst_loaded_sig = (
                    ctype,
                    self.shard_detail_selected_file,
                    key,
                )

            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._render_shard_src_panel()
            self._render_shard_dst_panel()
            self._render_shard_history()
            self._notify("已套用選取舊值並寫入快取", "info")
            self.page.update()
        except Exception as ex:
            self._notify(f"套用舊值失敗：{ex}", "error")

    def _on_shard_page_first(self, e):
        """

        回傳：None
        """
        self.shard_detail_page = 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_prev(self, e):
        """

        回傳：None
        """
        self.shard_detail_page -= 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_next(self, e):
        """

        回傳：None
        """
        self.shard_detail_page += 1
        self._render_shard_detail_keys()
        self.page.update()

    def _on_shard_page_last(self, e):
        """

        回傳：None
        """
        self.shard_detail_page = self.shard_detail_total_pages
        self._render_shard_detail_keys()
        self.page.update()

    def _render_query_type_shard_page(self):
        """

        回傳：None
        """
        if not hasattr(self, "query_type_shard_col"):
            return

        self.query_type_shard_col.controls.clear()

        pairs = list(self._iter_type_states(self._last_overview_data))
        if not pairs:
            self.query_type_shard_col.controls.append(
                ft.Text("目前沒有分類資料", color=theme.GREY_600)
            )
            self.shard_detail_selected_type = ""
            self.shard_detail_selected_file = ""
            self.shard_detail_selected_key = ""
            self.shard_detail_src_mode = "preview"
            self.shard_detail_keys = []
            self.shard_detail_page = 1
            self._set_shard_workspace_visible(False)
            self._render_shard_detail_keys()
            return

        valid_selection_pairs = set()
        shard_panel_height = self._dynamic_type_shard_panel_height()

        for ctype, st in pairs:
            entries_count = st.get("entries_count", 0)
            shard = st.get("active_shard_id", "-")
            shard_entries = int(st.get("active_shard_entries", 0) or 0)
            shard_capacity = int(st.get("shard_capacity", 2500) or 2500)
            dirty = "dirty" if bool(st.get("is_dirty", False)) else "clean"

            shard_rows = self._load_shard_rows(ctype, str(shard), shard_capacity)
            shard_controls = []
            if not shard_rows:
                shard_controls.append(
                    ft.Text(
                        "目前沒有可讀取的 shard 檔案", size=11, color=theme.GREY_600
                    )
                )
            else:
                for row in shard_rows:
                    valid_selection_pairs.add((ctype, row["filename"]))
                    mark = " | active" if row["is_active"] else ""
                    selected = (
                        self.shard_detail_selected_type == ctype
                        and self.shard_detail_selected_file == row["filename"]
                    )
                    shard_controls.append(
                        ft.Container(
                            padding=6,
                            border=ft.border.all(
                                1,
                                theme.BLUE_300
                                if selected
                                else theme.OUTLINE_VARIANT,
                            ),
                            border_radius=8,
                            bgcolor=theme.BLUE_50 if selected else None,
                            on_click=lambda e, t=ctype, f=row["filename"]: (
                                self._on_select_shard_row(t, f)
                            ),
                            content=ft.Column(
                                [
                                    ft.Text(row["filename"], size=11, selectable=True),
                                    ft.Text(
                                        f"keys: {row['key_count']}/{row['capacity']}{mark}",
                                        size=10,
                                        color=theme.BLUE_700
                                        if row["is_active"]
                                        else theme.GREY_700,
                                    ),
                                ],
                                spacing=2,
                                horizontal_alignment=ft.CrossAxisAlignment.START,
                            ),
                        )
                    )

            shard_list_container = ft.Container(
                height=shard_panel_height,
                padding=4,
                border=ft.border.all(1, theme.OUTLINE_VARIANT),
                border_radius=8,
                bgcolor=theme.WHITE,
                alignment=ft.alignment.top_left,
                content=ft.ListView(
                    expand=True,
                    spacing=4,
                    auto_scroll=False,
                    controls=shard_controls,
                ),
            )

            self.query_type_shard_col.controls.append(
                ft.Container(
                    padding=8,
                    border=ft.border.all(1, theme.OUTLINE_VARIANT),
                    border_radius=8,
                    bgcolor=theme.WHITE,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(ctype, size=13, weight=ft.FontWeight.BOLD),
                                    ft.TextButton(
                                        "切換查詢",
                                        icon=ft.Icons.MANAGE_SEARCH,
                                        on_click=lambda e, t=ctype: (
                                            self._on_jump_to_query_type(t)
                                        ),
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            ft.Text(
                                f"分片: {shard} | 狀態: {dirty}",
                                size=11,
                                color=theme.GREY_700,
                            ),
                            ft.Text(
                                f"筆數: {entries_count} | shard 使用: {shard_entries}/{shard_capacity}",
                                size=11,
                            ),
                            ft.ExpansionTile(
                                title=ft.Text(
                                    f"分片清單（{len(shard_rows)}）",
                                    weight=ft.FontWeight.BOLD,
                                ),
                                controls=[
                                    ft.Container(
                                        alignment=ft.alignment.top_left,
                                        content=shard_list_container,
                                    )
                                ],
                            ),
                        ],
                        spacing=4,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                )
            )

        if (
            self.shard_detail_selected_type,
            self.shard_detail_selected_file,
        ) not in valid_selection_pairs:
            self.shard_detail_selected_type = ""
            self.shard_detail_selected_file = ""
            self.shard_detail_selected_key = ""
            self.shard_detail_src_mode = "preview"
            self.shard_detail_keys = []
            self.shard_detail_page = 1
            self._set_shard_workspace_visible(False)

        self._render_shard_detail_keys()

    def _active_shard_filename(self, cache_type: str) -> str:
        """

    
        """
        for ctype, st in self._iter_type_states(self._last_overview_data):
            if ctype == cache_type:
                sid = st.get("active_shard_id")
                if sid:
                    return f"{cache_type}_{sid}.json"
        return "-"

    def _type_dirty_text(self, cache_type: str) -> str:
        """

    
        """
        for ctype, st in self._iter_type_states(self._last_overview_data):
            if ctype == cache_type:
                return "dirty" if bool(st.get("is_dirty", False)) else "clean"
        return "-"

    # -------------------- History storage helpers --------------------
    def _history_now_ts(self) -> str:
        return history_now_ts()

    def _history_dirs(self, cache_type: str):
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_dirs(root, cache_type)

    def _history_active_default(self, cache_type: str) -> dict:
        return history_active_default(cache_type)

    def _history_load_active(self, cache_type: str):
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_load_active(root, cache_type)

    def _history_save_active(self, active_path: Path, active: dict):
        return history_save_active(active_path, active)

    def _history_append_event(self, cache_type: str, event: dict):
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_append_event(root, cache_type, event)

    def _history_load_recent(
        self, cache_type: str, key: str, limit: int = 20
    ) -> list[dict]:
        root = str((self._last_overview_data or {}).get("cache_root", "") or "").strip()
        return history_load_recent(root, cache_type, key, limit)

    def _render_query_history(self):
        """

    
        """
        if not hasattr(self, "query_history_list"):
            return

        self.query_history_list.controls.clear()
        self.query_history_preview.value = ""

        row = self.query_selected_result
        if not row:
            self.query_history_records = []
            self.query_history_selected_event = None
            self.query_history_key_text.value = "Key: -"
            self.query_history_selected_text.value = "未選取歷史紀錄"
            self.query_history_list.controls.append(
                ft.Text("請先選擇左側結果", size=11, color=theme.GREY_600)
            )
            self._refresh_disabled_state()
            return

        ctype = str(row.get("cache_type", ""))
        key = str(row.get("key", ""))
        self.query_history_key_text.value = f"Key: {key} ({ctype})"

        self.query_history_records = self._history_load_recent(ctype, key, limit=20)
        if not self.query_history_records:
            self.query_history_selected_event = None
            self.query_history_selected_text.value = "此 key 目前沒有歷史紀錄"
            self.query_history_list.controls.append(
                ft.Text("尚無歷史紀錄", size=11, color=theme.GREY_600)
            )
            self._refresh_disabled_state()
            return

        # 若當前選取不在新清單中，就預設第一筆
        def _ev_id(ev: dict):
            """

        
            """
            return (
                str(ev.get("ts", "")),
                str(ev.get("old_dst", "")),
                str(ev.get("new_dst", "")),
            )

        selected_id = (
            _ev_id(self.query_history_selected_event)
            if self.query_history_selected_event
            else None
        )
        found = None
        for ev in self.query_history_records:
            if selected_id and _ev_id(ev) == selected_id:
                found = ev
                break
        self.query_history_selected_event = found or self.query_history_records[0]

        for ev in self.query_history_records:
            ts = str(ev.get("ts", ""))
            action = str(ev.get("action", "apply"))
            old_dst = str(ev.get("old_dst", ""))
            new_dst = str(ev.get("new_dst", ""))
            is_selected = _ev_id(ev) == _ev_id(self.query_history_selected_event)

            self.query_history_list.controls.append(
                ft.Container(
                    padding=6,
                    border=ft.border.all(
                        1,
                        theme.BLUE_200
                        if is_selected
                        else theme.OUTLINE_VARIANT,
                    ),
                    border_radius=8,
                    bgcolor=theme.BLUE_50 if is_selected else None,
                    on_click=lambda e, item=ev: self._on_select_history_event(item),
                    content=ft.Column(
                        [
                            ft.Text(
                                f"{ts} | {action}", size=10, color=theme.GREY_700
                            ),
                            ft.Text(f"old: {old_dst[:60]}", size=11, no_wrap=False),
                            ft.Text(f"new: {new_dst[:60]}", size=11, no_wrap=False),
                        ],
                        spacing=2,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                )
            )

        self._update_history_preview()
        self._refresh_disabled_state()

    def _update_history_preview(self):
        """

        回傳：None
        """
        ev = self.query_history_selected_event
        if not ev:
            self.query_history_selected_text.value = "未選取歷史紀錄"
            self.query_history_preview.value = ""
            return

        ts = str(ev.get("ts", ""))
        action = str(ev.get("action", "apply"))
        old_dst = str(ev.get("old_dst", ""))
        new_dst = str(ev.get("new_dst", ""))
        self.query_history_selected_text.value = f"已選取：{ts} | {action}"
        self.query_history_preview.value = f"old:\n{old_dst}\n\nnew:\n{new_dst}"

    def _on_open_history_window(self, e, source="query"):
        """打開歷史紀錄浮動視窗（獨立視窗）"""
        # 參數驗證
        if source not in ("query", "shard"):
            self._notify(f"無效的 source 參數：{source}", "error")
            return

        self.history_window_source = source

        # 根據來源開啟對應的獨立視窗
        if source == "query":
            self._render_query_history()
            self.query_history_window.visible = True
            source_text = "查詢區"
        else:  # source == 'shard'
            self._render_shard_history()
            self.shard_history_window.visible = True
            source_text = "分片區"

        self._show_snack_bar(
            f"歷史紀錄視窗已打開（{source_text}，可拖曳標題列移動）", theme.BLUE_400
        )
        self.page.update()

    def _on_close_history_window(self, e):
        """關閉歷史紀錄浮動視窗"""
        self.query_history_window.visible = False
        self.history_window_source = None
        self.page.update()

    def _on_query_history_window_drag(self, e: ft.DragUpdateEvent):
        """拖曳歷史紀錄視窗"""
        win = self.query_history_window
        win.top = max(0, win.top + e.delta_y)
        win.left = max(0, win.left + e.delta_x)
        win.update()

    def _on_query_history_window_resize(self, e: ft.DragUpdateEvent):
        """調整歷史紀錄視窗大小"""
        win = self.query_history_window
        win.width = max(300, win.width + e.delta_x)
        win.height = max(350, win.height + e.delta_y)
        win.update()

    def _on_close_shard_history_window(self, e):
        """關閉分片歷史紀錄浮動視窗"""
        self.shard_history_window.visible = False
        self.history_window_source = None
        self.page.update()

    def _on_shard_history_window_drag(self, e: ft.DragUpdateEvent):
        """拖曳分片歷史紀錄視窗"""
        win = self.shard_history_window
        win.top = max(0, win.top + e.delta_y)
        win.left = max(0, win.left + e.delta_x)
        win.update()

    def _on_shard_history_window_resize(self, e: ft.DragUpdateEvent):
        """調整分片歷史紀錄視窗大小"""
        win = self.shard_history_window
        win.width = max(300, win.width + e.delta_x)
        win.height = max(350, win.height + e.delta_y)
        win.update()

    def _on_tab_change(self, e):
        """Tab 切換時自動關閉歷史紀錄視窗（總覽/管理 ↔ 查詢）"""
        if self.query_history_window.visible:
            self.query_history_window.visible = False
            self.history_window_source = None
        if self.shard_history_window.visible:
            self.shard_history_window.visible = False
            self.history_window_source = None
        self.page.update()

    def _on_query_sub_tab_change(self, e):
        """查詢區內 sub-tab 切換時自動關閉歷史紀錄視窗（查詢區 ↔ 分類/分片）"""
        if self.query_history_window.visible:
            self.query_history_window.visible = False
            self.history_window_source = None
        if self.shard_history_window.visible:
            self.shard_history_window.visible = False
            self.history_window_source = None
        self.page.update()

    def _on_select_history_event(self, event: dict):
        """

        回傳：None
        """
        self.query_history_selected_event = event
        self._render_query_history()
        self.page.update()

    def _on_apply_selected_history(self, e):
        """

        回傳：None
        """
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        if not self.query_selected_result:
            self._notify("請先選擇一筆資料", "warn")
            return

        ev = self.query_history_selected_event
        if not ev:
            self._notify("請先選擇一筆歷史紀錄", "warn")
            return

        ctype = str(self.query_selected_result.get("cache_type", ""))
        key = str(self.query_selected_result.get("key", ""))
        new_dst = str(ev.get("old_dst", ""))

        current = cache_get_entry_service(ctype, key) or {}
        old_dst_now = str(current.get("dst", ""))

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用舊值失敗：找不到目標 key", "error")
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": str(self.query_selected_result.get("shard", "-")),
                "old_dst": old_dst_now,
                "new_dst": new_dst,
                "action": "revert_from_history",
                "actor": "cache_query_ui",
            }
            self._history_append_event(ctype, history_event)

            self.query_original_dst = new_dst
            self.query_detail_dst.value = new_dst
            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._notify("已套用選取舊值並寫入快取", "info")
            self.page.update()
        except Exception as ex:
            self._notify(f"套用舊值失敗：{ex}", "error")

    def _render_query_detail(self):
        """

        回傳：None
        """
        row = self.query_selected_result
        if not row:
            self.query_detail_key.value = "Key: -"
            self.query_detail_type.value = "類型: -"
            self.query_detail_shard.value = "Shard: -"
            self.query_detail_status.value = "Cache 狀態: -"
            self.query_detail_src.value = "-"
            self.query_detail_dst.value = ""
            self.query_original_dst = ""
            self._render_query_history()
            return

        ctype = str(row.get("cache_type", ""))
        key = str(row.get("key", ""))
        shard = str(row.get("shard", "-"))

        entry = cache_get_entry_service(ctype, key) or {}
        src = str(entry.get("src", "")).replace("\\r\\n", "\n").replace("\\n", "\n")
        dst = str(entry.get("dst", "")).replace("\\r\\n", "\n").replace("\\n", "\n")

        self.query_detail_key.value = f"Key: {key}"
        self.query_detail_type.value = f"類型: {ctype}"
        self.query_detail_shard.value = f"Shard: {shard}"
        self.query_detail_status.value = f"Cache 狀態: {self._type_dirty_text(ctype)}"
        self.query_detail_src.value = src or "-"
        self.query_detail_dst.value = dst
        self.query_original_dst = dst
        self._render_query_history()

    def _set_query_page(self, page: int):
        """

        回傳：None
        """
        total = len(self.query_results)
        self.query_total_pages = max(
            1, (total + self.query_page_size - 1) // self.query_page_size
        )
        self.query_page = max(1, min(page, self.query_total_pages))

    def _render_query_results(self):
        """

        回傳：None
        """
        if not hasattr(self, "query_result_list"):
            return

        self._set_query_page(self.query_page)
        self.query_result_list.controls.clear()

        total = len(self.query_results)
        start = (self.query_page - 1) * self.query_page_size
        end = start + self.query_page_size
        page_rows = self.query_results[start:end]

        if not page_rows:
            self.query_result_list.controls.append(
                ft.Row(
                    [
                        ft.Container(
                            expand=True,
                            padding=8,
                            border=ft.border.all(1, theme.OUTLINE_VARIANT),
                            border_radius=8,
                            content=ft.Text("尚未有搜尋結果", color=theme.GREY_600),
                        )
                    ]
                )
            )
        else:
            for row in page_rows:
                key = str(row.get("key", ""))
                cache_type = str(row.get("cache_type", "-"))
                shard = str(row.get("shard", "-"))
                preview = str(row.get("preview", ""))
                selected = (
                    self.query_selected_result is not None
                    and self.query_selected_result.get("cache_type") == cache_type
                    and self.query_selected_result.get("key") == key
                )

                self.query_result_list.controls.append(
                    ft.Row(
                        [
                            ft.Container(
                                expand=True,
                                padding=8,
                                border=ft.border.all(
                                    1,
                                    theme.BLUE_200
                                    if selected
                                    else theme.OUTLINE_VARIANT,
                                ),
                                border_radius=8,
                                bgcolor=theme.BLUE_50 if selected else None,
                                on_click=lambda e, r=row: self._on_select_result(r),
                                content=ft.Column(
                                    [
                                        ft.Text(
                                            f"Key: {key}",
                                            size=12,
                                            weight=ft.FontWeight.BOLD,
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            max_lines=1,
                                        ),
                                        ft.Text(
                                            f"類型: {cache_type} | shard: {shard}",
                                            size=11,
                                            color=theme.GREY_700,
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            max_lines=1,
                                        ),
                                        ft.Text(
                                            f"預覽: {preview}",
                                            size=11,
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            max_lines=2,
                                        ),
                                    ],
                                    spacing=3,
                                    horizontal_alignment=ft.CrossAxisAlignment.START,
                                ),
                            )
                        ]
                    )
                )

        self.tf_page_jump.value = str(self.query_page)
        self.query_page_info.value = f"頁 / 共 {self.query_total_pages}"
        self.query_total_info.value = f"共 {total} 筆"
        self._refresh_disabled_state()

    def _on_select_result(self, row: dict):
        """

        回傳：None
        """
        self.query_selected_result = row
        self._render_query_results()
        self._render_query_detail()
        self.page.update()

    def _on_page_first(self, e):
        """

        回傳：None
        """
        self.query_page = 1
        self._render_query_results()
        self.page.update()

    def _on_page_prev(self, e):
        """

        回傳：None
        """
        self.query_page -= 1
        self._render_query_results()
        self.page.update()

    def _on_page_next(self, e):
        """

        回傳：None
        """
        self.query_page += 1
        self._render_query_results()
        self.page.update()

    def _on_page_last(self, e):
        """

        回傳：None
        """
        self.query_page = self.query_total_pages
        self._render_query_results()
        self.page.update()

    def _on_page_jump(self, e):
        """

        回傳：None
        """
        try:
            p = int((self.tf_page_jump.value or "1").strip())
        except Exception:
            p = 1
        self.query_page = p
        self._render_query_results()
        self.page.update()

    def _on_page_size_change(self, e):
        """

        回傳：None
        """
        try:
            self.query_page_size = int(self.dd_page_size.value or "50")
        except Exception:
            self.query_page_size = 50
        self.query_page = 1
        self._render_query_results()
        self.page.update()

    def _on_apply_dst(self, e):
        """

        回傳：None
        """
        if self.ui_busy:
            self._notify("目前忙碌中，暫停套用", "warn")
            return

        if not self.query_selected_result:
            self._notify("請先選擇一筆資料", "warn")
            return

        ctype = str(self.query_selected_result.get("cache_type", ""))
        key = str(self.query_selected_result.get("key", ""))
        shard = str(self.query_selected_result.get("shard", "-"))
        new_dst = str(self.query_detail_dst.value or "")
        old_dst = str(self.query_original_dst or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._notify("套用失敗：找不到目標 key", "error")
                return

            # 寫入快取檔（不是新增，直接覆寫既有 key 的內容）
            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            # 持久化歷史（jsonl + pretty json）
            history_event = {
                "ts": self._history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": shard,
                "old_dst": old_dst,
                "new_dst": new_dst,
                "action": "apply",
                "actor": "cache_query_ui",
            }
            self._history_append_event(ctype, history_event)

            self.query_original_dst = new_dst
            for row in self.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._notify("已套用並寫入快取", "info")
            self.page.update()
        except Exception as ex:
            self._notify(f"套用失敗：{ex}", "error")

    def _on_revert_dst(self, e):
        """還原 DST 到原始值（查詢區）"""
        if not self.query_selected_result:
            self._show_snack_bar("請先選擇一筆資料", theme.AMBER_700)
            return

        self.query_detail_dst.value = str(self.query_original_dst or "")
        self._show_snack_bar("已還原到原始值", theme.BLUE_400)
        self.page.update()

    def _on_restore_latest_query(self, e):
        """還原最新歷史紀錄（查詢區，不立即寫入快取）"""
        if self.ui_busy:
            self._show_snack_bar("目前忙碌中，暫停還原", theme.AMBER_700)
            return

        if not self.query_selected_result:
            self._show_snack_bar("請先選擇一筆資料", theme.AMBER_700)
            return

        ctype = str(self.query_selected_result.get("cache_type", ""))
        key = str(self.query_selected_result.get("key", ""))
        if not ctype or not key:
            self._show_snack_bar("無法取得 cache_type 或 key", theme.RED_400)
            return

        # 載入最新歷史紀錄
        records = self._history_load_recent(ctype, key, limit=1)
        if not records:
            self._show_snack_bar("此 key 目前沒有歷史紀錄", theme.AMBER_700)
            return

        latest = records[0]
        old_dst = str(latest.get("old_dst", ""))

        # 只填入 DST 輸入框，不寫入快取
        self.query_detail_dst.value = old_dst
        self._show_snack_bar(
            "已載入最新歷史紀錄到 DST（尚未寫入快取，請點「套用」儲存）",
            theme.BLUE_400,
        )
        self.page.update()

    def _on_query_search(self, e):
        """

        回傳：None
        """
        if self.ui_busy:
            self._notify("目前忙碌中，暫停搜尋", "warn")
            return

        query = (self.tf_query_input.value or "").strip()
        if not query:
            self._notify("請輸入查詢內容", "warn")
            return

        mode = (self.dd_query_mode.value or "ALL").upper()
        target_type = self.dd_query_type.value or "ALL"
        targets = (
            [target_type]
            if target_type != "ALL"
            else [
                ctype for ctype, _ in self._iter_type_states(self._last_overview_data)
            ]
        )

        out = []
        for ctype in targets:
            if mode in ("KEY", "ALL"):
                r = cache_search_service(ctype, query, mode="key", limit=2000)
                for item in r.get("items", []):
                    k = item.get("key", "")
                    entry = cache_get_entry_service(ctype, k) or {}
                    preview_dst = (
                        str(entry.get("dst", ""))
                        .replace("\\r\\n", "\n")
                        .replace("\\n", "\n")
                    )
                    out.append(
                        {
                            "cache_type": ctype,
                            "key": k,
                            "preview": preview_dst,
                            "shard": self._active_shard_filename(ctype),
                        }
                    )

            if mode in ("DST", "ALL"):
                r = cache_search_service(ctype, query, mode="dst", limit=2000)
                for item in r.get("items", []):
                    out.append(
                        {
                            "cache_type": ctype,
                            "key": item.get("key", ""),
                            "preview": item.get("preview", ""),
                            "shard": self._active_shard_filename(ctype),
                        }
                    )

        seen = set()
        dedup = []
        for row in out:
            k = (row.get("cache_type"), row.get("key"))
            if k in seen:
                continue
            seen.add(k)
            dedup.append(row)

        self.query_results = dedup
        self.query_page = 1
        self.query_selected_result = (
            self.query_results[0] if self.query_results else None
        )
        self.query_search_hint.value = (
            f"搜尋完成：{len(self.query_results)} 筆（左側點選，右側檢視）"
        )
        self.query_search_hint.color = theme.BLUE_700
        self._render_query_results()
        self._render_query_detail()
        self.page.update()

    def _on_query_clear(self, e):
        """

        回傳：None
        """
        self.tf_query_input.value = ""
        self.query_results = []
        self.query_selected_result = None
        self.query_page = 1
        self.query_search_hint.value = "請輸入關鍵字開始搜尋"
        self.query_search_hint.color = theme.GREY_700
        self._render_query_results()
        self._render_query_detail()
        self.page.update()
