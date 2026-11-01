"""KubeJS／FTB 一鍵執行精靈：與模組包一鍵製作相同的 1/4 → 4/4 對話框。

精靈持有所有執行設定（候選根目錄、遊戲版本、同步模式、來源類型、LM 翻譯、overlay 輸出目錄），
頁面只保留來源路徑與步驟按鈕。第 4 步「開始預覽」只負責啟動唯讀預覽；預覽成功後仍由
流水線頁的「確認發布」對話框決定是否寫入 overlay。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.dialogs import close_page_dialog, present_page_dialog
from app.ui.snack import show_snack
from app.views.pipeline.pipeline_forms import build_pipeline_dialog, dialog_button
from app.views.pipeline.pipeline_workbench import build_pipeline_input_row

if TYPE_CHECKING:  # pragma: no cover - 僅供型別標註
    from app.views.kubejs_ftb_pipeline_view import KubejsFtbPipelineView

STEP_COUNT = 4
_STEP_TITLES = ("來源與版本", "翻譯設定", "輸出目的地", "確認並預覽")


class KubejsFtbWizard:
    """一鍵執行精靈；每次切換步驟都重建對話框內容，與模組包一鍵製作的做法一致。"""

    def __init__(self, view: KubejsFtbPipelineView) -> None:
        self._view = view
        self._step = 1
        self._dialog: ft.AlertDialog | None = None

    # ------------------------------------------------------------ 開啟／關閉

    def open(self) -> None:
        """從第 1 步開始顯示精靈。"""
        self._step = 1
        self._show(first=True)

    def _close(self, _e=None) -> None:
        if self._dialog is not None:
            close_page_dialog(self._view._page, self._dialog)
        self._dialog = None

    # ------------------------------------------------------------ 畫面

    def _show(self, *, first: bool = False) -> None:
        page = self._view._page
        new_dialog = build_pipeline_dialog(
            page,
            title=_STEP_TITLES[self._step - 1],
            content=self._step_content(),
            actions=self._step_actions(),
            step_label=ft.Text(
                f"{self._step}/{STEP_COUNT}",
                size=12,
                color=C.MUTED,
                weight=ft.FontWeight.W_500,
            ),
            icon=ft.Icons.FLASH_ON,
            tone="gold",
        )
        if self._dialog is not None:
            close_page_dialog(page, self._dialog)
        self._dialog = new_dialog
        present_page_dialog(page, new_dialog)

    def _step_content(self) -> ft.Control:
        builders = {
            1: self._content_source,
            2: self._content_translate,
            3: self._content_output,
            4: self._content_summary,
        }
        return builders[self._step]()

    def _step_actions(self) -> list[ft.Control]:
        if self._step == 1:
            return [
                dialog_button("取消", self._close),
                dialog_button("下一步", self._next, role="primary"),
            ]
        if self._step == STEP_COUNT:
            return [
                dialog_button("上一步", self._back),
                dialog_button(
                    "開始預覽", self._finish, role="primary", icon=ft.Icons.SEARCH
                ),
            ]
        return [
            dialog_button("上一步", self._back),
            dialog_button("下一步", self._next, role="primary"),
        ]

    # ------------------------------------------------------------ 各步驟內容

    def _content_source(self) -> ft.Control:
        view = self._view
        return ft.Column(
            [
                kit.section_label("來源路徑（在主頁面設定）"),
                ft.Text(
                    view.source_field.value or "尚未選擇來源",
                    size=13,
                    color=C.TEXT,
                    selectable=True,
                ),
                view.root_dropdown,
                kit.hint_text(
                    "多個候選時必須明確選擇；尚未分析來源時請先在主頁面按「分析來源」。"
                ),
                ft.Row([view.version_field, view.scan_mode], spacing=12, wrap=True),
                ft.Row([view.include_kubejs, view.include_ftb], spacing=16, wrap=True),
            ],
            spacing=12,
            tight=True,
        )

    def _content_translate(self) -> ft.Control:
        view = self._view
        return ft.Column(
            [
                view.translate_missing,
                kit.hint_text(
                    "開啟後，同步 ModDB 之後會翻譯待處理條目（需要 LM 設定）；預覽不會翻譯。"
                ),
            ],
            spacing=12,
            tight=True,
        )

    def _content_output(self) -> ft.Control:
        view = self._view
        return ft.Column(
            [
                kit.section_label("Overlay 輸出目錄（正式發布必填）"),
                build_pipeline_input_row(
                    view.output_field,
                    kit.pick_button(
                        ft.Icons.FOLDER_SPECIAL_OUTLINED,
                        "選擇 overlay 輸出目錄",
                        view._pick_output,
                    ),
                ),
                kit.hint_text(
                    "輸出會覆蓋模組包根目錄內的 kubejs／config/ftbquests overlay 檔案，只在確認發布後寫入。"
                ),
            ],
            spacing=12,
            tight=True,
        )

    def _content_summary(self) -> ft.Control:
        view = self._view
        rows = [
            ("來源", view.source_field.value or "—"),
            ("有效來源根目錄", view.root_dropdown.value or "—"),
            ("遊戲版本", view.version_field.value or "—"),
            ("同步模式", "完整掃描" if view.scan_mode.value == "full" else "增量同步"),
            ("來源類型", _source_label(view)),
            ("LM 翻譯待處理", "開啟" if view.translate_missing.value else "未啟用"),
            ("Overlay 輸出目錄", view.output_field.value or "—"),
        ]
        return ft.Column(
            [
                ft.Text(f"{label}：{value}", size=13, color=C.TEXT, selectable=True)
                for label, value in rows
            ]
            + [
                kit.hint_text(
                    "按「開始預覽」只會唯讀掃描；預覽完成後會再確認是否發布 overlay。"
                )
            ],
            spacing=10,
            tight=True,
        )

    # ------------------------------------------------------------ 導覽與驗證

    def _next(self, _e=None) -> None:
        problem = self._step_problem(self._step)
        if problem is not None:
            show_snack(self._view._page, problem[0], problem[1])
            return
        self._step += 1
        self._show()

    def _back(self, _e=None) -> None:
        self._step -= 1
        self._show()

    def _finish(self, _e=None) -> None:
        self._close()
        self._view._begin_one_click_preview()

    def _step_problem(self, step: int) -> tuple[str, str] | None:
        view = self._view
        if step == 1:
            if not view._candidates:
                return "請先在主頁面按「分析來源」", C.GOLD
            if len(view._candidates) > 1 and not view.root_dropdown.value:
                return "來源有多個候選，請先選擇有效來源根目錄", C.GOLD
            if not (view.include_kubejs.value or view.include_ftb.value):
                return "至少選擇 KubeJS 或 FTB Quests", C.RED
        if step == 3 and not (view.output_field.value or "").strip():
            return "一鍵執行會發布 overlay，請先填寫輸出目錄", C.RED
        return None


def _source_label(view: KubejsFtbPipelineView) -> str:
    kinds = []
    if view.include_kubejs.value:
        kinds.append("KubeJS")
    if view.include_ftb.value:
        kinds.append("FTB Quests")
    return "、".join(kinds) or "—"


__all__ = ["STEP_COUNT", "KubejsFtbWizard"]
