from enum import Enum
from dataclasses import dataclass
"""
IconResult = 診斷報告
IconRisk = 緊急程度
"""
class IconRisk(Enum):
    IGNORE = "ignore"
    WARN = "warn"
    DANGER = "danger"


@dataclass
class IconResult:
    icon_path: object | None
    reason: str
    risk: IconRisk
