"""UI for the two explicit repair modes on the Mod DB translation page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from app.ui import kit
from app.ui.design import C


@dataclass(frozen=True)
class RepairCardControls:
    card: ft.Control
    mode_group: ft.RadioGroup
    preview_button: ft.Control
    start_button: ft.Control
    preview_text: ft.Text
    explainer: ft.Text
    samples: ft.Column
    summary: ft.Text


def repair_copy(mode: str) -> tuple[str, str]:
    """Return concise condition and scope explanations for the selected mode."""
    if mode == "quality_mismatch":
        return (
            "檢查資料庫各來源譯文中，§ 格式碼、換行、佔位符或 Patchouli 標記不一致的項目。",
            (
                "會檢查人工、模組自帶、匯入 ZIP、自訂及 AI 等所有來源的資料庫譯文。"
                "檢查 § 格式碼、換行、佔位符與 Patchouli 標記。AI 修復通過檢查後只更新該來源；"
                "失敗或仍不一致時保留舊譯文。人工來源會改為未審核。"
                "這只改 Mod 資料庫，不會改寫原始 ZIP。"
            ),
        )
    return (
        "檢查目前生效來源為 AI 機翻，且譯文與原文完全相同的項目。",
        (
            "只處理目前生效來源為 AI 機翻、且譯文與原文完全相同的項目。"
            "會略過舊快取，只更新 AI 來源，不同步其他版本；一般機翻的同文重試仍依設定執行。"
        ),
    )


def create_repair_card(
    mode: str,
    *,
    on_mode_change: Callable,
    on_preview: Callable,
    on_confirm: Callable,
) -> RepairCardControls:
    """Build the repair mode selector, scope notes, preview and confirmation controls."""
    mode_group = ft.RadioGroup(
        content=ft.Row(
            [
                ft.Radio(label="特殊字元不一致", value="quality_mismatch"),
                ft.Radio(label="AI 譯文與原文相同", value="same_source_ai"),
            ],
            spacing=18,
            wrap=True,
        ),
        value=mode,
        on_change=on_mode_change,
    )
    preview_button = kit.button(
        "預覽候選譯文",
        "secondary",
        icon=ft.Icons.VISIBILITY_OUTLINED,
        on_click=on_preview,
    )
    start_button = kit.button(
        "開始修復",
        "primary",
        icon=ft.Icons.AUTO_AWESOME,
        on_click=on_confirm,
    )
    default_text, explanation = repair_copy(mode)
    preview_text = ft.Text(default_text, size=13, color=C.TEXT, selectable=True)
    explainer = kit.hint_text(explanation)
    samples = ft.Column(spacing=4)
    summary = ft.Text(
        "預覽只列候選、不呼叫 AI；按「開始修復」才會呼叫 AI。只儲存通過特殊字元檢查的結果，"
        "未通過就保留舊譯文。版本、模組、上限或條件變更後需重新預覽。",
        size=12.5,
        color=C.MUTED,
        selectable=True,
    )
    card = kit.section_card(
        "3　修復既有譯文",
        ft.Column(
            [
                mode_group,
                explainer,
                ft.Row([preview_button, start_button], spacing=10, wrap=True),
                preview_text,
                samples,
                summary,
            ],
            spacing=10,
        ),
        icon=ft.Icons.BUILD_CIRCLE_OUTLINED,
        tone="gold",
    )
    return RepairCardControls(
        card,
        mode_group,
        preview_button,
        start_button,
        preview_text,
        explainer,
        samples,
        summary,
    )
