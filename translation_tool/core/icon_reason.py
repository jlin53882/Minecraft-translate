"""translation_tool/core/icon_reason.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from enum import Enum
from dataclasses import dataclass
"""
IconResult = 診斷報告
IconRisk = 緊急程度
"""
class IconRisk(Enum):
    """IconRisk 類別。

    用途：封裝與 IconRisk 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    IGNORE = "ignore"
    WARN = "warn"
    DANGER = "danger"


@dataclass
class IconResult:
    """IconResult 類別。

    用途：封裝與 IconResult 相關的狀態與行為。
    維護注意：修改公開方法前請確認外部呼叫點與相容性。
    """
    icon_path: object | None
    reason: str
    risk: IconRisk
