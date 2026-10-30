"""共用流水線工作台外框。

一鍵流水線、KubeJS／FTB 流水線與任務翻譯頁共用同一個頁首與左右工作區，
讓頁面只負責提供自己的內容卡，不各自複製外框與 responsive 行為。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import flet as ft

from app.ui import kit
from app.ui.design import C
from app.ui.design import tone as get_tone


@dataclass(frozen=True)
class PipelineStepSpec:
    """描述工作台步驟列的可重用資料契約。"""

    number: int
    title: str
    description: str
    icon: str
    tone: str
    on_click: Callable | None = None
    action_label: str = "執行"
    action_tooltip: str | None = None
    toggle: ft.Checkbox | None = None
    status: str | None = None
    action_control: ft.Control | None = None
    extra_actions: tuple[PipelineActionSpec, ...] = ()


@dataclass(frozen=True)
class PipelineActionSpec:
    """描述步驟列右側的額外操作。

    大多數步驟只有一個「執行」按鈕，因此仍可使用 ``PipelineStepSpec`` 的
    ``on_click``／``action_label`` 簡寫。需要「提取／預覽」這類成對操作時，
    呼叫端以這個資料契約追加按鈕，仍由同一個步驟列負責間距、排列與響應式行為。
    ``control`` 讓既有 view 可以保留原本持有的按鈕物件與事件回呼。
    """

    label: str
    on_click: Callable | None = None
    icon: str | None = None
    kind: str = "secondary"
    tooltip: str | None = None
    control: ft.Control | None = None


_STEP_STATUS_TONES = {
    "waiting": "neutral",
    "running": "gold",
    "done": "em",
    "failed": "red",
    "cancelled": "gold",
}
_STEP_STATUS_ICONS = {
    "waiting": ft.Icons.CIRCLE,
    "running": ft.Icons.PENDING,
    "done": ft.Icons.CHECK_CIRCLE,
    "failed": ft.Icons.ERROR,
    "cancelled": ft.Icons.STOP_CIRCLE,
}
_STEP_STATUS_LABELS = {
    "waiting": "等待",
    "running": "執行中",
    "done": "完成",
    "failed": "失敗",
    "cancelled": "已取消",
}


class PipelineStepRow(ft.Container):
    """可選步驟的共用列；保留 checkbox 與可供 reset 使用的 refresh seam。"""

    def __init__(self, step: PipelineStepSpec, *, tone) -> None:
        self.name = step.title
        self.checkbox = step.toggle
        self._status = ft.Text(size=11, color=C.DIM)
        self.action_button: ft.Control | None = None
        self.action_buttons: list[ft.Control] = []
        self.step_status = step.status
        self._state_icon: ft.Icon | None = None
        self._state_label: ft.Text | None = None
        self._number_badge: ft.Container | None = None
        self._number_text: ft.Text | None = None
        super().__init__(
            padding=ft.Padding.symmetric(horizontal=4, vertical=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.LINE)),
        )
        self._tone = tone
        self._previous_on_change = (
            self.checkbox.on_change if self.checkbox is not None else None
        )
        if self.checkbox is not None:
            self.checkbox.label = "執行"
            self.checkbox.on_change = self._on_toggle
        self.content = self._build_content(step)
        self.refresh()

    def _build_content(self, step: PipelineStepSpec) -> ft.Control:
        self.action_button = None
        self.action_buttons = []
        primary = self._build_action(
            PipelineActionSpec(
                label=step.action_label,
                on_click=step.on_click,
                icon=step.icon,
                tooltip=step.action_tooltip or f"單獨執行：{step.title}",
                control=step.action_control,
            )
        )
        if primary is not None:
            self.action_button = primary
            self.action_buttons.append(primary)
        for action in step.extra_actions:
            control = self._build_action(action)
            if control is not None:
                self.action_buttons.append(control)

        trailing: list[ft.Control] = []
        if self.checkbox is not None:
            trailing.extend([self.checkbox, self._status])
        if self.action_buttons:
            trailing.append(
                ft.Row(
                    self.action_buttons,
                    spacing=6,
                    run_spacing=6,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )

        main = ft.Row(
            [
                self._build_number_badge(step),
                ft.Column(
                    [
                        ft.Text(
                            step.title,
                            size=14,
                            weight=ft.FontWeight.BOLD,
                            color=C.TEXT,
                        ),
                        ft.Text(step.description, size=12, color=C.DIM),
                    ],
                    spacing=1,
                    tight=True,
                    expand=True,
                ),
            ],
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        if self.step_status is not None:
            self._state_icon = ft.Icon(ft.Icons.CIRCLE, size=16)
            self._state_label = ft.Text(size=11)
            self._update_step_status()
            trailing.insert(0, self._state_icon)
            trailing.insert(1, self._state_label)
        trailing_control = ft.Row(
            trailing,
            spacing=6,
            run_spacing=6,
            wrap=True,
            alignment=ft.MainAxisAlignment.END,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.ResponsiveRow(
            [
                ft.Container(main, col={"xs": 12, "md": 7}),
                ft.Container(
                    trailing_control,
                    col={"xs": 12, "md": 5},
                    alignment=ft.Alignment(1, 0),
                ),
            ],
            spacing=14,
            run_spacing=6,
        )

    @staticmethod
    def _build_action(action: PipelineActionSpec) -> ft.Control | None:
        if action.control is not None:
            return action.control
        if action.on_click is None:
            return None
        return kit.button(
            action.label,
            action.kind,
            icon=action.icon,
            size="sm",
            tooltip=action.tooltip,
            on_click=action.on_click,
        )

    def _build_number_badge(self, step: PipelineStepSpec) -> ft.Container:
        self._number_text = ft.Text(
            str(step.number),
            size=14,
            weight=ft.FontWeight.BOLD,
            color=self._tone.fg,
        )
        self._number_badge = ft.Container(
            width=34,
            height=34,
            border_radius=17,
            bgcolor=self._tone.bg,
            border=ft.Border.all(1, self._tone.line),
            alignment=ft.Alignment.CENTER,
            content=self._number_text,
        )
        return self._number_badge

    def _on_toggle(self, event) -> None:
        self.refresh()
        if callable(self._previous_on_change):
            self._previous_on_change(event)

    def refresh(self) -> None:
        """同步 reset 或程式直接改值後的顯示狀態。"""
        if self.checkbox is not None:
            enabled = bool(self.checkbox.value)
            self._status.value = "將執行" if enabled else "略過"
            self._status.color = self._tone.fg if enabled else C.DIM
        if self.step_status is not None:
            self._update_step_status()

    def set_status(self, status: str) -> None:
        """更新執行狀態，供背景工作流程在 UI 執行緒同步。"""
        self.step_status = status
        self._update_step_status()

    @property
    def status(self) -> str | None:
        """相容舊進度面板的狀態讀取介面。"""
        return self.step_status

    def _update_step_status(self) -> None:
        if self.step_status is None:
            return
        status = (
            self.step_status if self.step_status in _STEP_STATUS_TONES else "waiting"
        )
        tone = get_tone(_STEP_STATUS_TONES[status])
        if self._state_icon is not None:
            self._state_icon.name = _STEP_STATUS_ICONS[status]
            self._state_icon.color = tone.fg
        if self._state_label is not None:
            self._state_label.value = _STEP_STATUS_LABELS[status]
            self._state_label.color = tone.fg
        if self._number_badge is not None:
            self._number_badge.bgcolor = tone.bg
            self._number_badge.border = ft.Border.all(1, tone.line)
        if self._number_text is not None:
            self._number_text.color = tone.fg


def build_pipeline_step_row(
    step: PipelineStepSpec,
    *,
    on_button_created: Callable[[ft.Control], None] | None = None,
) -> ft.Container:
    """建立一列工作台步驟；有 callback 才顯示右側步驟操作。"""
    tone = get_tone(step.tone)
    row = PipelineStepRow(step, tone=tone)
    if on_button_created is not None:
        for action_button in row.action_buttons:
            on_button_created(action_button)
    return row


def build_pipeline_step_list(
    steps: Sequence[PipelineStepSpec],
    *,
    on_button_created: Callable[[ft.Control], None] | None = None,
) -> ft.Column:
    """建立不含標題卡的步驟列，供已有外層卡片的頁面重用。"""
    return ft.Column(
        [
            build_pipeline_step_row(step, on_button_created=on_button_created)
            for step in steps
        ],
        spacing=0,
    )


def build_pipeline_steps_card(
    steps: Sequence[PipelineStepSpec],
    *,
    title: str = "流水線步驟",
    icon: str = ft.Icons.ACCOUNT_TREE_OUTLINED,
    tone: str = "ench",
    on_button_created: Callable[[ft.Control], None] | None = None,
) -> ft.Control:
    """建立所有流程頁共用的步驟卡，維持相同編號、間距與操作位置。"""
    return kit.section_card(
        title,
        build_pipeline_step_list(steps, on_button_created=on_button_created),
        icon=icon,
        tone=tone,
    )


def build_pipeline_status_card(
    *,
    progress: ft.Control,
    progress_status: ft.Control,
    status_chip: ft.Control | None = None,
    summary: ft.Control | None = None,
    hint: ft.Control | None = None,
    cancel: ft.Control | None = None,
    title: str = "執行狀態",
    icon: str = ft.Icons.INFO_OUTLINE,
    tone: str = "em",
) -> kit.SectionCard:
    """建立所有流水線頁共用的執行狀態卡。

    一鍵流水線、KubeJS／FTB 與任務翻譯的執行工作不同，但狀態卡的閱讀順序
    必須一致：目前狀態 → 整體進度 → 摘要／操作。把外殼集中在這裡，避免
    各頁面各自複製一套 padding、邊框與按鈕排列。
    """
    # The SectionCard header already owns the status icon.  Keeping the body
    # focused on the live status text avoids a double-icon variant that used to
    # make execution cards look different from every other workflow card.
    leading = ft.Row(
        [progress_status],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    trailing = [control for control in (status_chip, cancel) if control is not None]
    header: ft.Control = leading
    body = [header, progress]
    if summary is not None:
        body.append(summary)
    if hint is not None:
        body.append(hint)
    return kit.section_card(
        title,
        ft.Column(body, spacing=8),
        icon=icon,
        tone=tone,
        actions=trailing,
    )


def build_pipeline_log_card(
    log_view: ft.Control,
    *,
    title: str = "執行日誌",
    icon: str = ft.Icons.RECEIPT_LONG,
    tone: str = "neutral",
    height: int | None = None,
    actions: Sequence[ft.Control] = (),
    expand: bool | int = False,
    collapsible: bool = False,
) -> ft.Control:
    """建立工作流程共用的日誌卡。

    日誌卡只負責視覺外殼與高度，不擁有 session、輪詢或取消邏輯；各流程
    仍由自己的 ``LogView``／``TaskSession`` 管理內容。把標題、圖示與容器
    集中在這裡，避免 LM、打包、合併與任務翻譯各自長出不同的日誌外觀。
    """
    content: ft.Control = log_view
    if height is not None:
        content = ft.Container(content=log_view, height=height)
    return kit.section_card(
        title,
        content,
        icon=icon,
        tone=tone,
        actions=list(actions),
        expand=expand,
        collapsible=collapsible,
    )


def build_pipeline_workbench(
    *,
    title: str,
    subtitle: str,
    icon: str,
    tone: str,
    left_controls: Sequence[ft.Control],
    right_controls: Sequence[ft.Control],
    actions: Sequence[ft.Control] = (),
) -> ft.Column:
    """建立一鍵流水線風格的頁面外框。

    內容卡由呼叫頁面持有，外框統一處理頁首、左右欄間距、滾動與伸展。
    這樣新增流程入口時只需維護流程本身，不會再產生第二套頁面骨架。
    """
    columns = ft.ResponsiveRow(
        [
            ft.Column(
                list(left_controls),
                spacing=16,
                col={"xs": 12, "md": 5},
            ),
            ft.Column(
                list(right_controls),
                spacing=16,
                col={"xs": 12, "md": 7},
            ),
        ],
        spacing=16,
        run_spacing=16,
    )
    return ft.Column(
        [
            kit.page_header(
                title,
                subtitle,
                icon=icon,
                tone=tone,
                actions=list(actions),
            ),
            columns,
        ],
        spacing=18,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


__all__ = [
    "PipelineActionSpec",
    "PipelineStepRow",
    "PipelineStepSpec",
    "build_pipeline_log_card",
    "build_pipeline_status_card",
    "build_pipeline_step_list",
    "build_pipeline_step_row",
    "build_pipeline_steps_card",
    "build_pipeline_workbench",
]
