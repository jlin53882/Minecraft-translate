"""app/views/config_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from typing import ClassVar

import flet as ft

from app.config_apply import apply_timing_note
from app.services_impl.config_service import load_config_json, save_config_json
from app.services_impl.key_health_service import validate_api_keys_from_ui
from app.ui import design, kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.config.config_actions import (
    load_config_into_view,
    save_config_from_view,
)
from app.views.config.config_form import (
    build_card as build_config_card,
)
from app.views.config.config_form import (
    build_footer as build_config_footer,
)
from app.views.config.config_form import (
    build_header as build_config_header,
)
from app.views.config.config_form import (
    build_key_field,
    build_key_row,
)
from app.views.config.db_location import DbLocationBanner, attach_path_hooks
from app.views.config.settings_form import build_controls, build_pages
from app.views.config.settings_schema import NAV_PAGES

# 導覽項目由 settings_schema.NAV_PAGES 產生（圖示名稱對應 ft.Icons）
NAV_ITEMS = [
    {"id": page["id"], "label": page["label"], "icon": getattr(ft.Icons, page["icon"])}
    for page in NAV_PAGES
]


class ConfigView(ft.Column):
    """ConfigView 類別。

    用途：封裝與 ConfigView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    DEFAULT_MODELS: ClassVar[dict[str, bool]] = {
        "gemini-2.5-flash": True,
    }

    def __init__(self, page: ft.Page):
        """初始化 ConfigView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=0)
        self._page = page
        self._registry = None
        self.controls_map = {}
        self._selected_nav = "general"

        self._init_controls()
        self.db_location = DbLocationBanner()
        self._check_db_path = attach_path_hooks(
            self.controls_map["translation_db.path"], self.db_location
        )

        self.scroll_container = ft.Column(
            scroll=ft.ScrollMode.ADAPTIVE,
            expand=True,
            spacing=15,
            controls=[
                self._build_header(),
                self.db_location,
                ft.ResponsiveRow(
                    controls=[
                        ft.Container(
                            content=self._build_nav_column(),
                            col={"xs": 12, "md": 3},
                        ),
                        ft.Container(
                            content=self._build_content_area(),
                            col={"xs": 12, "md": 9},
                        ),
                    ],
                    spacing=15,
                    run_spacing=15,
                ),
            ],
        )

        self.footer = self._build_footer()

        self.controls = [self.scroll_container, self.footer]

        self.load_config()

    def _init_controls(self):
        """初始化所有輸入控制項"""
        # 一般設定的控制項由 settings_schema 產生（#134）；下面只建立專用元件
        build_controls(self.controls_map)

        self.new_model_field = kit.field(
            label="新增模型名稱", hint_text="gemini-2.5-flash", expand=True, dense=True
        )
        self.add_model_button = ft.IconButton(
            icon=ft.Icons.ADD, tooltip="新增模型", on_click=self.on_add_model_clicked
        )
        self.models_column = ft.Column(spacing=5)
        self.controls_map["lm_translator.models"] = self.models_column

        self.add_key_button = ft.IconButton(
            icon=ft.Icons.ADD,
            tooltip="新增 API Key",
            on_click=lambda e: self.add_key_row(),
        )
        self.key_fields: list[ft.TextField] = []
        self.keys_column = ft.Column(spacing=5)
        self.controls_map["lm_translator.keys"] = self.keys_column

    def _build_nav_item(self, item: dict) -> ft.Container:
        """建立導覽項目按鈕"""
        is_selected = self._selected_nav == item["id"]
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=11),
            border_radius=design.RADIUS_CONTROL,
            bgcolor=C.EM_BG if is_selected else None,
            ink=True,
            on_click=lambda e, iid=item["id"]: self._on_nav_click(iid),
            content=ft.Row(
                [
                    ft.Icon(
                        item["icon"], size=18, color=C.EM if is_selected else C.MUTED
                    ),
                    ft.Text(
                        item["label"],
                        weight=ft.FontWeight.W_700
                        if is_selected
                        else ft.FontWeight.W_500,
                        size=13,
                        color=C.TEXT if is_selected else C.MUTED,
                    ),
                ],
                spacing=10,
                alignment=ft.MainAxisAlignment.START,
            ),
        )

    def _on_nav_click(self, nav_id: str):
        """處理導覽點擊"""
        self._selected_nav = nav_id
        self._rebuild_nav()
        self._show_content(nav_id)

    def _rebuild_nav(self):
        """重新建構導覽列"""
        self.nav_column.controls = [self._build_nav_item(item) for item in NAV_ITEMS]
        self.nav_column.update()

    def _show_content(self, nav_id: str):
        """切換顯示內容"""
        for cid, container in self._content_containers.items():
            container.visible = cid == nav_id
        self.content_scroll.update()

    def _build_nav_column(self) -> ft.Container:
        """建立左側導覽列"""
        self.nav_column = ft.Column(
            [self._build_nav_item(item) for item in NAV_ITEMS], spacing=4
        )
        return ft.Container(
            content=ft.Column(
                [kit.section_label("設定分類"), self.nav_column], spacing=8
            ),
            padding=12,
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CARD,
        )

    def _build_content_area(self) -> ft.Column:
        """建立右側內容區（所有分類內容）；版面由 settings_schema 產生。"""
        self._content_containers = build_pages(
            self.controls_map,
            self._build_card,
            {"keys": self._keys_panel, "models": self._models_panel},
        )

        self.content_scroll = ft.Container(
            expand=True,
            content=ft.Stack(list(self._content_containers.values())),
        )

        for cid, container in self._content_containers.items():
            container.visible = cid == self._selected_nav

        return self.content_scroll

    def _models_panel(self) -> ft.Control:
        """模型清單的專用元件（卡片內容）。"""
        return ft.Container(
            bgcolor=C.PANEL,
            padding=10,
            border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                "模型清單 (Models List)", weight=ft.FontWeight.BOLD
                            ),
                            self.new_model_field,
                            self.add_model_button,
                        ]
                    ),
                    self.models_column,
                ]
            ),
        )

    def _keys_panel(self) -> ft.Control:
        """API 金鑰列的專用元件（卡片內容）。"""
        return ft.Container(
            bgcolor=C.PANEL,
            padding=10,
            border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("API 金鑰 (API Keys)", weight=ft.FontWeight.BOLD),
                            self.add_key_button,
                        ]
                    ),
                    self.keys_column,
                ]
            ),
        )

    def _build_lang_merger_card(self) -> ft.Control:
        """語言合併器頁的卡片（保留給既有呼叫端；內容由 schema 產生）。"""
        return self._content_containers["merger"].controls[0]

    def _build_header(self):
        """建立頁面標題"""
        return build_config_header(self)

    def _build_footer(self):
        """建立底部儲存列"""
        return build_config_footer(self)

    def _build_card(self, title, controls_list):
        """建立設定卡片"""
        return build_config_card(self, title, controls_list)

    def add_model_row(self, model_name: str, max_output_tokens: int | None = None):
        """新增模型項目到列表"""
        cb = ft.Checkbox(
            label=model_name,
            value=True,
            expand=True,
            label_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_500),
        )
        order_text = ft.Text(
            "00",
            size=12,
            color=C.MUTED,
            weight=ft.FontWeight.W_500,
            width=28,
            text_align=ft.TextAlign.RIGHT,
        )
        btn_up = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_UP,
            tooltip="上移",
            icon_size=18,
            on_click=lambda e: self.move_model_row(cb, -1),
        )
        btn_down = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_DOWN,
            tooltip="下移",
            icon_size=18,
            on_click=lambda e: self.move_model_row(cb, +1),
        )
        btn_delete = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip="刪除模型",
            icon_size=18,
            on_click=lambda e: self.remove_model_by_checkbox(cb),
        )
        max_tokens_field = kit.field(
            value="" if max_output_tokens is None else str(max_output_tokens),
            label="模型上限",
            hint_text="全域",
            helper=apply_timing_note("lm_translator.models.*.max_output_tokens"),
            dense=True,
            width=130,
            keyboard_type=ft.KeyboardType.NUMBER,
        )

        row = ft.Container(
            padding=12,
            border_radius=8,
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.TRACK),
            content=ft.Row(
                [
                    order_text,
                    ft.Row([cb, max_tokens_field], expand=True),
                    ft.Row([btn_up, btn_down, btn_delete], spacing=2),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        row._order_text = order_text
        row._checkbox = cb
        row._max_output_tokens = max_tokens_field
        self.models_column.controls.append(row)
        self._refresh_model_order_labels()

    def move_model_row(self, cb: ft.Checkbox, direction: int):
        """移動模型順序（上移/下移）"""
        controls = self.models_column.controls
        idx = next((i for i, r in enumerate(controls) if r._checkbox is cb), None)
        if idx is None:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(controls):
            return
        controls[idx], controls[new_idx] = controls[new_idx], controls[idx]
        self._refresh_model_order_labels()

    def remove_model_by_checkbox(self, cb: ft.Checkbox):
        """刪除勾選的模型項目"""
        row = next((r for r in self.models_column.controls if r._checkbox is cb), None)
        if row:
            self.models_column.controls.remove(row)
        self._refresh_model_order_labels()

    def on_add_model_clicked(self, e):
        """處理新增模型按鈕點擊事件"""
        name = self.new_model_field.value.strip()
        if not name:
            show_snack(self.page, "模型名稱不能為空")
            return
        if any(r._checkbox.label == name for r in self.models_column.controls):
            show_snack(self.page, "此模型已存在")
            return
        self.add_model_row(name)
        self.new_model_field.value = ""
        self.page.update()

    def _build_key_field(self, value: str = ""):
        """建立 API Key 輸入欄位"""
        return build_key_field(value=value)

    def _build_key_row(self, tf: ft.TextField):
        """建立 API Key 列"""
        return build_key_row(self, tf)

    def add_key_row(self):
        """新增 API Key 列"""
        tf = self._build_key_field()
        row = self._build_key_row(tf)
        self.key_fields.append(tf)
        self.keys_column.controls.append(row)
        self.keys_column.update()

    def remove_key_row(self, row: ft.Row):
        """刪除 API Key 列表中的指定列"""
        if row in self.keys_column.controls:
            idx = self.keys_column.controls.index(row)
            self.keys_column.controls.remove(row)
            self.key_fields.pop(idx)
        self.keys_column.update()

    def _refresh_model_order_labels(self):
        """重新整理模型順序編號"""
        for idx, row in enumerate(self.models_column.controls):
            if hasattr(row, "_order_text"):
                row._order_text.value = f"{idx + 1:02d}"
        self.page.update()

    def load_config(self):
        """載入設定檔"""
        config = load_config_json()
        result = load_config_into_view(self, config)
        self.db_location.refresh()
        self._check_db_path()
        return result

    def did_mount(self):
        """切回設定頁時重新確認資料庫位置（其他頁可能剛建立了資料庫）。"""
        self.db_location.safe_refresh()

    def _success_color(self):
        """取得成功顏色"""
        return C.EM

    def save_config_clicked(self, e):
        """儲存設定"""
        return save_config_from_view(
            self,
            load_config_json_fn=load_config_json,
            save_config_json_fn=save_config_json,
            validate_api_keys_from_ui_fn=validate_api_keys_from_ui,
            registry=self._registry,
        )

    @property
    def page(self):
        return self._page

    def set_registry(self, registry):
        """儲存 registry 參考，讓 save_config_clicked 能廣播到其他 views"""
        self._registry = registry
