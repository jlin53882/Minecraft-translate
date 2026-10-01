"""kit/progress.py：進度條與環形進度。"""

from __future__ import annotations

import flet as ft

from app.ui import design
from app.ui.design import C
from app.ui.design import tone as get_tone


def clamp01(value: float | None) -> float | None:
    """進度值限制在 0~1；None 代表不確定（indeterminate）。"""
    if value is None:
        return None
    return min(max(float(value), 0.0), 1.0)


def progress_bar(
    value: float | None = 0.0, tone: str = "em", *, height: int = 6
) -> ft.ProgressBar:
    """長條進度。value 為 None 時是不確定進度（來回滾動）。"""
    return ft.ProgressBar(
        value=clamp01(value),
        color=get_tone(tone).fg,
        bgcolor=C.TRACK,
        bar_height=height,
        border_radius=height / 2,
        stop_indicator_radius=0,
        track_gap=0,
    )


class ProgressRing(ft.Stack):
    """環形進度：中央可放百分比與說明，可用 ``set_value`` 更新。"""

    def __init__(
        self,
        value: float = 0.0,
        *,
        size: int = 96,
        stroke: int = 9,
        tone: str = "em",
        label: str | None = None,
        sub: str | None = None,
    ) -> None:
        self.size = size
        self.ring = ft.ProgressRing(
            value=clamp01(value),
            width=size,
            height=size,
            stroke_width=stroke,
            stroke_cap=ft.StrokeCap.ROUND,
            color=get_tone(tone).fg,
            bgcolor=C.TRACK,
        )
        self.label = ft.Text(
            label if label is not None else self._percent(value),
            size=max(11, round(size / 4.2)),
            weight=ft.FontWeight.BOLD,
            color=C.TEXT,
            font_family=design.FONT_MONO,
        )
        self.sub = ft.Text(sub or "", size=10.5, color=C.DIM, visible=bool(sub))
        super().__init__(
            width=size,
            height=size,
            controls=[
                self.ring,
                ft.Container(
                    width=size,
                    height=size,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Column(
                        [self.label, self.sub],
                        spacing=1,
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ),
            ],
        )

    @staticmethod
    def _percent(value: float | None) -> str:
        v = clamp01(value)
        return "…" if v is None else f"{round(v * 100)}%"

    def set_value(
        self, value: float | None, *, label: str | None = None, sub: str | None = None
    ) -> None:
        self.ring.value = clamp01(value)
        self.label.value = label if label is not None else self._percent(value)
        if sub is not None:
            self.sub.value = sub
            self.sub.visible = bool(sub)

    def set_tone(self, tone: str) -> None:
        self.ring.color = get_tone(tone).fg
