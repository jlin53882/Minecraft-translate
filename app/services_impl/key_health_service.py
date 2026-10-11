"""金鑰健康度與金鑰驗證：View／外殼取用引擎核心的唯一入口（#136）。

資料類別（``KeyHealth``、狀態常數）與函式皆原樣轉出，View 不必直接 import ``translation_tool.core``。
"""

from app.tasks.operation_registry import current_operation
from translation_tool.core.lm_config_rules import (
    get_key_health_snapshot,
    get_model_quota_snapshot,
    validate_api_keys_from_ui,
)
from translation_tool.core.lm_key_health import (
    STATUS_COOLING,
    STATUS_PROBING,
    KeyHealth,
    ModelQuotaHealth,
    get_active_model,
    set_active_model_owner_resolver,
)

__all__ = [
    "STATUS_COOLING",
    "STATUS_PROBING",
    "KeyHealth",
    "ModelQuotaHealth",
    "get_active_model",
    "get_key_health_snapshot",
    "get_model_quota_snapshot",
    "track_active_model_by_operation",
    "validate_api_keys_from_ui",
]


def track_active_model_by_operation() -> None:
    """引擎切換模型時記下所屬操作，讓狀態列只顯示目前任務自己用到的模型。"""
    set_active_model_owner_resolver(lambda: getattr(current_operation(), "id", None))
