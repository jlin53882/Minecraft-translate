"""app/views/config_view.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
import traceback
from typing import ClassVar

import flet as ft

from app.services_impl.config_service import load_config_json, save_config_json
from app.services_impl.key_health_service import validate_api_keys_from_ui
from app.ui import design, kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.config.config_actions import (
    SaveOutcome,
    load_config_into_view,
    save_config_from_view_with_outcome,
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
from app.views.config.db_location import (
    DbLocationBanner,
    attach_path_hooks,
    attach_priority_hooks,
)
from app.views.config.settings_form import build_controls, build_pages
from app.views.config.settings_schema import NAV_PAGES
from translation_tool.utils.redaction import redact_text

logger = logging.getLogger(__name__)

# 導覽項目由 settings_schema.NAV_PAGES 產生（圖示名稱對應 ft.Icons）
NAV_ITEMS = [
    {"id": page["id"], "label": page["label"], "icon": getattr(ft.Icons, page["icon"])}
    for page in NAV_PAGES
]


def _model_cap_controls(view, max_output_tokens):
    field = kit.field(
        value="" if max_output_tokens is None else str(max_output_tokens),
        label="模型上限",
        hint_text="全域",
        dense=True,
        width=130,
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=view._on_form_changed,
    )
    help_text = ft.Text(
        "留空：沿用全域「輸出 Token 上限」；輸入數值：使用此模型專屬上限；"
        "設為 0：不指定輸出上限。修改後於下一批翻譯時套用。",
        size=11,
        color=C.MUTED,
        visible=False,
    )
    help_button = ft.IconButton(
        icon=ft.Icons.HELP_OUTLINE,
        tooltip="顯示此模型的輸出上限說明",
        icon_size=17,
        on_click=lambda _e: view._toggle_model_cap_help(help_text, help_button),
    )
    cap_controls = ft.Row(
        [field, help_button],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return field, help_text, help_button, cap_controls


def _build_model_row(view, model_name, max_output_tokens):
    cb = ft.Checkbox(
        label="啟用",
        value=True,
        label_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_500),
        on_change=view._on_form_changed,
    )
    model_name_text = ft.Text(model_name, size=14, weight=ft.FontWeight.W_500)
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
        on_click=lambda _e: view.move_model_row(cb, -1),
    )
    btn_down = ft.IconButton(
        icon=ft.Icons.KEYBOARD_ARROW_DOWN,
        tooltip="下移",
        icon_size=18,
        on_click=lambda _e: view.move_model_row(cb, +1),
    )
    btn_delete = ft.IconButton(
        icon=ft.Icons.DELETE_OUTLINE,
        tooltip="刪除模型",
        icon_size=18,
        on_click=lambda _e: view.remove_model_by_checkbox(cb),
    )
    max_tokens_field, help_text, help_button, cap_controls = _model_cap_controls(
        view, max_output_tokens
    )
    row_content = ft.Column(
        [
            ft.Row(
                [
                    order_text,
                    ft.Row(
                        [
                            cb,
                            ft.Container(content=model_name_text, expand=True),
                            cap_controls,
                        ],
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row([btn_up, btn_down, btn_delete], spacing=2),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            help_text,
        ],
        spacing=4,
    )
    row = ft.Container(
        padding=12,
        border_radius=8,
        bgcolor=C.PANEL,
        border=ft.Border.all(1, C.TRACK),
        content=row_content,
    )
    row._order_text = order_text
    row._model_name = model_name
    row._model_name_text = model_name_text
    row._checkbox = cb
    row._max_output_tokens = max_tokens_field
    row._max_output_tokens_help = help_text
    row._max_output_tokens_help_button = help_button
    return row


class ConfigView(ft.Column):
    """ConfigView 類別。

    用途：封裝與 ConfigView 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """

    DEFAULT_MODELS: ClassVar[dict[str, bool]] = {
        "gemini-3.5-flash-lite": True,
        "gemini-3.1-flash-lite": True,
    }

    def __init__(self, page: ft.Page):
        """初始化 ConfigView。

        參數：
            page: Flet Page 物件
        """
        super().__init__(expand=True, spacing=0)
        self._page = page
        self._registry = None
        self._loading_config = False
        self._saved_form_state = None
        self._unsaved_dialog_open = False
        self._unsaved_dialog_resolved = False
        self._unsaved_dialog_continue = None
        self._unsaved_dialog = None
        self._allow_saved_recovery_exit = False
        self._last_save_outcome = None
        self._reload_recovery_required = False
        self._reload_before_next_entry = False
        self.controls_map = {}
        self._selected_nav = "general"

        self._init_controls()
        self.db_location = DbLocationBanner()
        self._check_db_path = attach_path_hooks(
            self.controls_map["translation_db.path"], self.db_location
        )
        self._check_priority = attach_priority_hooks(
            self.controls_map["translation_db.priority"]
        )
        self._bind_general_change_tracking()

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
            label="新增模型名稱",
            hint_text="gemini-3.5-flash-lite",
            expand=True,
            dense=True,
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
        if nav_id == self._selected_nav:
            return
        if self.requires_exit_confirmation:
            self.confirm_unsaved_changes(lambda: self._apply_nav(nav_id))
            return
        self._apply_nav(nav_id)

    def _apply_nav(self, nav_id: str):
        self._selected_nav = nav_id
        self._rebuild_nav()
        self._show_content(nav_id)

    def confirm_unsaved_changes(self, on_continue, *, allow_saved_recovery_exit=False):
        """Protect unsaved changes and a confirmed-save UI reload that needs recovery."""
        if self._unsaved_dialog_open:
            return False
        show_dialog = getattr(self.page, "show_dialog", None)
        if not callable(show_dialog):
            show_snack(self.page, "設定尚未儲存；目前無法安全切換頁面。")
            return False

        self._unsaved_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("設定尚未儲存"),
            content=ft.Text("要先儲存設定、放棄變更，還是留在此頁？"),
        )
        self._unsaved_dialog_resolved = False
        self._unsaved_dialog_continue = on_continue
        self._allow_saved_recovery_exit = allow_saved_recovery_exit
        self._configure_unsaved_dialog()
        self._unsaved_dialog_open = True
        try:
            show_dialog(self._unsaved_dialog)
        except Exception:
            self._unsaved_dialog_open = False
            self._unsaved_dialog_continue = None
            raise
        return True

    def _configure_unsaved_dialog(self) -> None:
        dialog = self._unsaved_dialog
        if self._reload_recovery_required:
            dialog.title.value = "設定已寫入，但畫面尚未同步"
            dialog.content.value = (
                "設定檔已確認寫入，但頁面重載失敗。請重試重新載入；"
                "若仍要離開，需確認已接受設定畫面尚未同步。"
                if self._allow_saved_recovery_exit
                else "設定檔已確認寫入，但頁面重載失敗。請重試重新載入，"
                "或留在此頁；重新載入成功前不會切換設定分類。"
            )
            dialog.actions = [
                ft.TextButton("留在此頁", on_click=self._on_unsaved_dialog_stay),
                ft.TextButton(
                    "重試重新載入", on_click=self._on_unsaved_dialog_retry_reload
                ),
            ]
            if self._allow_saved_recovery_exit:
                dialog.actions.append(
                    ft.TextButton(
                        "已確認寫入，仍要離開",
                        on_click=self._on_unsaved_dialog_leave_after_ack,
                    )
                )
        else:
            dialog.title.value = "設定尚未儲存"
            dialog.content.value = "要先儲存設定、放棄變更，還是留在此頁？"
            dialog.actions = [
                ft.TextButton("留在此頁", on_click=self._on_unsaved_dialog_stay),
                ft.TextButton("放棄變更", on_click=self._on_unsaved_dialog_discard),
                ft.TextButton("儲存並繼續", on_click=self._on_unsaved_dialog_save),
            ]
        self.page.update()

    def _finish_unsaved_dialog(self, *, continue_navigation: bool) -> None:
        if self._unsaved_dialog_resolved:
            return
        self._unsaved_dialog_resolved = True
        self._unsaved_dialog_open = False
        callback = self._unsaved_dialog_continue
        self._unsaved_dialog_continue = None
        self.page.pop_dialog()
        if continue_navigation and callback is not None:
            callback()

    def _on_unsaved_dialog_stay(self, _event=None) -> None:
        self._finish_unsaved_dialog(continue_navigation=False)

    def _on_unsaved_dialog_save(self, _event=None) -> None:
        if self._unsaved_dialog_resolved:
            return
        save_succeeded = self.save_config_clicked(None)
        if self._last_save_outcome is SaveOutcome.SAVED_RELOAD_FAILED:
            self._configure_unsaved_dialog()
        elif save_succeeded:
            self._finish_unsaved_dialog(continue_navigation=True)

    def _on_unsaved_dialog_discard(self, _event=None) -> None:
        if self._unsaved_dialog_resolved:
            return
        try:
            self.discard_unsaved_changes()
        except Exception:  # noqa: BLE001 - keep the dialog recoverable on reload failure
            logger.error(
                "放棄設定變更時重新載入失敗：%s",
                redact_text(traceback.format_exc()),
            )
            self._unsaved_dialog.content.value = (
                "重新載入設定失敗，原表單內容仍保留；可重試，或留在此頁。"
            )
            self.page.update()
            return
        self._finish_unsaved_dialog(continue_navigation=True)

    def _on_unsaved_dialog_retry_reload(self, _event=None) -> None:
        if self._unsaved_dialog_resolved:
            return
        if not self._retry_config_reload():
            self._unsaved_dialog.content.value = (
                "設定已寫入，但重新載入仍失敗；可稍後重試或留在此頁。"
            )
            self.page.update()
            return
        self._finish_unsaved_dialog(continue_navigation=True)

    def _on_unsaved_dialog_leave_after_ack(self, _event=None) -> None:
        if self._unsaved_dialog_resolved:
            return
        # The writer already confirmed persistence. Force a fresh load before re-entry.
        self._reload_recovery_required = False
        self._reload_before_next_entry = True
        self._saved_form_state = None
        self._finish_unsaved_dialog(continue_navigation=True)

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
                    ft.Text(
                        "輸入模型名稱後按「+」加入清單；未加入的文字不會寫入設定。勾選「啟用」的模型才會參與翻譯；至少保留一個啟用模型。",
                        size=12,
                        color=C.MUTED,
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
        row = _build_model_row(self, model_name, max_output_tokens)
        self.models_column.controls.append(row)
        self._refresh_model_order_labels()

    def _toggle_model_cap_help(self, help_text: ft.Text, button: ft.IconButton):
        """Toggle one model's output-cap hint without affecting sibling rows."""
        help_text.visible = not help_text.visible
        button.icon = ft.Icons.INFO if help_text.visible else ft.Icons.HELP_OUTLINE
        button.tooltip = (
            "隱藏此模型的輸出上限說明"
            if help_text.visible
            else "顯示此模型的輸出上限說明"
        )
        self.page.update()

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
        self._refresh_dirty_state()

    def remove_model_by_checkbox(self, cb: ft.Checkbox):
        """刪除勾選的模型項目"""
        row = next((r for r in self.models_column.controls if r._checkbox is cb), None)
        if row:
            self.models_column.controls.remove(row)
        self._refresh_model_order_labels()
        if row:
            self._refresh_dirty_state()

    def on_add_model_clicked(self, e):
        """處理新增模型按鈕點擊事件"""
        name = self.new_model_field.value.strip()
        if not name:
            show_snack(self.page, "模型名稱不能為空")
            return
        if any(r._model_name == name for r in self.models_column.controls):
            show_snack(self.page, "此模型已存在")
            return
        self.add_model_row(name)
        self.new_model_field.value = ""
        self.page.update()
        self._refresh_dirty_state()
        show_snack(
            self.page,
            "模型已加入清單，記得按「儲存變更」才會寫入 config.json。",
            C.GOLD,
        )

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
        self._bind_change_tracking(tf)
        self.keys_column.update()
        self._refresh_dirty_state()

    def remove_key_row(self, row: ft.Row):
        """刪除 API Key 列表中的指定列"""
        if row in self.keys_column.controls:
            idx = self.keys_column.controls.index(row)
            self.keys_column.controls.remove(row)
            self.key_fields.pop(idx)
        self.keys_column.update()
        self._refresh_dirty_state()

    def _refresh_model_order_labels(self):
        """重新整理模型順序編號"""
        for idx, row in enumerate(self.models_column.controls):
            if hasattr(row, "_order_text"):
                row._order_text.value = f"{idx + 1:02d}"
        self.page.update()

    def load_config(self):
        """載入設定檔"""
        self._loading_config = True
        try:
            config = load_config_json()
            result = load_config_into_view(self, config)
            for tf in self.key_fields:
                self._bind_change_tracking(tf)
            self._saved_form_state = self._capture_form_state()
        finally:
            self._loading_config = False
        self.db_location.refresh()
        self._check_db_path()
        self._check_priority()
        self._reload_recovery_required = False
        self._reload_before_next_entry = False
        self._refresh_dirty_state()
        return result

    def did_mount(self):
        """切回設定頁時重新確認資料庫位置（其他頁可能剛建立了資料庫）。"""
        self.db_location.safe_refresh()

    def _success_color(self):
        """取得成功顏色"""
        return C.EM

    def save_config_clicked(self, e):
        """儲存設定"""
        if self._reload_recovery_required or self._reload_before_next_entry:
            self._last_save_outcome = None
            return self._retry_config_reload()
        outcome = save_config_from_view_with_outcome(
            self,
            load_config_json_fn=load_config_json,
            save_config_json_fn=save_config_json,
            validate_api_keys_from_ui_fn=validate_api_keys_from_ui,
            registry=self._registry,
        )
        self._last_save_outcome = outcome
        if outcome is SaveOutcome.SAVED_OK:
            self._saved_form_state = self._capture_form_state()
            self._reload_recovery_required = False
            self._refresh_dirty_state()
        elif outcome is SaveOutcome.SAVED_RELOAD_FAILED:
            self._reload_recovery_required = True
            self._refresh_dirty_state()
        return outcome is SaveOutcome.SAVED_OK

    def _retry_config_reload(self) -> bool:
        try:
            self.load_config()
        except Exception:  # noqa: BLE001 - keep recovery state until a full reload succeeds
            self._reload_recovery_required = True
            logger.error("設定重載恢復失敗：%s", redact_text(traceback.format_exc()))
            show_snack(
                self.page,
                "⚠️ 設定仍未重新載入；目前內容已保留，請稍後重試。",
            )
            self._refresh_dirty_state()
            return False
        show_snack(self.page, "✅ 設定已重新載入，畫面與設定檔已同步。", C.EM)
        return True

    def reload_before_entry(self) -> bool:
        """Ensure a previously acknowledged stale view is refreshed before showing it."""
        if not self._reload_before_next_entry:
            return True
        if self._retry_config_reload():
            return True
        self._reload_recovery_required = True
        return False

    def _bind_general_change_tracking(self) -> None:
        for path, control in self.controls_map.items():
            if path in ("lm_translator.keys", "lm_translator.models"):
                continue
            if hasattr(control, "on_change") or hasattr(control, "on_select"):
                self._bind_change_tracking(control)

    def _bind_change_tracking(self, control: ft.Control) -> None:
        tracked_events = getattr(control, "_config_change_tracked_events", set())
        for event_name in ("on_change", "on_select", "on_blur"):
            if not hasattr(control, event_name) or event_name in tracked_events:
                continue
            previous = getattr(control, event_name, None)

            def on_event(event, previous=previous):
                if callable(previous):
                    previous(event)
                self._on_form_changed(event)

            setattr(control, event_name, on_event)
            tracked_events.add(event_name)
        control._config_change_tracked_events = tracked_events

    def _on_form_changed(self, _event=None) -> None:
        self._refresh_dirty_state()

    def _capture_form_state(self):
        settings = tuple(
            (path, repr(control.value))
            for path, control in self.controls_map.items()
            if path not in ("lm_translator.keys", "lm_translator.models")
            and hasattr(control, "value")
        )
        keys = tuple(tf.value or "" for tf in self.key_fields)
        models = tuple(
            (
                row._model_name,
                bool(row._checkbox.value),
                ""
                if row._max_output_tokens.value is None
                else str(row._max_output_tokens.value),
            )
            for row in self.models_column.controls
        )
        return settings, keys, models

    @property
    def has_unsaved_changes(self) -> bool:
        return (
            self._saved_form_state is not None
            and self._capture_form_state() != self._saved_form_state
        )

    @property
    def requires_exit_confirmation(self) -> bool:
        return self.has_unsaved_changes or self._reload_recovery_required

    def _refresh_dirty_state(self) -> None:
        if self._loading_config or self._saved_form_state is None:
            return
        dirty = self.has_unsaved_changes
        recovery_pending = (
            self._reload_recovery_required or self._reload_before_next_entry
        )
        if recovery_pending:
            self.save_hint.value = "⚠ 設定已寫入，但畫面尚未同步；請重新載入設定"
            self.save_hint.color = C.GOLD
            self.save_button.content = "重新載入設定"
            self.save_button.tooltip = "重試從 config.json 載入設定並同步畫面"
        else:
            self.save_hint.value = (
                "⚠ 設定尚未儲存，記得按「儲存變更」"
                if dirty
                else "提示：修改後請務必點擊儲存"
            )
            self.save_hint.color = C.GOLD if dirty else C.MUTED
            self.save_button.content = "儲存變更" if dirty else "儲存所有設定"
            self.save_button.tooltip = (
                "設定尚未儲存；按此寫入 config.json"
                if dirty
                else "寫入 config.json（請確認 API Keys 有填好）"
            )
        self.page.update()

    def discard_unsaved_changes(self) -> None:
        self.load_config()

    @property
    def page(self):
        return self._page

    def set_registry(self, registry):
        """儲存 registry 參考，讓 save_config_clicked 能廣播到其他 views"""
        self._registry = registry
