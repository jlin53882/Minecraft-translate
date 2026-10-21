"""SQLite 查詢共用的小型輔助函數。"""

from __future__ import annotations


def like_pattern(text: str) -> str:
    """把使用者輸入轉成含 ESCAPE 反斜線的 LIKE 字面比對模式。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


__all__ = ["like_pattern"]
