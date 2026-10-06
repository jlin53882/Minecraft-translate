"""UI kit：新設計的共用元件。

所有元件的顏色都來自 ``app.ui.design``（語意色 / 強調色組），會跟著深淺色主題自動切換；
只做樣式與版面封裝，不碰 services / translation_tool。

常用：
    from app.ui import kit

    kit.page_header("機器翻譯", "…", icon=ft.Icons.AUTO_AWESOME, tone="gold", actions=[...])
    kit.section_card("翻譯設定", body, icon=ft.Icons.SETTINGS, tone="gold")
    kit.button("開始", "primary", icon=ft.Icons.PLAY_ARROW, on_click=...)
    kit.chip("完成", "em", icon=ft.Icons.CHECK)
"""

from app.ui.kit.basics import (
    chip,
    count_badge,
    hint_text,
    kbd,
    mono_text,
    rekey,
    section_label,
    tone_icon,
    vdivider,
)
from app.ui.kit.cards import (
    ChoiceCard,
    SectionCard,
    StatCard,
    StepCard,
    page_header,
    section_card,
    stat_card,
)
from app.ui.kit.inputs import (
    Pager,
    Segmented,
    SwitchRow,
    button,
    dropdown,
    field,
    page_window,
    pick_button,
    set_dropdown_options,
    text_field,
)
from app.ui.kit.progress import ProgressRing, clamp01, progress_bar
from app.ui.kit.states import empty_state, error_state, loading_state

__all__ = [
    "ChoiceCard",
    "Pager",
    "ProgressRing",
    "SectionCard",
    "Segmented",
    "StatCard",
    "StepCard",
    "SwitchRow",
    "button",
    "chip",
    "clamp01",
    "count_badge",
    "dropdown",
    "empty_state",
    "error_state",
    "field",
    "hint_text",
    "kbd",
    "loading_state",
    "mono_text",
    "page_header",
    "page_window",
    "pick_button",
    "progress_bar",
    "rekey",
    "section_card",
    "section_label",
    "set_dropdown_options",
    "stat_card",
    "text_field",
    "tone_icon",
    "vdivider",
]
