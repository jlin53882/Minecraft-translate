from __future__ import annotations

from dataclasses import dataclass

@dataclass
class TranslationRunState:
    picker_target_field: object | None = None
    session: object | None = None
    ui_timer_running: bool = False
