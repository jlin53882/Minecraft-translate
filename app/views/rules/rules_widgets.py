"""RulesView 的控制項與版面組裝（由 rules_view.py 拆出，#114）。"""

import flet as ft

from app.ui import design, kit
from app.ui.design import C


class RulesWidgetsMixin:
    """RulesView 的控制項與版面組裝（由 rules_view.py 拆出，#114）。"""

    def _init_controls(self):
        """初始化所有互動控制項"""
        self._init_rules_table_controls()
        self._init_rules_sort_and_test_controls()

    def _init_rules_table_controls(self) -> None:
        """規則表分頁與導覽控制項。"""
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

    def _init_rules_sort_and_test_controls(self) -> None:
        """排序、規則表與即時測試控制項。"""

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
