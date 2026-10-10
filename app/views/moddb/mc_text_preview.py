"""Read-only Minecraft-formatted preview for the Mod DB translation editor."""

from __future__ import annotations

import flet as ft

from app.ui import design
from app.ui.design import C
from app.ui.mc_text import mc_text_spans


class MinecraftTextPreview:
    """A labeled, always-visible preview that renders Minecraft formatting codes."""

    def __init__(self) -> None:
        self.text = ft.Text(
            "",
            size=14,
            selectable=True,
            color=design.MC_PREVIEW_TEXT,
            visible=False,
        )
        self.hint = ft.Text(
            "輸入譯文後，預覽會顯示在這裡。",
            size=12,
            color=design.MC_PREVIEW_HINT,
        )
        self.surface = ft.Container(
            ft.Column([self.text, self.hint], spacing=0, tight=True),
            padding=12,
            bgcolor=design.MC_PREVIEW_BG,
            border=ft.Border.all(1, design.MC_PREVIEW_LINE),
            border_radius=design.RADIUS_CONTROL,
        )
        self.control = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            "Minecraft 樣式預覽",
                            size=12.5,
                            weight=ft.FontWeight.W_500,
                            color=C.TEXT,
                        ),
                        ft.Text("即時更新・唯讀", size=11, color=C.MUTED),
                    ],
                    spacing=8,
                    wrap=True,
                ),
                self.surface,
            ],
            spacing=4,
            tight=True,
        )

    def render(self, value: str) -> None:
        """Render plain text and Minecraft codes, or explain why the preview is empty."""
        value = value or ""
        spans = mc_text_spans(value, design.MC_PREVIEW_TEXT, 14)
        visible_text = "".join(span.text or "" for span in spans).strip()
        self.text.value = ""
        self.text.spans = spans
        self.text.visible = bool(visible_text)
        self.hint.visible = not bool(visible_text)
        if visible_text:
            self.hint.value = ""
        elif not value.strip():
            self.hint.value = "輸入譯文後，預覽會顯示在這裡。"
        else:
            self.hint.value = "目前只有格式碼，沒有可顯示的文字。"
