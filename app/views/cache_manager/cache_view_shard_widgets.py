"""CacheView 分片頁與主分頁版面的 widgets 組裝。（由 cache_view_shard.py 拆出，#114）"""

import flet as ft

from app.ui import kit

# UI 共用元件：總覽區使用新 UI kit。
from app.ui.design import C
from app.views.cache_manager.cache_state import (
    CacheShardState,
)


class CacheShardWidgetsMixin:
    """CacheView 分片頁與主分頁版面的 widgets 組裝。（由 cache_view_shard.py 拆出，#114）"""

    def _build_shard_widgets(self):
        """建立分片相關的 UI widgets（從 __init__ 提取）"""
        self._init_shard_state()
        self._build_shard_key_widgets()

        self._build_shard_src_widgets()

        self._build_shard_dst_widgets()

        self._build_shard_nav_widgets()

        self._build_shard_key_column()

        self._build_shard_editor_column()

        self._build_shard_workspace_card()

        self._build_query_type_shard_card()

        self.overview_page = self._build_overview_page()
        self.query_entry_page = self._build_query_entry_page()

        self._build_main_tabs()

    def _init_shard_state(self) -> None:
        """分片詳情的狀態與預設值。"""
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

        self.shard_detail_meta = ft.Text("尚未選擇分片", size=11, color=C.MUTED)

    def _build_shard_key_widgets(self) -> None:
        """分片 key 清單：篩選、清單、分頁按鈕。"""
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
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            bgcolor=C.PANEL,
            alignment=ft.alignment.Alignment(-1, -1),
            content=self.shard_detail_key_list,
        )

    def _build_shard_src_widgets(self) -> None:
        """SRC 預覽／原文欄位。"""
        self.shard_src_meta = ft.Text("SRC：請先選擇 key", size=11, color=C.MUTED)
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
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            bgcolor=C.PANEL,
            alignment=ft.alignment.Alignment(-1, -1),
            content=self.shard_src_field,
        )

    def _build_shard_dst_widgets(self) -> None:
        """DST 編輯欄位與操作按鈕。"""
        # C3：DST 編輯
        self.shard_dst_loaded_sig = self._shard_state.dst_loaded_sig
        self.shard_dst_original = self._shard_state.dst_original
        self.shard_dst_meta = ft.Text("DST：請先選擇 key", size=11, color=C.MUTED)
        self.shard_dst_field = ft.TextField(
            value="",
            multiline=True,
            min_lines=6,
            max_lines=12,
            text_align=ft.TextAlign.LEFT,
            text_style=ft.TextStyle(font_family="Consolas", size=12, height=1.45),
        )
        self.btn_shard_dst_apply = ft.Button(
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
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
            bgcolor=C.PANEL,
            alignment=ft.alignment.Alignment(-1, -1),
            content=self.shard_dst_field,
        )

    def _build_shard_nav_widgets(self) -> None:
        """分片導覽欄與返回按鈕。"""
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
        self.shard_workspace_meta = ft.Text("尚未選擇分片", size=11, color=C.MUTED)

    def _build_shard_key_column(self) -> None:
        """分片工作區左側的 key 欄。"""
        self.shard_key_column = ft.Container(
            width=self._dynamic_shard_key_panel_width(),
            padding=10,
            border=ft.Border(right=ft.border.BorderSide(1, C.LINE)),
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
                            ft.Text("|", size=12, color=C.DIM),
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

    def _build_shard_editor_column(self) -> None:
        """分片工作區右側的編輯欄。"""
        self.shard_editor_column = ft.Container(
            expand=True,
            padding=12,
            content=ft.Column(
                [
                    ft.Text("編輯工作區", size=16, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "右側：上 SRC（唯讀）/ 下 DST（可編輯）",
                        size=11,
                        color=C.MUTED,
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

    def _build_shard_workspace_card(self) -> None:
        """分片工作區卡片。"""
        self.shard_workspace_card = ft.Container(
            expand=True,
            visible=False,
            padding=0,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
            bgcolor=C.PANEL,
            content=ft.Column(
                [
                    ft.Container(
                        padding=ft.Padding(left=10, right=10, top=8, bottom=8),
                        border=ft.Border(bottom=ft.border.BorderSide(1, C.LINE)),
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

    def _build_query_type_shard_card(self) -> None:
        """查詢頁的分類／分片卡片。"""
        self.query_type_shard_card = ft.Container(
            expand=True,
            padding=0,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
            bgcolor=C.PANEL,
            content=ft.Column(
                controls=[self.shard_nav_view, self.shard_workspace_card],
                expand=True,
                spacing=0,
            ),
        )

    def _build_main_tabs(self) -> None:
        """主分頁列、分頁內容與整體版面（含浮動歷史視窗）。"""
        main_tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="總覽 / 管理"),
                ft.Tab(label="查詢"),
            ],
            indicator_color=C.EM,
            label_color=C.EM,
            unselected_label_color=C.MUTED,
            divider_color=C.LINE,
        )
        main_tab_view = ft.TabBarView(
            controls=[
                self.overview_page,
                self.query_entry_page,
            ],
            expand=True,
        )
        main_tab_content = ft.Column([main_tab_bar, main_tab_view], expand=True)
        self.main_tabs = ft.Tabs(
            content=main_tab_content,
            length=2,
            selected_index=0,
            expand=True,
            on_change=self._on_tab_change,
        )

        # PR5-7: Modal 入口按鈕（提供替代 Tab 的現代化體驗）
        # 主布局改成 Stack，支援浮動視窗
        self.controls = [
            ft.Stack(
                expand=True,
                controls=[
                    # 主要內容區（原本的 Column）
                    ft.Column(
                        expand=True,
                        controls=[
                            kit.page_header(
                                "快取管理",
                                "翻譯快取分片儲存與全文檢索；命中愈高，API 花費愈低",
                                icon=ft.Icons.STORAGE_OUTLINED,
                                tone="dia",
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
