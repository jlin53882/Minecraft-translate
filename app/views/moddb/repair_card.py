"""UI for the two explicit repair modes on the Mod DB translation page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from app.services_impl.moddb_translate_service import format_duration
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
    samples: ft.Control
    summary: ft.Text
    preview_search: ft.TextField
    preview_kind_filter: ft.Dropdown
    preview_source_filter: ft.Dropdown
    preview_issue_filter: ft.Dropdown
    preview_count: ft.Text
    pager: kit.Pager
    newline_row: kit.SwitchRow
    sync_row: kit.SwitchRow


# 修復與上方「1　選擇要機翻的範圍」共用同一組版本、模組、單次上限，以及「每次寫入新快取分片」；
# 「先沿用其他版本的相同譯文」只影響一般機翻，不影響修復。
SCOPE_HINT = (
    "修復的範圍沿用上方「1　選擇要機翻的範圍」的遊戲版本、模組與單次上限，"
    "AI 修復的快取寫入也遵循其中的「每次寫入新快取分片」；"
    "「類型」與「先沿用其他版本的相同譯文」只影響一般機翻（修復可在預覽中依類型篩選）。"
    "要換範圍請改上方設定後重新預覽。"
)


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
    on_filter_change: Callable,
    on_newline_change: Callable,
    on_page: Callable[[int], None],
    page_size: int,
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
        disabled=True,
    )
    scope_hint = kit.hint_text(SCOPE_HINT)
    default_text, explanation = repair_copy(mode)
    preview_text = ft.Text(default_text, size=13, color=C.TEXT, selectable=True)
    explainer = kit.hint_text(explanation)
    newline_row = kit.SwitchRow(
        "連「只有換行數不同」的項目一起送 AI 修復",
        "預設略過原文與譯文只差實際換行數的項目。開啟後這類項目也會送 AI，"
        "且 AI 結果的換行數必須與原文一致才會寫入。",
        False,
        on_change=on_newline_change,
    )
    newline_row.visible = mode == "quality_mismatch"
    sync_row = kit.SwitchRow(
        "修復後同步到其他版本",
        "同一模組、鍵值與原文的其他版本，若該來源的舊譯文跟這筆完全相同，"
        "一併改成新譯文（各自記錄歷史）。預設只修目前選的版本。",
        False,
    )
    sync_row.visible = mode == "quality_mismatch"
    preview_search = kit.text_field(
        "搜尋 Key／Mod／原文／譯文",
        icon=ft.Icons.SEARCH,
        on_change=on_filter_change,
        width=360,
    )
    preview_source_filter = kit.dropdown(
        label="翻譯來源",
        width=190,
        on_select=on_filter_change,
    )
    preview_kind_filter = kit.dropdown(
        label="內容類型",
        width=190,
        on_select=on_filter_change,
    )
    preview_issue_filter = kit.dropdown(
        label="異常類型",
        width=180,
        on_select=on_filter_change,
    )
    preview_count = ft.Text("尚未載入候選預覽。", size=12, color=C.MUTED)
    # The page already owns the scroll position.  Keeping a second scrollable
    # viewport here makes Flet Web retain a blank nested viewport after the
    # preview is populated, so let the outer TranslatePanel scroll the cards.
    samples = ft.Column(spacing=8, height=0, visible=False)
    pager = kit.Pager(0, page_size=page_size, on_change=on_page, unit="個 Key")
    pager.visible = False  # 候選不超過一頁時不顯示
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
                scope_hint,
                mode_group,
                explainer,
                newline_row,
                sync_row,
                ft.Row([preview_button, start_button], spacing=10, wrap=True),
                preview_text,
                ft.Row(
                    [
                        preview_search,
                        preview_kind_filter,
                        preview_source_filter,
                        preview_issue_filter,
                    ],
                    spacing=8,
                    wrap=True,
                ),
                preview_count,
                pager,
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
        preview_search,
        preview_kind_filter,
        preview_source_filter,
        preview_issue_filter,
        preview_count,
        pager,
        newline_row,
        sync_row,
    )


_PROGRESS_DEFAULTS = {
    "source": "0 / 0（0%）",
    "ai": "0 / 0",
    "batches": "0 / 0",
    "elapsed": "00:00",
    "eta": "計算中",
    "current": "等待開始",
}


def _progress_metric(title: str, value: ft.Text) -> ft.Column:
    return ft.Column(
        [ft.Text(title, size=11, color=C.MUTED), value], expand=True, spacing=2
    )


class RepairProgressPanel:
    """譯文格式修復執行中的進度面板：來源項目、AI 修復項目、規劃批次、目前執行與時間。

    只顯示修復專用的計數，不和一般機翻的統計卡混在一起。
    """

    def __init__(self) -> None:
        self.source = ft.Text(size=15, color=C.TEXT)
        self.ai = ft.Text(size=15, color=C.TEXT)
        self.batches = ft.Text(size=15, color=C.TEXT)
        self.elapsed = ft.Text(size=13, color=C.MUTED)
        self.eta = ft.Text(size=13, color=C.MUTED)
        self.current = ft.Text(size=13, color=C.TEXT, selectable=True)
        title = ft.Text("Mod 資料庫｜譯文格式修復", size=14, weight=ft.FontWeight.W_600)
        self.control = ft.Container(
            visible=False,
            padding=12,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=10,
            content=ft.Column(
                [
                    ft.Row(
                        [title, ft.Text("AI 翻譯中", size=12, color=C.EM)],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        [
                            _progress_metric("來源項目處理進度", self.source),
                            _progress_metric("AI 修復項目", self.ai),
                            _progress_metric("規劃批次", self.batches),
                        ],
                        spacing=16,
                    ),
                    ft.Row(
                        [ft.Text("目前執行：", size=11, color=C.MUTED), self.current],
                        spacing=4,
                    ),
                    ft.Row(
                        [
                            ft.Text("已耗時", size=11, color=C.MUTED),
                            self.elapsed,
                            ft.Text("　預估剩餘：", size=11, color=C.MUTED),
                            self.eta,
                        ],
                        spacing=2,
                    ),
                ],
                spacing=8,
            ),
        )
        self.reset()

    def reset(self) -> None:
        """隱藏面板並把所有欄位回到開始前的文字。"""
        self.control.visible = False
        for name, text in _PROGRESS_DEFAULTS.items():
            getattr(self, name).value = text

    def show_live(self, live: dict) -> None:
        """依修復服務回報的即時資料（``format_repair_live`` 的欄位）更新各欄。"""
        source_done = int(live.get("source_processed", live.get("processed", 0)) or 0)
        source_total = int(live.get("source_total", live.get("total", 0)) or 0)
        source_percent = 100 * source_done / max(source_total, 1)
        self.source.value = (
            f"{source_done:,} / {source_total:,}（{source_percent:.0f}%）"
        )
        self.ai.value = (
            f"{int(live.get('ai_completed', 0) or 0):,} / "
            f"{int(live.get('ai_total', 0) or 0):,}"
        )
        self.batches.value = (
            f"{int(live.get('planned_batches_done', live.get('batch_done', 0)) or 0):,} / "
            f"{int(live.get('planned_batches_total', live.get('batch_est', 0)) or 0):,}"
        )
        self.elapsed.value = format_duration(live.get("elapsed_sec"))
        eta = live.get("eta_sec")
        self.eta.value = format_duration(eta) if eta is not None else "計算中"
        profile = live.get("current_profile")
        batch = live.get("current_batch")
        status = live.get("current_status") or "等待開始"
        if profile and batch:
            current = (
                f"{status}｜{profile} 項目（第 {batch} / "
                f"{live.get('current_batch_total')} 批）"
            )
        else:
            current = str(status)
        if live.get("model_label"):
            current += f"｜模型：{live.get('model_label')}"
        self.current.value = current
