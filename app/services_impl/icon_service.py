"""圖示解析與預覽快取：View 取用引擎核心的唯一入口（#136）。"""

from translation_tool.core.icon_preview_cache import generate_icon_preview
from translation_tool.core.icon_reason import IconResult, IconRisk
from translation_tool.core.icon_resolver import resolve_icon_with_reason

__all__ = [
    "IconResult",
    "IconRisk",
    "generate_icon_preview",
    "resolve_icon_with_reason",
]
