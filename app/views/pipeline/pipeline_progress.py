"""流水線進度面板與步驟標籤。"""

from typing import ClassVar

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.views._log import LogView


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

        self.chip = ft.Chip(label=ft.Text(f"{step_num}. {name}"))
        self.icon = ft.Icon(ft.Icons.CIRCLE, size=12)
        self.chip.leading = self.icon
        self._update_chip()

    def _update_chip(self):
        tone = get_tone(self._TONES.get(self.status, "neutral"))
        self.icon.name = self._ICONS[self.status]
        self.icon.color = tone.fg
        self.chip.bgcolor = tone.bg
        self.chip.label_text_style = ft.TextStyle(color=tone.fg, size=12.5)
        self.chip.side = ft.BorderSide(1, tone.line)

    def set_status(self, status: str):
        self.status = status
        self._update_chip()


class PipelineProgressPanel:
    """日誌+進度面板，顯示步驟狀態晶片、進度條、即時日誌"""

    def __init__(self, page: ft.Page, on_cancel=None):
        self._page = page
        self.cancel_button = kit.button(
            "取消",
            "danger",
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            tooltip="在目前步驟的檢查點停止（已完成的輸出會保留）",
            on_click=(lambda e: on_cancel()) if on_cancel else None,
        )
        self.cancel_button.visible = False
        self.steps = [
            PipelineStepChip("抽取資源", 1),
            PipelineStepChip("語系比對", 2),
            PipelineStepChip("啟動翻譯", 3),
            PipelineStepChip("打包資源", 4),
        ]
        self.current_step = None

        arrow = lambda: ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=C.DIM)
        step_controls: list[ft.Control] = []
        for index, step in enumerate(self.steps):
            if index:
                step_controls.append(arrow())
            step_controls.append(ft.Container(content=step.chip, padding=5))
        self.step_row = ft.Row(controls=step_controls, spacing=5, wrap=True)

        self.current_label = ft.Text("等待執行...", color=C.MUTED, size=14)
        # PR refactor/unified-log-view: 改用 LogView widget
        # 統一深色容器 + 等寬字 + 等級顏色（從 theme）
        # 保留 height=120 限制
        self.log_view = LogView(
            page=page,
            mode="append",
            max_lines=500,
            height=120,
        )

        self.container = kit.section_card(
            "執行進度",
            ft.Column(
                [
                    self.step_row,
                    ft.Row(
                        [self.current_label, self.cancel_button],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.log_view,
                ],
                spacing=10,
            ),
            icon=ft.Icons.TIMELINE,
            tone="gold",
        )
        self.container.visible = False

    def start(self):
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

    def finish_all(self, success: bool, cancelled: bool = False):
        if success:
            self.current_label.value = "✅ 一鍵製作完成！"
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

    def clear_logs(self):
        # PR refactor/unified-log-view: log_view 改用 LogView
        self.log_view.clear()
