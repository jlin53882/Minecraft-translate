"""模組清單的一列：modid、總數／未翻譯文字、完成度長條與狀態晶片。

列控制項建立一次、換頁時只更新值（``bind``），Flet 只需送出變動的文字與顏色，
不必為每一頁重新建構、序列化數百個控制項。
"""

from collections.abc import Callable

import flet as ft

from app.ui import design
from app.ui.design import C, tone


class ModRow(ft.Container):
    """可重複綁定到不同模組的清單列；點擊時以目前綁定的 modid 呼叫 ``on_open``。"""

    def __init__(self, on_open: Callable[[str], None]):
        self.modid = ""
        self._on_open = on_open
        self._name = ft.Text(
            "",
            size=14,
            weight=ft.FontWeight.W_600,
            color=C.TEXT,
            font_family=design.FONT_MONO,
        )
        self._counts = ft.Text("", size=12, color=C.DIM)
        em = tone("em")
        self._progress = ft.ProgressBar(
            value=0.0,
            color=em.fg,
            bgcolor=C.TRACK,
            bar_height=5,
            border_radius=2.5,
            stop_indicator_radius=0,
            track_gap=0,
        )
        self._chip_text = ft.Text(
            "", size=11.5, weight=ft.FontWeight.W_500, color=em.fg, no_wrap=True
        )
        self._chip = ft.Container(
            height=22,
            padding=ft.Padding.symmetric(horizontal=9),
            border_radius=11,
            bgcolor=em.bg,
            border=ft.Border.all(1, em.line),
            content=ft.Row(
                [self._chip_text],
                spacing=5,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        super().__init__(
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL + 2,
            ink=True,
            on_click=lambda e: self._on_open(self.modid),
            content=ft.Row(
                [
                    ft.Column(
                        [self._name, self._counts],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                    ft.Container(width=120, content=self._progress),
                    self._chip,
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=18, color=C.DIM),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def bind(self, modid: str, total_count: int, untranslated: int | None) -> None:
        """顯示指定模組的筆數與完成度；``untranslated`` 為 None 表示掃描中、尚無譯文統計。"""
        if untranslated is None:
            self._bind_scanning(modid, total_count)
            return
        done = untranslated == 0
        palette = tone("em" if done else "gold")
        self.modid = modid
        self._name.value = modid
        self._counts.value = f"總數 {total_count} ｜ 未翻譯 {untranslated}"
        self._progress.value = 1.0 - (
            untranslated / total_count if total_count else 0.0
        )
        self._progress.color = palette.fg
        self._chip_text.value = "完成" if done else f"未翻譯 {untranslated}"
        self._chip_text.color = palette.fg
        self._chip.bgcolor = palette.bg
        self._chip.border = ft.Border.all(1, palette.line)

    def _bind_scanning(self, modid: str, total_count: int) -> None:
        palette = tone("neutral")
        self.modid = modid
        self._name.value = modid
        self._counts.value = f"總數 {total_count} ｜ 掃描中…"
        self._progress.value = 0.0
        self._chip_text.value = "掃描中"
        self._chip_text.color = palette.fg
        self._chip.bgcolor = palette.bg
        self._chip.border = ft.Border.all(1, palette.line)
