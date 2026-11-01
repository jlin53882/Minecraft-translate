"""流水線進度面板與步驟標籤。"""

from typing import ClassVar

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.views._log import LogView
from app.views.pipeline.pipeline_workbench import (
    PipelineStepSpec,
    build_pipeline_status_card,
    build_pipeline_step_row,
)


class PipelineStepChip:
    """單一步驟狀態晶片（語意色組：灰=等待、金=進行中、綠=完成、紅=失敗 / 取消）。"""

    _TONES: ClassVar[dict[str, str]] = {
        "waiting": "neutral",
        "running": "gold",
        "done": "em",
        "failed": "red",
        "cancelled": "gold",
    }
    _ICONS: ClassVar[dict[str, str]] = {
        "waiting": ft.Icons.CIRCLE,
        "running": ft.Icons.PENDING,
        "done": ft.Icons.CHECK_CIRCLE,
        "failed": ft.Icons.ERROR,
        "cancelled": ft.Icons.STOP_CIRCLE,
    }

    def __init__(self, name: str, step_num: int):
        self.name = name
        self.step_num = step_num
        self.status = "waiting"

        # ft.Chip 沒有 on_click／on_select 時 Flutter 視為停用並整個淡化，
        # 狀態色就看不出來；改用一般容器做的膠囊。
        self.label = ft.Text(f"{step_num}. {name}", size=12.5, no_wrap=True)
        self.icon = ft.Icon(ft.Icons.CIRCLE, size=12)
        self.chip = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=16,
            content=ft.Row(
                [self.icon, self.label],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self._update_chip()

    def _update_chip(self):
        tone = get_tone(self._TONES.get(self.status, "neutral"))
        self.icon.name = self._ICONS[self.status]
        self.icon.color = tone.fg
        self.label.color = tone.fg
        self.chip.bgcolor = tone.bg
        self.chip.border = ft.Border.all(1, tone.line)

    def set_status(self, status: str):
        self.status = status
        self._update_chip()


class PipelineProgressPanel:
    """日誌+進度面板，顯示步驟狀態晶片、進度條、即時日誌"""

    def __init__(
        self,
        page: ft.Page,
        on_cancel=None,
        *,
        step_names: list[str] | None = None,
        log_height: int = 120,
        progress: ft.Control | None = None,
    ):
        self._page = page
        self.cancel_button = kit.button(
            "取消",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            tooltip="在目前步驟的檢查點停止（已完成的輸出會保留）",
            on_click=(lambda e: on_cancel()) if on_cancel else None,
        )
        self.cancel_button.visible = False
        names = step_names or ["抽取資源", "語系比對", "啟動翻譯", "打包資源"]
        self.steps = [
            build_pipeline_step_row(
                PipelineStepSpec(
                    number=index,
                    title=name,
                    description="",
                    icon=ft.Icons.TIMELINE,
                    tone="neutral",
                    status="waiting",
                )
            )
            for index, name in enumerate(names, start=1)
        ]
        self.current_step = None

        self.step_row = ft.Column(controls=self.steps, spacing=0)

        self.current_label = ft.Text("等待執行...", color=C.MUTED, size=14)
        self.progress = progress or kit.progress_bar(0, "gold", height=8)
        self.progress_status = self.current_label
        # PR refactor/unified-log-view: 改用 LogView widget
        # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
        # 保留 height=120 限制
        self.log_view = LogView(
            page=page,
            mode="append",
            max_lines=500,
            height=log_height,
        )

        self.container = build_pipeline_status_card(
            progress=self.progress,
            progress_status=self.progress_status,
            cancel=self.cancel_button,
            title="執行進度",
            icon=ft.Icons.TIMELINE,
            tone="gold",
            summary=ft.Column(
                [self.step_row, self.log_view],
                spacing=10,
            ),
        )
        self.container.visible = False
        self._idle_status_card: ft.Control | None = None

    def attach_idle_status_card(self, card: ft.Control) -> None:
        """連結執行前狀態卡，執行時避免同一欄位同時顯示兩張狀態卡。"""
        self._idle_status_card = card

    def _set_idle_status_visible(self, visible: bool) -> None:
        if self._idle_status_card is not None:
            self._idle_status_card.visible = visible

    def start(self):
        self._set_idle_status_visible(False)
        self.container.visible = True
        for step in self.steps:
            step.set_status("waiting")

    def set_step_running(self, step_num: int, name: str):
        self.current_step = step_num - 1
        for i, step in enumerate(self.steps):
            if i < self.current_step:
                step.set_status("done")
            elif i == self.current_step:
                step.set_status("running")
            else:
                step.set_status("waiting")
        self.current_label.value = f"目前的：{name}"

    def add_log(self, msg: str, level: str = "info", is_success: bool | None = None):
        """PR refactor/unified-log-view: 改用 LogView.add() 統一處理等級顏色。

        Args:
            msg: log 文字
            level: debug/info/warning/error/system（從字串前綴自動推斷）
            is_success: 保留向後兼容（True=success、False=error、None=預設）
        """
        # 向後兼容 is_success 參數（map 到 level）
        if is_success is not None and level == "info":
            level = "system" if is_success else "error"
        # 從 msg 字串前綴推斷 level（向後兼容既有呼叫）
        if level == "info":
            if msg.startswith(("▶", "✅")):
                level = "system"
            elif msg.startswith("❌"):
                level = "error"
        self.log_view.add(f">> {msg}", level=level, mirror_text=msg, dedupe=False)

    def finish_step(self, step_num: int, success: bool, cancelled: bool = False):
        if cancelled:
            self.steps[step_num - 1].set_status("cancelled")
        else:
            self.steps[step_num - 1].set_status("done" if success else "failed")

    def set_running(self, running: bool):
        """任務執行中才顯示取消按鈕。"""
        self.cancel_button.visible = running
        self.cancel_button.disabled = not running

    def finish_all(self, success: bool, cancelled: bool = False, dry_run: bool = False):
        if success:
            self.current_label.value = (
                "✅ 一鍵製作完成（翻譯為 Dry Run，未翻譯）"
                if dry_run
                else "✅ 一鍵製作完成！"
            )
        elif cancelled:
            self.current_label.value = "⏹ 已取消"
        else:
            self.current_label.value = "❌ 流程失敗"
        for step in self.steps:
            if step.status == "running":
                step.set_status("failed" if not success else "done")
        self.set_running(False)

    def hide(self):
        self.container.visible = False
        self._set_idle_status_visible(True)

    def clear_logs(self):
        # PR refactor/unified-log-view: log_view 改用 LogView
        self.log_view.clear()
