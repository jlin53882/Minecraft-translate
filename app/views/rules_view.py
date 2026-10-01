"""app/views/rules_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import asyncio
import re
import threading

import flet as ft

from app.services_impl.config_service import load_replace_rules
from app.ui import design, kit, theme
from app.ui.debounce import Debouncer
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.rules.rules_actions import (
    calc_total_pages,
    start_reload_thread,
    start_save_thread,
)
from app.views.rules.rules_actions import (
    translate_regex_error as rules_translate_regex_error,
)
from app.views.rules.rules_state import RulesTableState
from app.views.rules.rules_table import create_rule_row as rules_create_row
from translation_tool.utils.text_processor import apply_replace_rules


class RulesView(ft.Column):
    """RulesView 類別。

    用途：封裝與 RulesView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    def __init__(self, page: ft.Page):
        """初始化 RulesView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=16)
        self._page = page

        # --- 分頁和數據狀態 ---
        self._state = RulesTableState()
        self.page_size = self._state.page_size
        self.current_page = self._state.current_page
        self.all_rules_data = []
        self.total_pages = self._state.total_pages

        # --- 搜尋狀態（進階版）---
        self.search_results = None  # 搜尋結果（符合的 rule 物件列表）
        self.search_keyword = ""  # 當前搜尋關鍵字
        self.search_case_sensitive = False  # 大小寫區分
        self.search_regex = False  # Regex 模式
        self.search_current_idx = 0  # 當前導航位置

        # 背景執行緒 → UI 的暫存佇列（掛載前完成的載入）
        self._ui_lock = threading.Lock()
        self._pending_ui_calls: list = []
        # debounce 在 event loop 上執行（threading.Timer 會在背景執行緒改控制項）
        self._search_debouncer = Debouncer(lambda: self.page, 0.3)

        # RID 序號生成器 (UI 專用 ID)
        self._rid_seq = self._state.rid_seq

        # --- UI 控制項初始化 (預先建立需參照的控制項) ---
        self._init_controls()

        # --- UI 結構建構 ---
        self.controls = [
            self._build_header(),
            self._build_toolbar(),
            ft.Row(
                [self._build_rules_table_area(), self._build_test_panel()],
                spacing=16,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

        # 啟動背景載入
        self._initial_load()

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
        """同步分頁跳轉欄位。"""
        if hasattr(self, "page_jump_field"):
            self.page_jump_field.value = str(self.current_page)
            try:
                if self.page_jump_field.page:
                    self.page_jump_field.update()
            except RuntimeError:
                pass

    def on_page_jump_submit(self, e):
        """驗證並執行頁碼跳轉"""
        raw = (e.control.value or "").strip()
        if not raw:
            show_snack(self.page, "請輸入頁碼", theme.PRIMARY, text_color=theme.WHITE)
            self._sync_page_jump_field()
            return

        try:
            page = int(raw)
        except ValueError:
            show_snack(self.page, "頁碼必須是數字", theme.ERROR, text_color=theme.WHITE)
            self._sync_page_jump_field()
            return

        if page < 1 or page > self.total_pages:
            show_snack(
                self.page,
                f"頁碼範圍：1 ~ {self.total_pages}",
                theme.ERROR,
                text_color=theme.WHITE,
            )
            self._sync_page_jump_field()
            return

        self.current_page = page
        self._render_current_page()
        show_snack(
            self.page, f"已跳至第 {page} 頁", theme.PRIMARY, text_color=theme.WHITE
        )
        self._sync_page_jump_field()

    def _init_controls(self):
        """初始化所有互動控制項"""
        # 1. 載入指示器
        self.loading_indicator = ft.ProgressRing(
            width=20, height=20, stroke_width=2, visible=False, color=C.EM
        )

        # 2. 分頁控制
        self.page_info = ft.Text("頁面 0 / 0", size=13, color=C.MUTED)
        self.total_count_text = ft.Text("共 0 條規則", size=13, color=C.MUTED)

        nav_style = ft.ButtonStyle(
            bgcolor=C.PANEL2,
            side=ft.BorderSide(1, C.LINE2),
            shape=ft.RoundedRectangleBorder(radius=design.RADIUS_CONTROL),
        )
        self.prev_button = ft.IconButton(
            ft.Icons.ARROW_BACK,
            on_click=self.prev_page,
            tooltip="上一頁",
            disabled=True,
            icon_color=C.TEXT,
            style=nav_style,
        )
        self.next_button = ft.IconButton(
            ft.Icons.ARROW_FORWARD,
            on_click=self.next_page,
            tooltip="下一頁",
            disabled=True,
            icon_color=C.TEXT,
            style=nav_style,
        )

        self.total_pages_text_label = ft.Text(" / 1 頁", size=13, color=C.MUTED)

        self.page_jump_field = ft.TextField(
            value=str(self.current_page),
            width=70,
            dense=True,
            text_align=ft.TextAlign.CENTER,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="頁碼",
            on_submit=self.on_page_jump_submit,
            filled=True,
            bgcolor=C.PANEL2,
            border_color=C.LINE2,
            focused_border_color=C.EM,
            border_radius=design.RADIUS_CONTROL,
            text_size=13,
            content_padding=ft.Padding.symmetric(horizontal=6, vertical=8),
        )

        # 3. 搜尋與排序
        self.search_box = kit.text_field(
            hint="搜尋 from / to / 備註 / 分類　（/正則/ 以斜線包起來）",
            icon=ft.Icons.SEARCH,
            on_change=self.on_search,
            expand=True,
        )

        self.sort_box = ft.Dropdown(
            label="排序方式",
            options=[
                ft.dropdown.Option("from_asc", "依 From 字典序"),
                ft.dropdown.Option("from_len", "依 From 長度"),
            ],
            dense=True,
            width=190,
            text_size=13,
            filled=True,
            bgcolor=C.PANEL2,
            border_color=C.LINE2,
            focused_border_color=C.EM,
            border_radius=design.RADIUS_CONTROL,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        self.sort_box.on_select = self.on_sort_change

        # 4. 表格
        def heading(text: str, **kwargs):
            return ft.DataColumn(
                ft.Text(text, weight=ft.FontWeight.W_600, size=12, color=C.MUTED),
                **kwargs,
            )

        self.rules_table = ft.DataTable(
            column_spacing=20,
            heading_row_height=40,
            data_row_min_height=50,
            heading_row_color=C.PANEL2,
            divider_thickness=1,
            horizontal_lines=ft.BorderSide(1, C.LINE),
            columns=[
                heading("#", numeric=True),
                heading("原文 (簡體)"),
                heading("替換為 (繁體)"),
                heading("操作", numeric=True),
            ],
            rows=[],
        )

        # 5. 即時測試：用目前（尚未儲存）的規則試跑一段文字
        self.test_input = kit.text_field(
            hint="貼上一段簡體文字，立即看到套用結果",
            multiline=True,
            min_lines=3,
            max_lines=5,
            on_change=self.on_test_change,
        )
        self.test_result = ft.Text(
            "", size=13.5, selectable=True, color=C.TEXT, no_wrap=False
        )
        self.test_info = ft.Text("", size=12, color=C.DIM)

    # --- UI 建構區塊 ---

    def _build_header(self):
        """頁面標題區"""
        return kit.page_header(
            "替換規則",
            "機器翻譯後自動套用的用語統一規則，支援純文字與正規表達式",
            icon=ft.Icons.FIND_REPLACE,
            tone="ench",
            actions=[self.loading_indicator],
        )

    def _build_toolbar(self):
        """工具與操作區 (搜尋/排序/按鈕)"""
        return ft.Row(
            [
                self.search_box,
                self.sort_box,
                kit.button(
                    "重新載入",
                    "secondary",
                    icon=ft.Icons.REFRESH,
                    tooltip="重新載入 replace_rules.json",
                    on_click=self.reload_rules_clicked,
                ),
                kit.button(
                    "新增規則",
                    "gold",
                    icon=ft.Icons.ADD,
                    tooltip="新增一列規則",
                    on_click=self.add_row_clicked,
                ),
                kit.button(
                    "全部儲存",
                    "primary",
                    icon=ft.Icons.SAVE_OUTLINED,
                    tooltip="儲存全部規則到 replace_rules.json",
                    on_click=self.save_rules_clicked,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_rules_table_area(self):
        """表格內容區"""
        return ft.Container(
            expand=True,
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    ft.ListView(controls=[self.rules_table], expand=True, spacing=0),
                    self._build_footer(),
                ],
                spacing=0,
                expand=True,
            ),
        )

    def _build_footer(self):
        """底部狀態與分頁列"""
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border=ft.Border.only(top=ft.BorderSide(1, C.LINE)),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self.total_count_text,
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.prev_button,
                            ft.Text("第", size=13, color=C.MUTED),
                            self.page_jump_field,
                            self.total_pages_text_label,
                            self.next_button,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    # 空白佔位，讓分頁列維持置中
                    ft.Container(width=100),
                ],
            ),
        )

    def _build_test_panel(self):
        """右側：即時測試 + 小提示"""
        return ft.Column(
            [
                kit.section_card(
                    "即時測試",
                    ft.Column(
                        [
                            kit.section_label("輸入文字"),
                            self.test_input,
                            kit.section_label("套用結果"),
                            ft.Container(
                                content=self.test_result,
                                padding=12,
                                bgcolor=C.EM_BG,
                                border=ft.Border.all(1, C.EM_LINE),
                                border_radius=design.RADIUS_CONTROL,
                                height=110,
                            ),
                            self.test_info,
                        ],
                        spacing=8,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    icon=ft.Icons.EDIT_NOTE,
                    tone="ench",
                ),
                kit.section_card(
                    "使用說明",
                    ft.Text(
                        "規則由上而下依序套用：固定文字依長詞優先，正規表達式最後套用。"
                        "from 欄位可使用 \\d、(…) 等語法，to 欄位用 $1 或 \\1 引用群組。",
                        size=12.5,
                        color=C.MUTED,
                    ),
                    icon=ft.Icons.INFO_OUTLINE,
                    tone="dia",
                ),
            ],
            spacing=16,
            width=340,
        )

    # --- 即時測試 ---
    def on_test_change(self, e=None):
        """輸入變更時，用目前（尚未儲存）的規則套用到測試文字。"""
        text = self.test_input.value or ""
        if not text.strip():
            self.test_result.value = ""
            self.test_info.value = ""
        else:
            rules = [
                {"from": r.get("from", ""), "to": r.get("to", "")}
                for r in self.all_rules_data
                if (r.get("from") or "").strip()
                and self._is_compilable(r.get("from", ""))
            ]
            try:
                result = apply_replace_rules(text, rules)
            except Exception as err:  # noqa: BLE001 - 規則有問題時顯示原因，不讓頁面出錯
                result = text
                self.test_info.value = f"套用失敗：{err}"
                self.test_info.color = C.RED
            else:
                self.test_result.value = result
                changed = result != text
                self.test_info.value = (
                    f"已套用 {len(rules)} 條規則，文字有變更"
                    if changed
                    else f"已套用 {len(rules)} 條規則，沒有符合的規則"
                )
                self.test_info.color = C.EM if changed else C.DIM
            self.test_result.value = result
        try:
            self.test_result.update()
            self.test_info.update()
        except (AssertionError, RuntimeError):
            pass

    @staticmethod
    def _is_compilable(pattern: str) -> bool:
        try:
            re.compile(pattern)
        except re.error:
            return False
        return True

    # --- 邏輯功能 ---
    def on_sort_change(self, e):
        """根據選擇的排序模式重新排序規則資料"""
        mode = e.control.value
        if mode == "from_asc":
            self.all_rules_data.sort(key=lambda r: r.get("from", ""))
            show_snack(
                self.page,
                "✅ 已排序：依 From 字典序",
                theme.PRIMARY,
                text_color=theme.WHITE,
            )
        elif mode == "from_len":
            self.all_rules_data.sort(key=lambda r: len(r.get("from", "")))
            show_snack(
                self.page,
                "✅ 已排序：依 From 長度",
                theme.PRIMARY,
                text_color=theme.WHITE,
            )

        self.current_page = 1
        self._render_current_page()

    def on_search(self, e: ft.ControlEvent):
        """根據關鍵字搜尋規則並更新顯示（進階版：過濾顯示 + 多欄位 + Regex）"""
        # Debounce: 延遲搜尋
        keyword = e.control.value

        if not (keyword or "").strip():
            # 清除搜尋，回覆顯示全部（並取消尚未執行的搜尋）
            self._search_debouncer.cancel()
            self._do_search("")
            return

        # 300ms 內的連續輸入只搜尋最後一次
        self._search_debouncer.call(self._do_search, keyword)

    def _do_search(self, keyword: str):
        """執行實際搜尋（Debounce 觸發）"""
        keyword = keyword.strip()

        if not keyword:
            # 清除搜尋
            self.search_results = None
            self.search_keyword = ""
            self.search_case_sensitive = False
            self.search_regex = False
            self.search_current_idx = 0
            self.current_page = 1
            self._render_current_page()
            show_snack(
                self.page,
                "已清除搜尋，顯示全部規則",
                theme.PRIMARY,
                text_color=theme.WHITE,
            )
            return

        # 檢測 Regex 模式（以 / 開頭和結尾）
        use_regex = False
        search_keyword = keyword

        if keyword.startswith("/") and keyword.endswith("/") and len(keyword) > 2:
            use_regex = True
            search_keyword = keyword[1:-1]  # 移除 /

        self.search_keyword = search_keyword
        self.search_regex = use_regex

        # 執行搜尋
        matched_rules = []

        for rule in self.all_rules_data:
            if self._rule_matches(rule, search_keyword, use_regex):
                matched_rules.append(rule)

        self.search_results = matched_rules
        self.search_current_idx = 0

        if not self.search_results:
            show_snack(
                self.page, "找不到符合的規則", theme.WARNING, text_color=theme.WHITE
            )
            self._render_current_page()
            return

        # 顯示結果數量
        count = len(self.search_results)
        mode_text = "（正則）" if use_regex else ""
        show_snack(
            self.page,
            f"找到 {count} 筆符合的規則{mode_text}",
            theme.PRIMARY,
            text_color=theme.WHITE,
        )

        # 強制回到第一頁
        self.current_page = 1
        self._render_current_page()

    def _rule_matches(self, rule: dict, keyword: str, use_regex: bool) -> bool:
        """檢查規則是否符合搜尋條件"""
        # 搜尋欄位
        fields = ["from", "to", "comment", "category"]

        for field in fields:
            value = rule.get(field, "")
            if not value:
                continue

            # Regex 模式
            if use_regex:
                try:
                    flags = 0 if self.search_case_sensitive else re.IGNORECASE
                    if re.search(keyword, value, flags):
                        return True
                except re.error:
                    # Regex 錯誤，回退到普通搜尋
                    pass

            # 普通搜尋模式
            if not self.search_case_sensitive:
                if keyword.lower() in value.lower():
                    return True
            else:
                if keyword in value:
                    return True

        return False

    # ---------------------------------------------
    # 規則驗證模組
    # ---------------------------------------------

    def validate_rule(
        self, src: str, dst: str, all_rules, current_index, from_index=None
    ):
        """
        驗證規則格式正確性，回傳 (is_valid: bool, msg: str)

        from_index：可選的 {from: [索引...]}（由 _build_from_index 建立）。
        批次驗證全部規則時傳入，重複檢查就不必每條都掃過整個清單（O(n²) → O(n)）。
        """
        if not src.strip():
            return False, "from 欄位不可為空"

        try:
            compiled = re.compile(src)
        except re.error as err:
            return False, self.translate_regex_error(err)

        if from_index is not None:
            candidates = from_index.get(src, ())
        else:
            candidates = (
                idx for idx, rule in enumerate(all_rules) if rule.get("from") == src
            )
        for idx in candidates:
            if idx != current_index:
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
        """翻譯正則表達式錯誤訊息"""
        return rules_translate_regex_error(err)

    # --- 執行緒輔助與載入 ---

    def _run_on_ui_thread(self, func, *args, **kwargs):
        """在 UI 執行緒（Flet event loop）上安全執行函式。

        尚未掛上頁面時先暫存，等 did_mount 再執行，避免背景載入比掛載更早完成時
        結果被丟掉或在背景執行緒直接改控制項。
        """
        with self._ui_lock:
            try:
                page = self.page
            except RuntimeError:
                page = None
            loop = getattr(page, "loop", None) if page else None
            if loop is None:
                self._pending_ui_calls.append((func, args, kwargs))
                return
        loop.call_soon_threadsafe(lambda: func(*args, **kwargs))

    def did_mount(self):
        """掛上頁面後執行在掛載前排入的 UI 更新。"""
        with self._ui_lock:
            pending, self._pending_ui_calls = self._pending_ui_calls, []
        for func, args, kwargs in pending:
            func(*args, **kwargs)

    def _load_rules_core(self):
        """從檔案載入替換規則並回傳"""
        return load_replace_rules()

    def _initial_load(self):
        """初次啟動時從檔案載入規則並渲染"""

        def run():
            # 背景執行緒只負責讀檔；渲染與 page.update() 交給 event loop
            try:
                rules_data = self._load_rules_core()
            except Exception as err:  # noqa: BLE001 - 失敗要顯示在 UI
                self._run_on_ui_thread(self._handle_reload_failure, err)
                return
            self._run_on_ui_thread(self._handle_reload_success, rules_data)

        threading.Thread(target=run, daemon=True).start()

    # --- 分頁渲染邏輯 ---

    def _render_current_page(self):
        """根據當前頁碼渲染規則表格（支援搜尋結果過濾）"""
        # 決定資料來源：搜尋結果 OR 全部資料
        if self.search_results is not None:
            # 搜尋模式：只顯示符合的資料
            display_data = self.search_results
            total_count = len(display_data)
            self.total_pages = max(
                1, (total_count + self.page_size - 1) // self.page_size
            )
            # 確保頁碼在有效範圍內
            self.current_page = min(self.current_page, self.total_pages)
            self.current_page = max(self.current_page, 1)
        else:
            # 一般模式：顯示全部資料
            display_data = self.all_rules_data
            total_count = len(self.all_rules_data)
            self.total_pages = calc_total_pages(total_count, self.page_size)

        # 計算當前頁的資料範圍
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        current_page_data = display_data[start:end]

        # 重用既有的表格列，只更新內容：
        # 每次都清空重建 50 列（各含兩個多行 TextField）時，Flet 需移除並重新序列化
        # 所有控制項，3 萬條規則下排序 / 清除搜尋單次約 2 秒；重用後只送出變更的值。
        # 多出來的列（例如搜尋結果較少時）只隱藏不刪除，清除搜尋時不必重建。
        rows = self.rules_table.rows
        needed = len(current_page_data)
        while len(rows) < needed:
            rows.append(self.create_rule_row("", "", 0, 0))
        for extra in rows[needed:]:
            extra.visible = False
            extra.data = None

        for offset, rule in enumerate(current_page_data):
            # 確保有 RID
            if "_rid" not in rule:
                rule["_rid"] = self._new_rid()
            self._fill_rule_row(
                rows[offset],
                rule.get("from", ""),
                rule.get("to", ""),
                rule["_rid"],
                display_no=start + offset + 1,
            )

        # 顯示搜尋結果數或總數
        if self.search_results is not None:
            total_rules = len(self.search_results)
            status_text = "（搜尋結果）"
        else:
            total_rules = len(self.all_rules_data)
            status_text = ""

        self.page_info.value = f"頁面 {self.current_page} / {self.total_pages}"
        self.total_pages_text_label.value = f"/ {self.total_pages} 頁"
        self.total_count_text.value = f"共 {total_rules} 條規則 {status_text}"
        self._sync_page_jump_field()

        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == self.total_pages

        # 只刷新規則頁本身（而非整頁 diff）；尚未掛載時退回整頁更新
        try:
            self.update()
        except (AssertionError, RuntimeError):
            self.page.update()

    # --- 互動事件處理 ---

    def on_text_change(self, e):
        """當文字輸入變更時即時驗證並更新資料"""
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
            from_field.border_color = C.RED
            to_field.border_color = C.RED
            from_field.error_text = msg
            to_field.error_text = msg

        from_field.update()
        to_field.update()

    @staticmethod
    def _fill_rule_row(row, from_text, to_text, rid: int, display_no: int):
        """把既有的表格列改成顯示指定規則（清除先前的驗證錯誤樣式）。"""
        row.visible = True
        row.data = rid
        number_cell, from_cell, to_cell, delete_cell = row.cells
        number_cell.content.value = str(display_no)
        for field, name, value in (
            (from_cell.content, "from", from_text),
            (to_cell.content, "to", to_text),
        ):
            field.value = value
            field.data = {"rid": rid, "field": name}
            field.border_color = None
            field.error_text = None
        delete_cell.content.data = rid

    def create_rule_row(self, from_text, to_text, rid: int, display_no: int):
        """建立規則編輯列 UI 元件"""
        return rules_create_row(self, from_text, to_text, rid, display_no)

    # --- 操作邏輯 ---

    def reload_rules_clicked(self, e):
        """觸發重新載入規則的執行緒"""
        return start_reload_thread(self)

    def _handle_reload_success(self, rules_data):
        """處理規則重新載入成功後的資料初始化與渲染"""
        self.all_rules_data = rules_data
        # ✅ 給每條 rule 補上穩定 rid
        for r in self.all_rules_data:
            if "_rid" not in r:
                r["_rid"] = self._new_rid()

        self.current_page = 1
        self._render_current_page()
        self.loading_indicator.visible = False
        show_snack(self.page, "規則載入完成！", theme.GREEN_600, text_color=theme.WHITE)
        self.page.update()

    def _handle_reload_failure(self, err):
        """處理規則重新載入失敗的錯誤顯示"""
        self.loading_indicator.visible = False
        self.page.update()
        show_snack(
            self.page, f"載入規則時發生錯誤: {err}", theme.ERROR, text_color=theme.WHITE
        )

    def prev_page(self, e):
        """上一頁，若已在首頁則顯示提示"""
        if self.current_page > 1:
            self.current_page -= 1
            self._render_current_page()
        else:
            show_snack(self.page, "已在第一頁", theme.PRIMARY, text_color=theme.WHITE)

    def next_page(self, e):
        """下一頁，若已在末頁則顯示提示"""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._render_current_page()
        else:
            show_snack(self.page, "已在最後一頁", theme.PRIMARY, text_color=theme.WHITE)

    @staticmethod
    def _build_from_index(all_rules) -> dict:
        """建立 {from: [索引...]}，供批次驗證時 O(1) 查重複。"""
        index: dict = {}
        for idx, rule in enumerate(all_rules):
            index.setdefault(rule.get("from"), []).append(idx)
        return index

    def _validate_all(self, rules):
        """驗證全部規則；回傳 (錯誤索引, 訊息) 或 None。可在背景執行緒執行。"""
        from_index = self._build_from_index(rules)
        for idx, rule in enumerate(rules):
            ok, msg = self.validate_rule(
                rule["from"], rule["to"], rules, idx, from_index=from_index
            )
            if not ok:
                return idx, msg
        return None

    def save_rules_clicked(self, e):
        """儲存規則點擊事件：驗證（背景執行緒）→ 儲存。

        3 萬條規則時舊版逐條掃描整個清單查重複（O(n²)），UI 約 87 秒無回應。
        """
        if getattr(self, "_saving", False):
            return
        snapshot = [dict(r) for r in self.all_rules_data]

        def finish(failure):
            self._saving = False
            if failure is not None:
                idx, msg = failure
                show_snack(
                    self.page,
                    f"第 {idx + 1} 條規則錯誤：{msg}",
                    theme.ERROR,
                    text_color=theme.WHITE,
                )
                self.current_page = idx // self.page_size + 1
                self._render_current_page()
                return

            # 移除 _rid 並過濾
            clean_rules = [
                {"from": r.get("from", ""), "to": r.get("to", "")}
                for r in snapshot
                if r.get("from", "").strip()
            ]
            show_snack(
                self.page,
                "✅ 驗證通過，正在儲存規則…",
                theme.PRIMARY,
                text_color=theme.WHITE,
            )
            start_save_thread(self, clean_rules)

        run_task = getattr(self.page, "run_task", None)
        if run_task is None:
            finish(self._validate_all(snapshot))
            return

        self._saving = True
        show_snack(self.page, "🔎 正在驗證規則…", theme.PRIMARY, text_color=theme.WHITE)

        async def _validate_then_save():
            try:
                failure = await asyncio.to_thread(self._validate_all, snapshot)
            except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
                self._saving = False
                show_snack(
                    self.page,
                    f"驗證規則時發生錯誤：{ex}",
                    theme.ERROR,
                    text_color=theme.WHITE,
                )
                return
            finish(failure)

        run_task(_validate_then_save)

    def add_row_clicked(self, e):
        """新增一列空白規則並跳轉至最後一頁"""
        self.all_rules_data.append({"from": "", "to": "", "_rid": self._new_rid()})
        self.current_page = self.total_pages  # 假設在最後
        # 重新計算總頁數（因為可能剛好換頁）
        total_rules = len(self.all_rules_data)
        self.total_pages = calc_total_pages(total_rules, self.page_size)
        self.current_page = self.total_pages

        self._render_current_page()
        show_snack(
            self.page,
            "➕ 已新增一條規則（已跳至最後一頁）",
            theme.PRIMARY,
            text_color=theme.WHITE,
        )

    def delete_row_clicked(self, e):
        """刪除指定 RID 的規則並重新渲染"""
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
            show_snack(
                self.page,
                f"🗑 已刪除：{src_preview} → {dst_preview}",
                theme.ERROR,
                text_color=theme.WHITE,
            )

    @property
    def page(self):
        return self._page
