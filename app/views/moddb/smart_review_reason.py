"""智慧校對審查共用的 AI 理由與證據呈現元件。"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_smart_review_service import SmartReviewCandidate
from app.ui.design import C
from app.views.moddb.smart_review_evidence import SmartReviewEvidencePresenter


def reason_text(row: SmartReviewCandidate) -> str:
    """回傳候選的 AI 理由，沒有理由時也明確告知審查者。"""
    return row.reason or "這批候選沒有儲存 AI 理由。"


def issue_text(row: SmartReviewCandidate) -> str:
    """回傳候選標記的問題類型。"""
    return "、".join(row.issue_types) or "未標記"


def build_batch_text_line(label: str, value: str, *, main: bool = False) -> ft.Row:
    """建立批量卡片的原文／現況／建議摘要列。"""
    return ft.Row(
        [
            ft.Container(ft.Text(label, size=10.5, color=C.DIM), width=30),
            ft.Text(
                value or "（無內容）",
                size=13 if main else 12,
                color=C.TEXT if main else C.MUTED,
                selectable=True,
                expand=True,
                max_lines=3,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def build_evidence_tile(row: SmartReviewCandidate) -> ft.ExpansionTile:
    """建立預設收合的本地證據區，避免長證據擠走主要檢視內容。"""
    return ft.ExpansionTile(
        title=ft.Text("本地證據", size=12, color=C.MUTED),
        controls=[
            ft.Container(
                ft.Text(
                    SmartReviewEvidencePresenter.evidence_text(row),
                    size=12,
                    selectable=True,
                ),
                padding=ft.Padding.only(left=14, right=14, bottom=10),
            )
        ],
        expanded=False,
        tile_padding=ft.Padding.symmetric(horizontal=10),
    )


def build_ai_reason_panel(row: SmartReviewCandidate) -> ft.Container:
    """建立逐筆審查頂端的 AI 理由卡，理由與問題類型一眼可見。"""
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.AUTO_AWESOME, size=16, color=C.EM),
                        ft.Text("AI 理由說明", size=12, color=C.EM),
                    ],
                    spacing=6,
                ),
                ft.Text(reason_text(row), size=13, selectable=True),
                ft.Text(
                    f"問題類型：{issue_text(row)}",
                    size=12,
                    color=C.MUTED,
                    selectable=True,
                ),
                build_evidence_tile(row),
            ],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding.symmetric(horizontal=14, vertical=11),
        bgcolor=C.PANEL2,
        border=ft.Border.all(1, C.LINE),
        border_radius=10,
    )
