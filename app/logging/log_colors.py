"""app/logging/log_colors.py

日誌等級 → 顏色對應規則。

把所有 view 散落的顏色判斷收斂到同一個地方。
"""

from __future__ import annotations

# Flet 可用的顏色常量（theme 常見值）
# 這裡用 hex 或基本顏色名稱，view 層再依據 theme 轉換
COLOR_MAP = {
    "system": "4CAF50",   # 綠
    "info": "90CAF9",    # 淺藍
    "warning": "FF9800",  # 橙
    "error": "F44336",    # 紅
    "debug": "9E9E9E",    # 灰
}

# 等級標記前綴（當無顏色時用於裝飾）
LEVEL_PREFIX = {
    "system": "[SYS]",
    "info": "",
    "warning": "[WARN]",
    "error": "[ERR]",
    "debug": "[DBG]",
}


def get_level_color(level: str) -> str:
    """取得指定等級對應的 hex 顏色。"""
    return COLOR_MAP.get(str(level).lower(), COLOR_MAP["info"])


def get_level_prefix(level: str) -> str:
    """取得指定等級的前綴文字。"""
    return LEVEL_PREFIX.get(str(level).lower(), "")
