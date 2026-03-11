"""cache_types.py

Cache 頁面 UI 狀態的資料結構定義。

這些 dataclass 只負責「型別與欄位」：
- 不包含任何 UI 控制項
- 不包含任何流程/行為

這樣做的好處：
- cache_view.py 可以更專注在組 UI
- presenter/controller 可以更容易做單元測試
"""

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
