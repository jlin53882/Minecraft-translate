from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CacheUiState:
    busy: bool = False
    reason: str = ""
    trace: str = "trace: init"


@dataclass(slots=True)
class ActionState:
    action_id: int
    reason: str
    phase: str
