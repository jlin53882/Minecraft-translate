"""app/views/cache_query_panel.py 模組。

用途：封裝快取查詢（Explorer）功能的 UI 元件。
維護注意：本模組依賴 cache_state.CacheQueryState 與 cache_services。
"""

import flet as ft
from app.ui import theme
from translation_tool.utils.log_unit import log_info
from app.views.cache_manager.cache_state import CacheQueryState
from app.services_impl.cache.cache_services import (
    cache_get_entry_service,
    cache_search_service,
    cache_update_dst_service,
    cache_save_all_service,
)
from app.views.cache_manager.cache_history_store import (
    history_now_ts,
    history_append_event,
)


class CacheQueryPanel(ft.Container):
    """CacheQueryPanel 元件。

    用途：封裝快取查詢區的 UI 與邏輯。
    維護注意：此元件處理關鍵字搜尋與結果展示。
    """

    def __init__(self, page: ft.Page, state: CacheQueryState, last_overview_data: dict):
        """初始化 CacheQueryPanel。

        參數：
            page: Flet Page 物件
            state: CacheQueryState 實例
            last_overview_data: 快取總覽資料（用於取得分類列表）
        """
        # 先建立 UI 元件
        self._build_components()

        # 先呼叫父類初始化
        super().__init__(content=self._build_content())

        # 再設定實例屬性
        self._page = page
        self.state = state
        self.last_overview_data = last_overview_data

    def _build_components(self):
        """建立內部元件"""
        # 搜尋輸入
        self.tf_query_input = ft.TextField(
            label="輸入 key / dst / 關鍵字",
            width=360,
            tooltip="輸入要搜尋的 key、dst 或關鍵字",
            on_submit=self._on_query_search,
        )

        # 搜尋模式
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

        # 分類選擇
        self.dd_query_type = ft.Dropdown(
            width=180,
            value="ALL",
            tooltip="選擇要查詢的分類",
            options=[ft.dropdown.Option("ALL", "全部")],
        )

        # 按鈕
        self.btn_query_search = ft.Button(
            "搜尋", icon=ft.Icons.SEARCH, on_click=self._on_query_search
        )
        self.btn_query_clear = ft.OutlinedButton(
            "清空", icon=ft.Icons.CLEAR, on_click=self._on_query_clear
        )

        # 提示文字
        self.query_search_hint = ft.Text(
            "請輸入關鍵字開始搜尋", size=11, color=theme.GREY_700
        )

        # 結果列表
        self.query_result_list = ft.ListView(
            expand=True,
            spacing=6,
            auto_scroll=False,
        )

        # 詳情面板
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

        # 分頁控制
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
        )
        self.dd_page_size.on_change = self._on_page_size_change
        self.query_page_info = ft.Text("第 1 頁 / 共 1 頁")
        self.query_total_info = ft.Text("共 0 筆")

        # 套用/還原按鈕
        self.btn_apply_dst = ft.Button(
            "套用", icon=ft.Icons.SAVE, on_click=self._on_apply_dst
        )
        self.btn_revert_dst = ft.OutlinedButton(
            "還原",
            icon=ft.Icons.UNDO,
            on_click=self._on_revert_dst,
            tooltip="還原到原始值",
        )

    def _build_content(self) -> ft.Column:
        """建立 UI 內容"""
        return ft.Column(
            [
                ft.Text("查詢區塊（Explorer）", size=16, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "關鍵字輸入（可輸入 key / dst / 關鍵字）",
                    size=11,
                    color=theme.GREY_700,
                ),
                ft.Row(
                    [self.tf_query_input, self.btn_query_search, self.btn_query_clear],
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
                                            border=ft.Border.all(1, theme.OUTLINE_VARIANT),
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
                                        ft.Text(
                                            "內容檢視（右）",
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                        ft.Container(
                                            expand=True,
                                            padding=8,
                                            border=ft.Border.all(1, theme.OUTLINE_VARIANT),
                                            border_radius=8,
                                            bgcolor=theme.WHITE,
                                            alignment=ft.alignment.Alignment(-1,-1),
                                            content=ft.Column(
                                                [
                                                    self.query_detail_key,
                                                    self.query_detail_type,
                                                    self.query_detail_shard,
                                                    self.query_detail_status,
                                                    self.query_detail_src,
                                                    self.query_detail_dst,
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
                            ft.Container(width=10),
                            self.query_total_info,
                            ft.Container(width=14),
                            self.btn_apply_dst,
                            self.btn_revert_dst,
                        ],
                        wrap=True,
                        spacing=6,
                    ),
                ),
            ],
            expand=True,
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

    def _iter_type_states(self, data: dict):
        """迭代所有快取類型與其狀態"""
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

    def _type_dirty_text(self, cache_type: str) -> str:
        """取得指定類型的髒污狀態文字"""
        for ctype, st in self._iter_type_states(self.last_overview_data):
            if ctype == cache_type:
                return "dirty" if bool(st.get("is_dirty", False)) else "clean"
        return "-"

    def _active_shard_filename(self, cache_type: str) -> str:
        """取得指定類型的活躍分片檔案名"""
        for ctype, st in self._iter_type_states(self.last_overview_data):
            if ctype == cache_type:
                sid = st.get("active_shard_id")
                if sid:
                    return f"{cache_type}_{sid}.json"
        return "-"

    def refresh_type_options(self):
        """更新分類下拉選單"""
        types = sorted(
            [ctype for ctype, _ in self._iter_type_states(self.last_overview_data)]
        )
        self.dd_query_type.options = [ft.dropdown.Option("ALL", "全部")]
        self.dd_query_type.options.extend([ft.dropdown.Option(t, t) for t in types])
        if not self.dd_query_type.value:
            self.dd_query_type.value = "ALL"

    # ==================== 搜尋相關 ====================
    def _on_query_search(self, e):
        """執行關鍵字搜尋"""
        query = (self.tf_query_input.value or "").strip()
        if not query:
            self._show_snack_bar("請輸入查詢內容", theme.AMBER_700)
            return

        mode = (self.dd_query_mode.value or "ALL").upper()
        target_type = self.dd_query_type.value or "ALL"
        targets = (
            [target_type]
            if target_type != "ALL"
            else [
                ctype for ctype, _ in self._iter_type_states(self.last_overview_data)
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

        # 去重
        seen = set()
        dedup = []
        for row in out:
            k = (row.get("cache_type"), row.get("key"))
            if k in seen:
                continue
            seen.add(k)
            dedup.append(row)

        self.state.query_results = dedup
        self.state.query_page = 1
        self.state.query_selected_result = (
            self.state.query_results[0] if self.state.query_results else None
        )
        self.query_search_hint.value = (
            f"搜尋完成：{len(self.state.query_results)} 筆"
        )
        self.query_search_hint.color = theme.BLUE_700
        self._render_query_results()
        self._render_query_detail()
        self._page.update()

    def _on_query_clear(self, e):
        """清除搜尋條件與結果"""
        self.tf_query_input.value = ""
        self.state.query_results = []
        self.state.query_selected_result = None
        self.state.query_page = 1
        self.query_search_hint.value = "請輸入關鍵字開始搜尋"
        self.query_search_hint.color = theme.GREY_700
        self._render_query_results()
        self._render_query_detail()
        self._page.update()

    # ==================== 分頁相關 ====================
    def _set_query_page(self, page: int):
        """設定查詢結果頁碼"""
        total = len(self.state.query_results)
        self.state.query_total_pages = max(
            1, (total + self.state.query_page_size - 1) // self.state.query_page_size
        )
        self.state.query_page = max(1, min(page, self.state.query_total_pages))

    def _render_query_results(self):
        """渲染查詢結果列表"""
        self._set_query_page(self.state.query_page)
        self.query_result_list.controls.clear()

        total = len(self.state.query_results)
        start = (self.state.query_page - 1) * self.state.query_page_size
        end = start + self.state.query_page_size
        page_rows = self.state.query_results[start:end]

        if not page_rows:
            self.query_result_list.controls.append(
                ft.Text("無搜尋結果", size=12, color=theme.GREY_600)
            )
        else:
            for row in page_rows:
                key = str(row.get("key", ""))
                cache_type = str(row.get("cache_type", "-"))
                shard = str(row.get("shard", "-"))
                preview = str(row.get("preview", ""))
                selected = (
                    self.state.query_selected_result is not None
                    and self.state.query_selected_result.get("cache_type") == cache_type
                    and self.state.query_selected_result.get("key") == key
                )

                self.query_result_list.controls.append(
                    ft.Container(
                        padding=8,
                        border=ft.Border.all(
                            1,
                            theme.BLUE_200 if selected else theme.OUTLINE_VARIANT,
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
                )

        self.tf_page_jump.value = str(self.state.query_page)
        self.query_page_info.value = (
            f"第 {self.state.query_page} 頁 / 共 {self.state.query_total_pages} 頁"
        )
        self.query_total_info.value = f"共 {total} 筆"

    def _on_select_result(self, row: dict):
        """選擇查詢結果"""
        self.state.query_selected_result = row
        self._render_query_results()
        self._render_query_detail()
        self._page.update()

    def _render_query_detail(self):
        """渲染查詢詳情面板"""
        row = self.state.query_selected_result
        if not row:
            self.query_detail_key.value = "Key: -"
            self.query_detail_type.value = "類型: -"
            self.query_detail_shard.value = "Shard: -"
            self.query_detail_status.value = "Cache 狀態: -"
            self.query_detail_src.value = "-"
            self.query_detail_dst.value = ""
            self.state.query_original_dst = ""
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
        self.state.query_original_dst = dst

    def _on_page_first(self, e):
        """跳到第一頁"""
        self.state.query_page = 1
        self._render_query_results()
        self._page.update()

    def _on_page_prev(self, e):
        """上一頁"""
        self.state.query_page -= 1
        self._render_query_results()
        self._page.update()

    def _on_page_next(self, e):
        """下一頁"""
        self.state.query_page += 1
        self._render_query_results()
        self._page.update()

    def _on_page_last(self, e):
        """跳到最後一頁"""
        self.state.query_page = self.state.query_total_pages
        self._render_query_results()
        self._page.update()

    def _on_page_jump(self, e):
        """跳轉到指定頁"""
        try:
            p = int((self.tf_page_jump.value or "1").strip())
        except Exception:
            p = 1
        self.state.query_page = p
        self._render_query_results()
        self._page.update()

    def _on_page_size_change(self, e):
        """變更每頁數量"""
        try:
            self.state.query_page_size = int(self.dd_page_size.value or "50")
        except Exception:
            self.state.query_page_size = 50
        self.state.query_page = 1
        self._render_query_results()
        self._page.update()

    # ==================== 編輯相關 ====================
    def _on_apply_dst(self, e):
        """套用目標翻譯"""
        if not self.state.query_selected_result:
            self._show_snack_bar("請先選擇一筆資料", theme.AMBER_700)
            return

        ctype = str(self.state.query_selected_result.get("cache_type", ""))
        key = str(self.state.query_selected_result.get("key", ""))
        shard = str(self.state.query_selected_result.get("shard", "-"))
        new_dst = str(self.query_detail_dst.value or "")
        old_dst = str(self.state.query_original_dst or "")

        try:
            done = cache_update_dst_service(ctype, key, new_dst)
            if not done:
                self._show_snack_bar("套用失敗：找不到目標 key", theme.RED_400)
                return

            cache_save_all_service(write_new_shard=False, only_types=[ctype])

            # 記錄歷史
            history_event = {
                "ts": history_now_ts(),
                "cache_type": ctype,
                "key": key,
                "shard": shard,
                "old_dst": old_dst,
                "new_dst": new_dst,
                "action": "apply",
                "actor": "cache_query_panel",
            }
            self._history_append_event(ctype, history_event)

            self.state.query_original_dst = new_dst
            for row in self.state.query_results:
                if row.get("cache_type") == ctype and row.get("key") == key:
                    row["preview"] = new_dst
                    break

            self._render_query_results()
            self._render_query_detail()
            self._show_snack_bar("已套用並寫入快取", theme.BLUE_400)
            self._page.update()
        except Exception as ex:
            self._show_snack_bar(f"套用失敗：{ex}", theme.RED_400)

    def _on_revert_dst(self, e):
        """還原 DST 到原始值"""
        if not self.state.query_selected_result:
            self._show_snack_bar("請先選擇一筆資料", theme.AMBER_700)
            return

        self.query_detail_dst.value = str(self.state.query_original_dst or "")
        self._show_snack_bar("已還原到原始值", theme.BLUE_400)
        self._page.update()

    def _history_append_event(self, cache_type: str, event: dict):
        """新增歷史事件"""
        root = str((self.last_overview_data or {}).get("cache_root", "") or "").strip()
        history_append_event(root, cache_type, event)

    def _show_snack_bar(self, message: str, color: str):
        """顯示 SnackBar"""
        log_info(f"[UI] SnackBar: {message}")
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self._page.overlay.append(snack)
        snack.open = True
        self._page.update()

    @property
    def page(self):
        return self._page
