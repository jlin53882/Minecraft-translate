"""app/views/rules_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import flet as ft

# UI 共用元件：統一按鈕樣式
from app.ui.components import primary_button, secondary_button
import threading
import math
import re
from app.services_impl.config_service import load_replace_rules, save_replace_rules
from app.views.rules.rules_actions import (
    calc_total_pages,
    start_reload_thread,
    start_save_thread,
    translate_regex_error as rules_translate_regex_error,
    validate_rule as rules_validate_rule,
)
from app.views.rules.rules_state import RulesTableState
from app.views.rules.rules_table import create_rule_row as rules_create_row


class RulesView(ft.Column):
    """RulesView 類別。

    用途：封裝與 RulesView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, page: ft.Page):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`__init__`, `_init_controls`, `start`

        回傳：None
        """
        super().__init__(expand=True, spacing=15)
        self.page = page

        # --- 分頁和數據狀態 ---
        self._state = RulesTableState()
        self.page_size = self._state.page_size
        self.current_page = self._state.current_page
        self.all_rules_data = []
        self.total_pages = self._state.total_pages
        self.search_results = None  # 搜尋結果索引列表（或 None）

        # RID 序號生成器 (UI 專用 ID)
        self._rid_seq = self._state.rid_seq

        # --- UI 控制項初始化 (預先建立需參照的控制項) ---
        self._init_controls()

        # --- UI 結構建構 ---
        self.controls = [
            self._build_header(),
            self._build_toolbar(),
            self._build_rules_table_area(),
            self._build_footer(),
        ]

        # 啟動背景載入
        threading.Thread(target=self._initial_load, daemon=True).start()

    def _new_rid(self) -> int:
        """生成一個新的唯一 RID"""
        self._rid_seq += 1
        return self._rid_seq

    def _find_index_by_rid(self, rid: int) -> int:
        """透過 RID 找回資料在 all_rules_data 中的索引"""
        for i, r in enumerate(self.all_rules_data):
            if r.get("_rid") == rid:
                return i
        return -1

    def _sync_page_jump_field(self):
        # 確保欄位顯示跟 current_page 一致
        """處理此函式的工作（細節以程式碼為準）。

        回傳：None
        """
        if hasattr(self, "page_jump_field"):
            self.page_jump_field.value = str(self.current_page)
            # 只有當控制項已加入頁面時才執行 update，避免初始化時 crash
            if self.page_jump_field.page:
                self.page_jump_field.update()

    def on_page_jump_submit(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`strip`, `_render_current_page`

        回傳：None
        """
        raw = (e.control.value or "").strip()
        if not raw:
            self._show_snack_bar("請輸入頁碼", ft.Colors.BLUE_GREY_700)
            self._sync_page_jump_field()
            return

        try:
            page = int(raw)
        except ValueError:
            self._show_snack_bar("頁碼必須是數字", ft.Colors.RED_400)
            self._sync_page_jump_field()
            return

        if page < 1 or page > self.total_pages:
            self._show_snack_bar(f"頁碼範圍：1 ~ {self.total_pages}", ft.Colors.RED_400)
            self._sync_page_jump_field()
            return

        self.current_page = page
        self._render_current_page()
        self._show_snack_bar(f"已跳至第 {page} 頁", ft.Colors.BLUE_700)
        self._sync_page_jump_field()

    def _init_controls(self):
        """初始化所有互動控制項"""
        # 1. 載入指示器
        self.loading_indicator = ft.ProgressRing(
            width=20, height=20, stroke_width=2, visible=False
        )

        # 2. 分頁控制
        self.page_info = ft.Text("頁面 0 / 0", size=14, color=ft.Colors.GREY_700)
        self.total_count_text = ft.Text(
            "共 0 條規則", size=14, color=ft.Colors.GREY_700
        )

        self.prev_button = ft.IconButton(
            ft.Icons.ARROW_BACK,
            on_click=self.prev_page,
            tooltip="上一頁",
            disabled=True,
            icon_color=ft.Colors.GREY_800,
        )
        self.next_button = ft.IconButton(
            ft.Icons.ARROW_FORWARD,
            on_click=self.next_page,
            tooltip="下一頁",
            disabled=True,
            icon_color=ft.Colors.GREY_800,
        )

        self.total_pages_text_label = ft.Text(
            " / 1 頁", size=13, color=ft.Colors.GREY_700
        )

        self.page_jump_field = ft.TextField(
            value=str(self.current_page),
            width=70,
            dense=True,
            text_align=ft.TextAlign.CENTER,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="頁碼",
            on_submit=self.on_page_jump_submit,
        )

        # 3. 搜尋與排序
        self.search_box = ft.TextField(
            label="搜尋規則 (由/至)",
            hint_text="輸入關鍵字...",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self.on_search,
            dense=True,
            expand=True,
            text_size=14,
            border_color=ft.Colors.OUTLINE,
            content_padding=15,
        )

        self.sort_box = ft.Dropdown(
            label="排序方式",
            options=[
                ft.dropdown.Option("from_asc", "依 From 字典序"),
                ft.dropdown.Option("from_len", "依 From 長度"),
            ],
            on_change=self.on_sort_change,
            dense=True,
            width=180,
            text_size=14,
            border_color=ft.Colors.OUTLINE,
            content_padding=10,
        )

        # 4. 表格
        self.rules_table = ft.DataTable(
            column_spacing=20,
            heading_row_height=40,
            data_row_min_height=50,
            columns=[
                ft.DataColumn(
                    ft.Text("#", weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_800),
                    numeric=True,
                ),
                ft.DataColumn(
                    ft.Text(
                        "原文 (簡體)",
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.GREY_800,
                    )
                ),
                ft.DataColumn(
                    ft.Text(
                        "替換為 (繁體)",
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.GREY_800,
                    )
                ),
                ft.DataColumn(
                    ft.Text(
                        "操作", weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_800
                    ),
                    numeric=True,
                ),
            ],
            rows=[],
        )

    # --- UI 建構區塊 ---

    def _build_header(self):
        """頁面標題區"""
        return ft.Container(
            padding=ft.padding.only(left=5, bottom=5),
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.RULE_FOLDER, size=28, color=ft.Colors.BLUE_GREY_800
                    ),
                    ft.Text(
                        "規則管理 (Translation Rules)",
                        theme_style=ft.TextThemeStyle.HEADLINE_MEDIUM,
                        color=ft.Colors.BLUE_GREY_900,
                    ),
                    self.loading_indicator,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_toolbar(self):
        """工具與操作區 (搜尋/排序/按鈕)"""
        return ft.Card(
            elevation=2,
            surface_tint_color=ft.Colors.WHITE,
            content=ft.Container(
                padding=15,
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        # 左側：搜尋與排序
                        ft.Row(
                            expand=True,
                            controls=[
                                self.search_box,
                                self.sort_box,
                            ],
                            spacing=10,
                        ),
                        # 右側：功能按鈕
                        ft.Row(
                            controls=[
                                secondary_button(
                                    "重新載入",
                                    icon=ft.Icons.REFRESH,
                                    tooltip="重新載入 replace_rules.json",
                                    on_click=self.reload_rules_clicked,
                                ),
                                primary_button(
                                    "新增規則",
                                    icon=ft.Icons.ADD,
                                    tooltip="新增一列規則",
                                    on_click=self.add_row_clicked,
                                    bgcolor=ft.Colors.BLUE_600,
                                ),
                                primary_button(
                                    "全部儲存",
                                    icon=ft.Icons.SAVE,
                                    tooltip="儲存全部規則到 replace_rules.json",
                                    on_click=self.save_rules_clicked,
                                    bgcolor=ft.Colors.GREEN_700,
                                ),
                            ],
                            spacing=10,
                        ),
                    ],
                ),
            ),
        )

    def _build_rules_table_area(self):
        """表格內容區"""
        return ft.Card(
            expand=True,
            elevation=2,
            surface_tint_color=ft.Colors.WHITE,
            content=ft.Container(
                padding=10,
                content=ft.ListView(
                    controls=[self.rules_table], expand=True, spacing=0
                ),
            ),
        )

    def _build_footer(self):
        """底部狀態與分頁列"""
        return ft.Container(
            padding=ft.padding.symmetric(horizontal=10, vertical=5),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    self.total_count_text,
                    ft.Row(
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.prev_button,
                            ft.Text("第", size=13, color=ft.Colors.GREY_700),
                            self.page_jump_field,
                            self.total_pages_text_label,
                            self.next_button,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    # 預留一個空的 Container 以達成 Space Between 的平衡，或放置其他資訊
                    ft.Container(width=100),
                ],
            ),
        )

    # --- 邏輯功能 ---
    def on_sort_change(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_render_current_page`

        回傳：None
        """
        mode = e.control.value
        if mode == "from_asc":
            self.all_rules_data.sort(key=lambda r: r.get("from", ""))
            self._show_snack_bar("✅ 已排序：依 From 字典序", ft.Colors.BLUE_700)
        elif mode == "from_len":
            self.all_rules_data.sort(key=lambda r: len(r.get("from", "")))
            self._show_snack_bar("✅ 已排序：依 From 長度", ft.Colors.BLUE_700)

        self.current_page = 1
        self._render_current_page()

    def on_search(self, e: ft.ControlEvent):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_render_current_page`

        回傳：None
        """
        keyword = e.control.value.strip().lower()

        if not keyword:
            # 清除搜尋狀態
            self.search_results = None
            self.current_page = 1
            self._render_current_page()
            self._show_snack_bar("已清除搜尋，顯示全部規則", ft.Colors.BLUE_GREY_700)
            return

        # 找出所有匹配的規則 index
        self.search_results = [
            idx
            for idx, rule in enumerate(self.all_rules_data)
            if keyword in rule.get("from", "").lower()
            or keyword in rule.get("to", "").lower()
        ]

        if not self.search_results:
            self._show_snack_bar("找不到符合的規則", ft.Colors.AMBER_700)
            self._render_current_page()
            return

        # 跳到第一筆搜尋命中的頁面
        first_idx = self.search_results[0]
        self.current_page = first_idx // self.page_size + 1
        self._render_current_page()

    # ---------------------------------------------
    # 規則驗證模組
    # ---------------------------------------------

    def validate_rule(self, src: str, dst: str, all_rules, current_index):
        """
        驗證規則格式正確性，回傳 (is_valid: bool, msg: str)
        """
        if not src.strip():
            return False, "from 欄位不可為空"

        try:
            compiled = re.compile(src)
        except re.error as err:
            return False, self.translate_regex_error(err)

        for idx, rule in enumerate(all_rules):
            if idx != current_index and rule.get("from") == src:
                return False, f"⚠ 與第 {idx + 1} 條規則重複"

        group_refs = re.findall(r"(?:\\+(\d+)|\$(\d+))", dst)
        if group_refs:
            refs = [int(a or b) for a, b in group_refs]
            max_group = compiled.groups
            for ref in refs:
                if ref > max_group:
                    return False, f"引用群組 \\{ref} 超出群組數 {max_group}"

        if re.search(r"\\\\(?!\d)", dst):
            return False, "可能存在無效跳脫（\\\\）"

        return True, ""

    def translate_regex_error(self, err) -> str:
        return rules_translate_regex_error(err)

    # --- 執行緒輔助與載入 ---

    def _run_on_ui_thread(self, func, *args, **kwargs):
        """執行此函式的工作（細節以程式碼為準）。

        回傳：None
        """
        if self.page and self.page.loop:
            self.page.loop.call_soon_threadsafe(func, *args, **kwargs)

    def _show_snack_bar(self, message: str, color: str = ft.Colors.RED_600):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`SnackBar`

        回傳：None
        """
        if not self.page:
            return
        snack = ft.SnackBar(
            ft.Text(message, color=ft.Colors.WHITE), bgcolor=color, open=True
        )
        self.page.overlay.append(snack)
        snack.open = True
        self.page.update()

    def _load_rules_core(self):
        """載入此函式的工作（細節以程式碼為準）。

        - 主要包裝：`load_replace_rules`

        回傳：依函式內 return path。
        """
        return load_replace_rules()

    def _initial_load(self):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_load_rules_core`

        回傳：None
        """
        try:
            rules_data = self._load_rules_core()
            self._run_on_ui_thread(lambda: self._handle_reload_success(rules_data))
        except Exception as err:
            msg = f"初次載入規則失敗: {err}"
            self._run_on_ui_thread(
                lambda msg=msg: self._show_snack_bar(msg, ft.Colors.RED_600)
            )

    # --- 分頁渲染邏輯 ---

    def _render_current_page(self):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`clear`, `enumerate`, `extend`

        回傳：None
        """
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        current_page_data = self.all_rules_data[start:end]

        self.rules_table.rows.clear()
        rows_to_display = []

        for index_on_all_data, rule in enumerate(current_page_data, start=start):
            # 確保有 RID
            if "_rid" not in rule:
                rule["_rid"] = self._new_rid()

            rid = rule["_rid"]

            row = self.create_rule_row(
                rule.get("from", ""),
                rule.get("to", ""),
                rid,
                display_no=index_on_all_data + 1,
            )
            # 搜尋結果高亮
            if self.search_results and index_on_all_data in self.search_results:
                row.color = ft.Colors.YELLOW_50
            else:
                row.color = None
            rows_to_display.append(row)

        self.rules_table.rows.extend(rows_to_display)

        total_rules = len(self.all_rules_data)
        self.total_pages = calc_total_pages(total_rules, self.page_size)

        self.page_info.value = f"頁面 {self.current_page} / {self.total_pages}"
        self.total_pages_text_label.value = f"/ {self.total_pages} 頁"
        self.total_count_text.value = f"共 {total_rules} 條規則"
        self._sync_page_jump_field()

        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == self.total_pages

        self.page.update()

    # --- 互動事件處理 ---

    def on_text_change(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_find_index_by_rid`

        回傳：None
        """
        rid = e.control.data["rid"]
        field = e.control.data["field"]

        index = self._find_index_by_rid(rid)
        if index >= 0:
            self.all_rules_data[index][field] = e.control.value
            self.validate_row_ui(rid)

    def validate_row_ui(self, rid: int):
        """驗證特定列並更新 UI 樣式"""
        row = next((r for r in self.rules_table.rows if r.data == rid), None)
        if row is None:
            return

        from_field = row.cells[1].content
        to_field = row.cells[2].content

        idx = self._find_index_by_rid(rid)
        if idx < 0:
            return

        src = from_field.value
        dst = to_field.value

        is_valid, msg = self.validate_rule(src, dst, self.all_rules_data, idx)

        if is_valid:
            from_field.border_color = None
            to_field.border_color = None
            from_field.error_text = None
            to_field.error_text = None
        else:
            from_field.border_color = ft.Colors.RED_400
            to_field.border_color = ft.Colors.RED_400
            from_field.error_text = msg
            to_field.error_text = msg

        from_field.update()
        to_field.update()

    def create_rule_row(self, from_text, to_text, rid: int, display_no: int):
        return rules_create_row(self, from_text, to_text, rid, display_no)

    # --- 操作邏輯 ---

    def reload_rules_clicked(self, e):
        return start_reload_thread(self)


    def _handle_reload_success(self, rules_data):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_render_current_page`, `_show_snack_bar`

        回傳：None
        """
        self.all_rules_data = rules_data
        # ✅ 給每條 rule 補上穩定 rid
        for r in self.all_rules_data:
            if "_rid" not in r:
                r["_rid"] = self._new_rid()

        self.current_page = 1
        self._render_current_page()
        self.loading_indicator.visible = False
        self._show_snack_bar("規則載入完成！", ft.Colors.GREEN_600)
        self.page.update()

    def _handle_reload_failure(self, err):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_show_snack_bar`

        回傳：None
        """
        self.loading_indicator.visible = False
        self.page.update()
        self._show_snack_bar(f"載入規則時發生錯誤: {err}", ft.Colors.RED_600)

    def prev_page(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：None
        """
        if self.current_page > 1:
            self.current_page -= 1
            self._render_current_page()
        else:
            self._show_snack_bar("已在第一頁", ft.Colors.BLUE_GREY_700)

    def next_page(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        回傳：None
        """
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._render_current_page()
        else:
            self._show_snack_bar("已在最後一頁", ft.Colors.BLUE_GREY_700)

    def save_rules_clicked(self, e):
        # 先驗證
        """保存此函式的工作（細節以程式碼為準）。

        - 主要包裝：`enumerate`, `_show_snack_bar`, `start`

        回傳：None
        """
        for idx, rule in enumerate(self.all_rules_data):
            ok, msg = self.validate_rule(
                rule["from"], rule["to"], self.all_rules_data, idx
            )
            if not ok:
                self._show_snack_bar(
                    f"第 {idx + 1} 條規則錯誤：{msg}", ft.Colors.RED_600
                )
                self.current_page = idx // self.page_size + 1
                self._render_current_page()
                return

        # 移除 _rid 並過濾
        clean_rules = [
            {"from": r.get("from", ""), "to": r.get("to", "")}
            for r in self.all_rules_data
            if r.get("from", "").strip()
        ]
        self._show_snack_bar("✅ 驗證通過，正在儲存規則…", ft.Colors.BLUE_700)
        return start_save_thread(self, clean_rules)


    def add_row_clicked(self, e):
        """加入此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_render_current_page`

        回傳：None
        """
        self.all_rules_data.append({"from": "", "to": "", "_rid": self._new_rid()})
        self.current_page = self.total_pages  # 假設在最後
        # 重新計算總頁數（因為可能剛好換頁）
        total_rules = len(self.all_rules_data)
        self.total_pages = calc_total_pages(total_rules, self.page_size)
        self.current_page = self.total_pages

        self._render_current_page()
        self._show_snack_bar("➕ 已新增一條規則（已跳至最後一頁）", ft.Colors.BLUE_700)

    def delete_row_clicked(self, e):
        """處理此函式的工作（細節以程式碼為準）。

        - 主要包裝：`_find_index_by_rid`

        回傳：None
        """
        rid_to_delete = e.control.data
        idx = self._find_index_by_rid(rid_to_delete)

        if idx >= 0:
            # ✅ 先抓預覽文字
            src = (self.all_rules_data[idx].get("from") or "").strip()
            dst = (self.all_rules_data[idx].get("to") or "").strip()

            del self.all_rules_data[idx]

            if self.current_page > 1 and (
                self.current_page - 1
            ) * self.page_size >= len(self.all_rules_data):
                self.current_page -= 1

            self._render_current_page()

            # ✅ 顯示簡短提示（避免太長）
            src_preview = src[:20] + ("…" if len(src) > 20 else "")
            dst_preview = dst[:20] + ("…" if len(dst) > 20 else "")
            self._show_snack_bar(
                f"🗑 已刪除：{src_preview} → {dst_preview}", ft.Colors.RED_400
            )
