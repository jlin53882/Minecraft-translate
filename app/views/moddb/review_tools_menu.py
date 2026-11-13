"""條目校對工具列的「審核工具」選單：把批量審核、版本差異審核等操作收成一個選單按鈕。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import flet as ft

from app.ui import design
from app.ui.design import C

TOOL_BULK_REVIEW = "bulk_review"
TOOL_VERSION_DIFF = "version_diff"
TOOL_SAME_TEXT = "same_text"
TOOL_SMART_REVIEW = "smart_review"
TOOL_SPECIES_SCAN = "species_scan"
TOOL_BATCH_REPLACE = "batch_replace"


@dataclass(frozen=True)
class ToolSpec:
    key: str
    label: str
    icon: str
    tooltip: str


# 選單分兩組（中間以分隔線隔開）：審核類、輔助與取代工具
REVIEW_GROUP = (
    ToolSpec(
        TOOL_BULK_REVIEW,
        "批量審核",
        ft.Icons.VERIFIED_OUTLINED,
        "依範圍與品質條件把譯文批次標為已審核",
    ),
    ToolSpec(
        TOOL_VERSION_DIFF,
        "版本差異審核",
        ft.Icons.COMPARE_ARROWS,
        "比較其他版本已審核的相同內容，套用或保留目標譯文",
    ),
    ToolSpec(
        TOOL_SAME_TEXT,
        "同文一致性審核",
        ft.Icons.RULE,
        "找出原文相同但譯文不一致的條目，統一成已審核的譯文",
    ),
)
HELPER_GROUP = (
    ToolSpec(
        TOOL_SMART_REVIEW,
        "智慧校對",
        ft.Icons.AUTO_AWESOME,
        "以本地 AI 產生未審核譯文的人工校對候選",
    ),
    ToolSpec(
        TOOL_SPECIES_SCAN,
        "掃描學名",
        ft.Icons.SCIENCE_OUTLINED,
        "掃描目前版本／模組／類型範圍中的英文原文，找出可能的學名",
    ),
    ToolSpec(
        TOOL_BATCH_REPLACE,
        "批次取代",
        ft.Icons.FIND_REPLACE,
        "依目前篩選條件批次取代譯文中的文字",
    ),
)


class ReviewToolsMenu:
    """「審核工具 ▾」選單按鈕：外觀同 kit 的 secondary／sm 按鈕，項目分組並以分隔線隔開。

    停用時整個選單按鈕與所有項目一起停用（資料載入中或清單有錯誤）。
    """

    def __init__(self, handlers: Mapping[str, Callable]) -> None:
        self.items: dict[str, ft.PopupMenuItem] = {}
        self._face = ft.Container(
            height=30,
            padding=ft.Padding.symmetric(horizontal=14),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, C.LINE2),
            border_radius=design.RADIUS_CONTROL,
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.FACT_CHECK_OUTLINED, size=16, color=C.TEXT),
                    ft.Text(
                        "審核工具", size=12, weight=ft.FontWeight.W_500, color=C.TEXT
                    ),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=C.MUTED),
                ],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        entries: list[ft.PopupMenuItem] = []
        for index, group in enumerate((REVIEW_GROUP, HELPER_GROUP)):
            if index:
                entries.append(self._divider())
            for spec in group:
                item = ft.PopupMenuItem(
                    content=ft.Text(spec.label, size=13),
                    icon=spec.icon,
                    tooltip=spec.tooltip,
                    on_click=handlers[spec.key],
                )
                self.items[spec.key] = item
                entries.append(item)
        self.control = ft.PopupMenuButton(
            content=self._face, items=entries, tooltip="審核與批次處理工具"
        )

    @staticmethod
    def _divider() -> ft.PopupMenuItem:
        return ft.PopupMenuItem(
            content=ft.Divider(height=1, color=C.LINE),
            height=8,
            padding=ft.Padding.symmetric(horizontal=8),
            disabled=True,
        )

    @property
    def disabled(self) -> bool:
        return bool(self.control.disabled)

    def set_disabled(self, disabled: bool) -> bool:
        """停用或啟用整個選單；回傳狀態是否改變（改變時呼叫端要重送 ``control``）。"""
        if self.control.disabled == disabled:
            return False
        self.control.disabled = disabled
        self._face.opacity = 0.5 if disabled else 1.0
        for item in self.items.values():
            item.disabled = disabled
        return True
