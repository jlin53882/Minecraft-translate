from __future__ import annotations

from dataclasses import dataclass

@dataclass
class ExtractionState:
    progress: float = 0.0
    current: int = 0
    total: int = 0
    done: bool = False
    error: bool = False

    def as_dict(self) -> dict:
        return {
            'progress': self.progress,
            'current': self.current,
            'total': self.total,
            'done': self.done,
            'error': self.error,
        }

@dataclass
class PreviewState:
    progress: float = 0.0
    current: int = 0
    total: int = 0
    done: bool = False
    result: dict | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        """將 PreviewState 轉換為字典格式。"""
        return {
            'progress': self.progress,
            'current': self.current,
            'total': self.total,
            'done': self.done,
            'result': self.result,
            'error': self.error,
        }
