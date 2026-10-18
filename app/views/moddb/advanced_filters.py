"""Collapsed time, translation-quality and ordering controls for Mod DB entries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import flet as ft

from app.services_impl.moddb_service import (
    QualityFilter,
    TimeFilter,
    custom_date_bounds,
    quick_date_bounds,
)
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack


def picked_date(value: date | datetime) -> date:
    """日曆回傳的時間是 UTC（台灣的 10/11 00:00 會是 10/10 16:00Z），要換回本地日期。"""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.date()
    offset = value.utcoffset()
    if offset is not None and not offset and value.time() == datetime.min.time():
        return value.date()  # 已經是「當天 UTC 零點」，直接就是選到的日期
    return value.astimezone().date()


class AdvancedFilters:
    def __init__(self, on_change: Callable[[], None]):
        self._on_change = on_change
        self._time_bounds: tuple[str, str] | None = None
        self._sort_desc = False
        self._last_sort = "default"
        self._build_time_controls()
        self._build_quality_controls()
        self._build_sort_control()
        self._build_panel()

    def _build_time_controls(self) -> None:
        self.toggle_button = kit.button(
            "進階篩選", "ghost", size="sm", on_click=self._toggle
        )
        self.active_text = ft.Text("", size=11.5, color=C.MUTED, expand=True)
        self.error_text = ft.Text("", size=11.5, color=C.RED, visible=False)
        self.time_kind = self._dropdown(
            "時間依據",
            [
                ("none", "不限時間"),
                ("entry_created", "條目匯入時間"),
                ("translation_created", "來源首次匯入時間"),
                ("effective_updated", "目前譯文更新時間"),
                ("manual_activity", "人工操作時間"),
            ],
            "none",
        )
        self.time_preset = self._dropdown(
            "日期範圍",
            [
                ("all", "不限"),
                ("today", "今天"),
                ("yesterday", "昨天"),
                ("7d", "近 7 天"),
                ("30d", "近 30 天"),
                ("custom", "自訂日期"),
            ],
            "all",
        )
        self.start_date = kit.text_field(
            "起始日", hint="YYYY-MM-DD", width=150, on_submit=self._custom_changed
        )
        self.end_date = kit.text_field(
            "結束日", hint="YYYY-MM-DD", width=150, on_submit=self._custom_changed
        )
        self.start_pick = kit.button(
            "選起始日",
            "ghost",
            size="sm",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda _e: self._pick_date(self.start_date, "起始日"),
            tooltip="用日曆選日期，不必手動輸入",
        )
        self.end_pick = kit.button(
            "選結束日",
            "ghost",
            size="sm",
            icon=ft.Icons.CALENDAR_MONTH,
            on_click=lambda _e: self._pick_date(self.end_date, "結束日"),
            tooltip="用日曆選日期，不必手動輸入",
        )
        self.unknown_policy = self._dropdown(
            "未知日期",
            [("include", "包含未知"), ("exclude", "排除未知"), ("only", "只看未知")],
            "include",
        )
        self.activity_action = self._dropdown(
            "操作類型",
            [
                ("all", "所有人工操作"),
                ("edit", "手動儲存"),
                ("review", "審核"),
                ("batch", "批次操作"),
                ("revert", "還原"),
            ],
            "all",
        )

    def _build_quality_controls(self) -> None:
        self.quality_status = self._dropdown(
            "品質狀態",
            [
                ("all", "不限品質"),
                ("mismatch", "特殊字元不一致"),
                ("consistent", "特殊字元相符"),
                ("whitespace", "前後空白或換行"),
                ("unknown_source", "原文未知"),
                ("missing_translation", "尚無譯文"),
                ("species_name", "疑似學名文字"),
            ],
            "all",
        )
        self.direction = self._dropdown(
            "不一致方向",
            [("all", "多字或少字"), ("missing", "少了 token"), ("extra", "多了 token")],
            "all",
        )
        self.category = self._dropdown(
            "token 類別",
            [
                ("all", "所有類別"),
                ("placeholder", "佔位符"),
                ("minecraft", "Minecraft 色碼"),
                ("patchouli", "Patchouli 提示"),
                ("newline", "換行"),
            ],
            "all",
        )

    def _build_sort_control(self) -> None:
        self.sort = self._dropdown(
            "排序",
            [
                ("default", "模組／鍵值"),
                ("entry_newest", "依匯入時間"),
                ("effective_updated_newest", "依譯文更新時間"),
                ("manual_activity_newest", "依人工操作時間"),
            ],
            "default",
        )
        self.sort_direction = kit.button(
            "",
            "ghost",
            size="sm",
            on_click=self._toggle_sort_direction,
            tooltip="切換日期排序方向",
        )
        self._refresh_sort_direction()

    def _build_panel(self) -> None:
        for control in (
            self.time_kind,
            self.time_preset,
            self.unknown_policy,
            self.activity_action,
            self.quality_status,
            self.direction,
            self.category,
            self.sort,
        ):
            control.on_select = self._selection_changed
        self.panel = ft.Column(
            [
                ft.Row(
                    [
                        self.time_kind,
                        self.time_preset,
                        self.start_date,
                        self.start_pick,
                        self.end_date,
                        self.end_pick,
                    ],
                    spacing=8,
                    wrap=True,
                ),
                ft.Row(
                    [self.unknown_policy, self.activity_action, self.quality_status],
                    spacing=8,
                    wrap=True,
                ),
                ft.Row(
                    [self.direction, self.category, self.sort, self.sort_direction],
                    spacing=8,
                    wrap=True,
                ),
                self.error_text,
            ],
            spacing=8,
            visible=False,
        )

    @staticmethod
    def _dropdown(
        label: str, options: list[tuple[str, str]], value: str
    ) -> ft.Dropdown:
        control = kit.dropdown(
            label=label, dense=True, width=185, value=value, options=[]
        )
        kit.set_dropdown_options(control, options)
        return control

    def _toggle(self, _e=None) -> None:
        self.panel.visible = not self.panel.visible
        self.toggle_button.text = "收合進階篩選" if self.panel.visible else "進階篩選"

    def _pick_date(self, field: ft.TextField, label: str) -> None:
        """用日曆選日期；選完填入欄位並立即套用篩選。"""
        page = self.panel.page
        try:
            initial = date.fromisoformat((field.value or "").strip())
        except ValueError:
            initial = None

        def chosen(event: ft.ControlEvent) -> None:
            value = event.control.value
            if not isinstance(value, date):
                return
            field.value = picked_date(value).isoformat()
            auto_basis = self._ensure_time_kind()
            note = (
                "；「時間依據」原本是不限時間，已自動設為「條目匯入時間」，可在左邊更改"
                if auto_basis
                else ""
            )
            self.time_preset.value = "custom"
            page.update()
            if not (self.start_date.value and self.end_date.value):
                # 自訂區間需要起訖兩個日期；另一個還沒選時不套用，也不顯示錯誤
                other = "結束日" if field is self.start_date else "起始日"
                show_snack(
                    page,
                    f"已選擇{label} {field.value}，還需要選擇{other}，兩個日期都選好才會套用篩選{note}",
                    C.GOLD,
                )
                return
            self._custom_changed()
            if not self.error_text.visible:
                show_snack(page, f"已套用{label} {field.value}{note}", C.EM)

        locale = ft.Locale("zh", "TW")
        configuration = page.locale_configuration or ft.LocaleConfiguration()
        supported = list(configuration.supported_locales or [])
        if not any(str(item) == str(locale) for item in supported):
            # 日曆的月份與星期要中文，Flutter 需要該語系先列入支援清單。
            configuration.supported_locales = [*supported, locale]
            page.locale_configuration = configuration
        page.show_dialog(
            ft.DatePicker(
                locale=locale,
                value=initial,
                first_date=date(2015, 1, 1),
                last_date=date(2100, 12, 31),
                help_text=f"選擇{label}",
                confirm_text="確定",
                cancel_text="取消",
                on_change=chosen,
            )
        )

    def _direction_names(self) -> tuple[str, str]:
        """(遞減, 遞增) 的完整說明；模組／鍵值依字母，其他依日期。"""
        if str(self.sort.value or "default") == "default":
            return "遞減（Z→A，模組與鍵值由後往前）", "遞增（A→Z，模組與鍵值由前往後）"
        return "遞減（新→舊，最近的在最上面）", "遞增（舊→新，最早的在最上面）"

    def _refresh_sort_direction(self) -> None:
        desc, asc = self._direction_names()
        current, other = (desc, asc) if self._sort_desc else (asc, desc)
        self.sort_direction.text = "遞減 ↓" if self._sort_desc else "遞增 ↑"
        self.sort_direction.icon = (
            ft.Icons.ARROW_DOWNWARD if self._sort_desc else ft.Icons.ARROW_UPWARD
        )
        self.sort_direction.tooltip = (
            f"目前排序方向：{current}。點一下切換為{other}。"
            "方向套用在左邊「排序」所選的依據上。"
        )

    def _toggle_sort_direction(self, _e=None) -> None:
        page = self.panel.page
        self._sort_desc = not self._sort_desc
        self._refresh_sort_direction()
        self.sort_direction.update()
        self._on_change()
        desc, asc = self._direction_names()
        show_snack(
            page,
            f"排序已改為{desc if self._sort_desc else asc}；清單已依新方向重新載入",
            C.EM,
        )

    def _selection_changed(self, _e=None) -> None:
        current_sort = str(self.sort.value or "default")
        if current_sort != self._last_sort:
            # 換排序依據時回到該依據的自然方向：模組／鍵值由 A 到 Z，日期由新到舊
            self._last_sort = current_sort
            self._sort_desc = current_sort != "default"
        self._refresh_sort_direction()
        if self.time_preset.value == "custom":
            self._time_bounds = None
            self.error_text.visible = False
            self.error_text.value = ""
        else:
            try:
                self._time_bounds = quick_date_bounds(
                    str(self.time_preset.value or "all")
                )
                self.error_text.visible = False
                self.error_text.value = ""
            except ValueError as exc:
                self.error_text.visible = True
                self.error_text.value = str(exc)
        self._on_change()

    def _ensure_time_kind(self) -> bool:
        """日期條件需要「時間依據」才會生效；沒選時自動用條目匯入時間。"""
        if str(self.time_kind.value or "none") != "none":
            return False
        if not (self.start_date.value or self.end_date.value):
            return False
        self.time_kind.value = "entry_created"
        return True

    def _custom_changed(self, _e=None) -> None:
        self.time_preset.value = "custom"
        self._ensure_time_kind()
        try:
            self._time_bounds = custom_date_bounds(
                self.start_date.value or "", self.end_date.value or ""
            )
            self.error_text.visible = False
            self.error_text.value = ""
        except ValueError as exc:
            self._time_bounds = None
            self.error_text.visible = True
            self.error_text.value = str(exc)
        self._on_change()

    def reset(self) -> None:
        """Clear all advanced conditions without triggering a database refresh."""
        self.time_kind.value = "none"
        self.time_preset.value = "all"
        self.start_date.value = ""
        self.end_date.value = ""
        self.unknown_policy.value = "include"
        self.activity_action.value = "all"
        self.quality_status.value = "all"
        self.direction.value = "all"
        self.category.value = "all"
        self.sort.value = "default"
        self._sort_desc = False
        self._last_sort = "default"
        self._refresh_sort_direction()
        self._time_bounds = None
        self.error_text.value = ""
        self.error_text.visible = False
        self.set_summary()

    def time_filter(self) -> TimeFilter | None:
        # Invalid custom dates remain an error even if another filter control is
        # changed or the time-kind dropdown is temporarily set to "none".
        if self.time_preset.value == "custom" and self.error_text.visible:
            raise ValueError(self.error_text.value)
        kind = str(self.time_kind.value or "none")
        if self.time_preset.value == "custom":
            bounds = custom_date_bounds(
                self.start_date.value or "", self.end_date.value or ""
            )
        elif self.time_preset.value == "all":
            bounds = None
        else:
            bounds = self._time_bounds
        if self.error_text.visible:
            raise ValueError(self.error_text.value)
        if kind == "none":
            return None
        unknown_policy = (
            str(self.unknown_policy.value or "include")
            if kind == "translation_created"
            else "exclude"
        )
        if (
            bounds is None
            and kind != "manual_activity"
            and not (kind == "translation_created" and unknown_policy != "include")
        ):
            return None
        start, end = bounds if bounds else (None, None)
        return TimeFilter(
            kind=kind,
            start_utc=start,
            end_utc=end,
            unknown_policy=unknown_policy,
            action=(
                str(self.activity_action.value or "all")
                if kind == "manual_activity"
                else "all"
            ),
        )

    def quality_filter(self) -> QualityFilter:
        return QualityFilter(
            status=str(self.quality_status.value or "all"),
            direction=str(self.direction.value or "all"),
            token_category=str(self.category.value or "all"),
        )

    @property
    def sort_by(self) -> str:
        value = str(self.sort.value or "default")
        # 下拉選單被程式直接改值、還沒走 _selection_changed 時，方向用該依據的預設方向
        descending = self._sort_desc if value == self._last_sort else value != "default"
        if value == "default":
            return "default_desc" if descending else "default"
        if not descending:
            return value.replace("_newest", "_oldest")
        return value

    def set_summary(self) -> int:
        count = 0
        try:
            count += int(self.time_filter() is not None)
        except ValueError:
            count += 1
        if self.quality_filter().active:
            count += 1
        if self.sort_by != "default":
            count += 1
        self.toggle_button.text = (
            f"進階篩選（{count}）" if not self.panel.visible else "收合進階篩選"
        )
        self.active_text.value = f"已套用 {count} 項進階條件" if count else ""
        return count
