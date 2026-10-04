"""金鑰健康度與金鑰驗證：View／外殼取用引擎核心的唯一入口（#136）。

資料類別（``KeyHealth``、狀態常數）與函式皆原樣轉出，View 不必直接 import ``translation_tool.core``。
"""

from translation_tool.core.lm_config_rules import (
    get_key_health_snapshot,
    validate_api_keys_from_ui,
)
from translation_tool.core.lm_key_health import (
    STATUS_COOLING,
    STATUS_PROBING,
    KeyHealth,
)

__all__ = [
    "STATUS_COOLING",
    "STATUS_PROBING",
    "KeyHealth",
    "get_key_health_snapshot",
    "validate_api_keys_from_ui",
]
