"""CacheView 查詢頁（含歷史視窗）的 widgets 組裝。（由 cache_view_query.py 拆出，#114）"""

import flet as ft

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C
from app.ui.sync_text_field import SyncTextField
from app.views.cache_manager.cache_state import (
    CacheHistoryState,
    CacheQueryState,
)


class CacheQueryWidgetsMixin:
    """CacheView 查詢頁（含歷史視窗）的 widgets 組裝。（由 cache_view_query.py 拆出，#114）"""

    def _build_query_widgets(self):
        """建立查詢相關的 UI widgets（從 __init__ 提取）"""
        self._init_query_state()

        self._build_query_inputs()

        self._build_query_detail_widgets()

        self._build_query_history_widgets()

        self._build_query_history_window()

        self._build_shard_history_widgets()

        self._build_shard_history_window()
        self._build_query_tiles_and_pager()

        self._build_query_search_card()

        self._build_query_type_shard_section()

        self._build_shard_widgets()

    def _init_query_state(self) -> None:
        """查詢狀態與分頁初始值。"""
        # -------------------- Query: Search / Explorer --------------------
        self._query_state = CacheQueryState()
        self.query_results = self._query_state.query_results
        self.query_selected_result = self._query_state.query_selected_result
        self.query_original_dst = self._query_state.query_original_dst
        self.query_page = self._query_state.query_page
        self.query_page_size = self._query_state.query_page_size
        self.query_total_pages = self._query_state.query_total_pages

        # PR5-7: 查詢變更提示
        self._last_query_value = ""

    def _build_query_inputs(self) -> None:
        """查詢輸入列：關鍵字、模式、分類、搜尋／清空按鈕。"""
        self.tf_query_input = SyncTextField(
            label="輸入 key / dst / 關鍵字",
            width=360,
            tooltip="輸入要搜尋的 key、dst 或關鍵字",
            on_submit=self._on_query_search,
            on_change=self._on_query_input_change,
        )
        self.query_change_hint = ft.Text("", size=11, color=C.GOLD, selectable=True)
        # PR5-7: 将提示加入 UI
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
        )
        self.dd_query_mode.on_select = self._on_query_mode_change
        self.dd_query_type = ft.Dropdown(
            width=180,
            value="ALL",
            tooltip="選擇要查詢的分類（例如 lang / patchouli）",
            options=[ft.dropdown.Option("ALL", "全部")],
        )
        self.dd_query_type.on_select = self._on_query_type_change
        self.btn_query_search = ft.Button(
            "搜尋", icon=ft.Icons.SEARCH, on_click=self._on_query_search
        )
        self.btn_query_clear = ft.OutlinedButton(
            "清空", icon=ft.Icons.CLEAR, on_click=self._on_query_clear
        )

    def _build_query_detail_widgets(self) -> None:
        """查詢結果清單與單筆詳情欄位。"""
        self.query_search_hint = ft.Text("請輸入關鍵字開始搜尋", size=11, color=C.MUTED)
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
        self.query_detail_dst = SyncTextField(
            value="",
            multiline=True,
            min_lines=4,
            max_lines=8,
            text_align=ft.TextAlign.LEFT,
        )

    def _build_query_history_widgets(self) -> None:
        """查詢區的歷史紀錄控制項。"""
        # 歷史還原（持久化）- 改成可拖曳浮動視窗（查詢區 + 分類分片共用）
        self._history_state = CacheHistoryState()
        self.history_window_source = self._history_state.history_window_source
        self.query_history_records = self._history_state.query_records
        self.query_history_selected_event = self._history_state.query_selected_event
        self.query_history_selected_text = ft.Text(
            "未選取歷史紀錄", size=11, color=C.MUTED
        )
        self.query_history_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
        self.query_history_preview = SyncTextField(
            read_only=True,
            multiline=True,
            min_lines=3,
            max_lines=5,
            text_align=ft.TextAlign.LEFT,
            value="",
        )
        self.btn_apply_history_old = ft.Button(
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
        self.query_history_key_text = ft.Text("Key: -", size=11, color=C.MUTED)
        self.btn_open_history_drawer = ft.OutlinedButton(
            "歷史紀錄",
            icon=ft.Icons.HISTORY,
            on_click=lambda e: self._on_open_history_window(e, source="query"),
        )

    def _build_query_history_window(self) -> None:
        """查詢區可拖曳的浮動歷史視窗。"""
        # 可拖曳浮動歷史紀錄視窗（查詢區）
        self.query_history_window = ft.Container(
            visible=False,
            left=100,
            top=100,
            width=420,
            height=480,
            bgcolor=C.PANEL,
            border=ft.Border.all(2, C.DIA),
            border_radius=10,
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.3, C.TEXT),
                offset=ft.Offset(2, 2),
            ),
            content=ft.Stack(
                controls=[
                    self._query_history_window_body(),
                    # 右下角調整大小標記
                    ft.Container(
                        right=0,
                        bottom=0,
                        width=20,
                        height=20,
                        content=ft.GestureDetector(
                            mouse_cursor=ft.MouseCursor.RESIZE_DOWN_RIGHT,
                            on_pan_update=self._on_query_history_window_resize,
                            content=ft.Icon(ft.Icons.DRAG_HANDLE, size=16, color=C.DIM),
                        ),
                    ),
                ],
            ),
        )

    def _query_history_window_body(self) -> ft.Control:
        """查詢區歷史視窗的標題列與內容。"""
        return ft.Column(
            spacing=0,
            controls=[
                # 標題列（可拖曳）
                ft.Container(
                    bgcolor=C.DIA_BG,
                    padding=10,
                    border_radius=ft.BorderRadius(10, 10, 0, 0),
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
                                            color=C.DIA,
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
                                border=ft.Border.all(1, C.LINE),
                                border_radius=8,
                                bgcolor=C.PANEL,
                                content=self.query_history_list,
                            ),
                            ft.Text("預覽", weight=ft.FontWeight.BOLD, size=12),
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
        )

    def _build_shard_history_widgets(self) -> None:
        """分片區的歷史紀錄控制項。"""
        # C3 歷史紀錄功能（與查詢區相同）- 改成可拖曳浮動視窗
        self.shard_history_records: list[dict] = []
        self.shard_history_selected_event: dict | None = None
        self.shard_history_selected_text = ft.Text(
            "未選取歷史紀錄", size=11, color=C.MUTED
        )
        self.shard_history_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
        self.shard_history_preview = SyncTextField(
            read_only=True,
            multiline=True,
            min_lines=3,
            max_lines=5,
            text_align=ft.TextAlign.LEFT,
            value="",
        )
        self.btn_shard_apply_history_old = ft.Button(
            "套用選取舊值",
            icon=ft.Icons.HISTORY,
            on_click=self._on_shard_apply_selected_history,
        )
        self.shard_history_key_text = ft.Text("Key: -", size=11, color=C.MUTED)
        self.btn_open_shard_history_drawer = ft.OutlinedButton(
            "歷史紀錄",
            icon=ft.Icons.HISTORY,
            on_click=lambda e: self._on_open_history_window(e, source="shard"),
        )

    def _build_shard_history_window(self) -> None:
        """分片區可拖曳的浮動歷史視窗。"""
        # 可拖曳浮動歷史紀錄視窗（分片區）
        self.shard_history_window = ft.Container(
            visible=False,
            left=150,
            top=150,
            width=420,
            height=480,
            bgcolor=C.PANEL,
            border=ft.Border.all(2, C.DIA),
            border_radius=10,
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.3, C.TEXT),
                offset=ft.Offset(2, 2),
            ),
            content=ft.Stack(
                controls=[
                    self._shard_history_window_body(),
                    # 右下角調整大小標記
                    ft.Container(
                        right=0,
                        bottom=0,
                        width=20,
                        height=20,
                        content=ft.GestureDetector(
                            mouse_cursor=ft.MouseCursor.RESIZE_DOWN_RIGHT,
                            on_pan_update=self._on_shard_history_window_resize,
                            content=ft.Icon(ft.Icons.DRAG_HANDLE, size=16, color=C.DIM),
                        ),
                    ),
                ],
            ),
        )

    def _shard_history_window_body(self) -> ft.Control:
        """分片區歷史視窗的標題列與內容。"""
        return ft.Column(
            spacing=0,
            controls=[
                ft.Container(
                    bgcolor=C.DIA_BG,
                    padding=10,
                    border_radius=ft.BorderRadius(10, 10, 0, 0),
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
                                            color=C.DIA,
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
                                border=ft.Border.all(1, C.LINE),
                                border_radius=8,
                                bgcolor=C.PANEL,
                                content=self.shard_history_list,
                            ),
                            ft.Text("預覽", weight=ft.FontWeight.BOLD, size=12),
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
        )

    def _build_query_tiles_and_pager(self) -> None:
        """SRC／DST 區塊、套用／還原按鈕與分頁控制。"""
        self.query_src_tile = ft.ExpansionTile(
            title=ft.Text("SRC（可展開）", weight=ft.FontWeight.BOLD),
            controls=[
                ft.Container(
                    alignment=ft.alignment.Alignment(-1, -1),
                    padding=8,
                    border=ft.Border.all(1, C.LINE),
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
                    alignment=ft.alignment.Alignment(-1, -1),
                    padding=8,
                    border=ft.Border.all(1, C.LINE),
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

        self.btn_apply_dst = ft.Button(
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
        self.tf_page_jump = SyncTextField(
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
        )
        self.dd_page_size.on_select = self._on_page_size_change
        self.query_page_info = ft.Text("第 1 頁 / 共 1 頁")
        self.query_total_info = ft.Text("共 0 筆")

    def _build_query_search_card(self) -> None:
        """查詢頁主卡片。"""
        self.query_search_card = ft.Container(
            expand=True,
            padding=14,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
            bgcolor=C.PANEL,
            alignment=ft.alignment.Alignment(-1, -1),
            content=ft.Column(
                [
                    ft.Text("查詢區塊（Explorer）", size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "關鍵字輸入（可輸入 key / dst / 關鍵字）",
                        size=11,
                        color=C.MUTED,
                    ),
                    ft.Row(
                        [
                            self.tf_query_input,
                            self.btn_query_search,
                            self.btn_query_clear,
                        ],
                        wrap=True,
                    ),
                    # PR5-7: 查询变更提示
                    self.query_change_hint,
                    ft.Text("查詢模式與分類選擇", size=11, color=C.MUTED),
                    ft.Row([self.dd_query_mode, self.dd_query_type], wrap=True),
                    self.query_search_hint,
                    self._query_results_split_view(),
                    self._query_pager_bar(),
                ],
                expand=True,
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    def _query_results_split_view(self) -> ft.Control:
        """左側結果列表、右側內容檢視。"""
        return ft.Container(
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
                                    border=ft.Border.all(1, C.LINE),
                                    border_radius=8,
                                    bgcolor=C.PANEL,
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
                                    border=ft.Border.all(1, C.LINE),
                                    border_radius=8,
                                    bgcolor=C.PANEL,
                                    alignment=ft.alignment.Alignment(-1, -1),
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
        )

    def _query_pager_bar(self) -> ft.Control:
        """查詢結果的分頁與套用／還原按鈕列。"""
        return ft.Container(
            padding=ft.Padding(top=4),
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
                    ft.Text("|", size=12, color=C.DIM),
                    self.query_total_info,
                    ft.Container(width=14),
                    self.btn_apply_dst,
                    self.btn_revert_dst,
                    self.btn_restore_latest_query,
                ],
                wrap=True,
                spacing=6,
            ),
        )

    def _build_query_type_shard_section(self) -> None:
        """分類／分片獨立分頁區塊。"""
        # 分離：分類/分片獨立分頁
        self.query_type_shard_hint = ft.Text(
            "分類 / 分片清單（獨立分頁）", size=11, color=C.MUTED
        )
        self.query_type_shard_col = ft.Column(
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )
        self.query_type_shard_list_container = ft.Container(
            expand=True,
            padding=8,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            bgcolor=C.PANEL,
            alignment=ft.alignment.Alignment(-1, -1),
            content=self.query_type_shard_col,
        )

    def _build_query_entry_page(self):
        """建立查詢頁面的 UI"""
        query_sub_tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="查詢區"),
                ft.Tab(label="分類/分片"),
            ]
        )
        query_sub_tab_view = ft.TabBarView(
            controls=[
                self.query_search_card,
                self.query_type_shard_card,
            ],
            expand=True,
        )
        query_sub_tab_content = ft.Column(
            [query_sub_tab_bar, query_sub_tab_view], expand=True
        )
        self.query_sub_tabs = ft.Tabs(
            content=query_sub_tab_content,
            length=2,
            selected_index=0,
            animation_duration=200,
            expand=True,
            on_change=self._on_query_sub_tab_change,
        )

        return ft.Container(
            expand=True,
            bgcolor=C.PANEL,
            padding=8,
            alignment=ft.alignment.Alignment(-1, -1),
            content=self.query_sub_tabs,
        )
