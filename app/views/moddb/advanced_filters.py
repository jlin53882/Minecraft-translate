"""Collapsed time, translation-quality and ordering controls for Mod DB entries."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_service import (
    QualityFilter,
    TimeFilter,
    custom_date_bounds,
    quick_date_bounds,
)
from app.ui import kit
from app.ui.design import C


class AdvancedFilters:
    def __init__(self, on_change: Callable[[], None]):
        self._on_change = on_change
        self._time_bounds: tuple[str, str] | None = None
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
                ("entry_newest", "新匯入優先"),
                ("entry_oldest", "舊匯入優先"),
                ("effective_updated_newest", "譯文最近更新"),
                ("manual_activity_newest", "人工操作最近"),
            ],
            "default",
        )

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
                    [self.time_kind, self.time_preset, self.start_date, self.end_date],
                    spacing=8,
                    wrap=True,
                ),
                ft.Row(
                    [self.unknown_policy, self.activity_action, self.quality_status],
                    spacing=8,
                    wrap=True,
                ),
                ft.Row(
                    [self.direction, self.category, self.sort], spacing=8, wrap=True
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

    def _selection_changed(self, _e=None) -> None:
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

    def _custom_changed(self, _e=None) -> None:
        self.time_preset.value = "custom"
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
        return str(self.sort.value or "default")

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
