"""app/shell/statusbar.py：底部狀態列（連線 / 目前任務、模型、工作資料夾、版本）。"""

from __future__ import annotations

import flet as ft

from app.ui import design, kit
from app.ui.design import C
from app.ui.design import tone as get_tone

STATUSBAR_HEIGHT = 30


def _item(icon: str | None, text_control: ft.Text, *, divider: bool = False) -> ft.Row:
    """一個狀態項目；``divider`` 時在左側帶一條細線（項目隱藏時線也跟著隱藏）。"""
    parts: list[ft.Control] = []
    if divider:
        parts.append(kit.vdivider(12))
    if icon:
        parts.append(ft.Icon(icon, size=13, color=C.DIM))
    parts.append(text_control)
    return ft.Row(parts, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def _text(value: str = "", *, mono: bool = False) -> ft.Text:
    return ft.Text(
        value,
        size=11.5,
        color=C.MUTED,
        no_wrap=True,
        overflow=ft.TextOverflow.ELLIPSIS,
        font_family=design.FONT_MONO if mono else None,
    )


class StatusBar(ft.Container):
    """狀態列。內容全部由外殼用 ``set_*`` 推進來。"""

    def __init__(self, version: str = "") -> None:
        self.dot = ft.Container(width=7, height=7, border_radius=4)
        self.status = _text("就緒")
        self.model = _text("")
        self.workdir = _text("", mono=True)
        self.version = _text(f"v{version}" if version else "", mono=True)
        self._model_item = _item(
            ft.Icons.AUTO_AWESOME_OUTLINED, self.model, divider=True
        )
        self._workdir_item = _item(ft.Icons.FOLDER_OPEN, self.workdir, divider=True)
        super().__init__(
            height=STATUSBAR_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=18),
            bgcolor=C.SIDE,
            border=ft.Border.only(top=ft.BorderSide(1, C.LINE)),
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Row(
                                [self.dot, self.status],
                                spacing=7,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            self._model_item,
                            self._workdir_item,
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
                    self.version,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self.set_status("就緒", "em")
        self.set_model(None)
        self.set_workdir(None)

    def set_status(self, text: str, tone: str = "em") -> None:
        self.status.value = text
        self.dot.bgcolor = get_tone(tone).fg

    def set_model(self, name: str | None) -> None:
        self.model.value = name or ""
        self._model_item.visible = bool(name)

    def set_workdir(self, path: str | None) -> None:
        self.workdir.value = path or ""
        self._workdir_item.visible = bool(path)
