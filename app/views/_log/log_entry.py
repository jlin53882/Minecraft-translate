"""app/logging/log_entry.py

日誌事件資料模型。

提供 LogEntry 不可變資料類，以及常用 level 常數。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class LogLevel(StrEnum):
    """日誌等級枚舉，繼承 str 可直接比較。"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SYSTEM = "system"


# 等級顯示順序（越小越重要）
LEVEL_ORDER = {
    LogLevel.SYSTEM: 0,
    LogLevel.ERROR: 1,
    LogLevel.WARNING: 2,
    LogLevel.INFO: 3,
    LogLevel.DEBUG: 4,
}


@dataclass(frozen=True, slots=True)
class LogEntry:
    """
    單一日誌事件，設計為不可變（frozen=True），確保跨執行緒傳遞安全。

    屬性：
        seq     — 遞增序號，用於 presenter 判斷「哪些是新行」
        level   — 等級（debug/info/warning/error/system）
        text    — 日誌文字內容
        source  — 來源標記（merge/extractor/translation/.../ui）
        ts      — 產生時間戳（time.time()）
    """

    seq: int
    level: str
    text: str
    source: str
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        # 等級正規化：小寫比對
        object.__setattr__(self, "level", self.level.lower())

    @classmethod
    def make(
        cls,
        text: str,
        level: str = "info",
        source: str = "ui",
        seq: int = 0,
    ) -> LogEntry:
        """工廠方法：從簡單參數建立 LogEntry。"""
        return cls(seq=seq, level=level, text=text, source=source, ts=time.time())
