from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExtractionStats:
    success: int = 0
    warnings: int = 0
    failures: int = 0
    total_files: int = 0

    def reset(self) -> None:
        self.success = 0
        self.warnings = 0
        self.failures = 0
        self.total_files = 0

    def as_dict(self) -> dict:
        return {
            'success': self.success,
            'warnings': self.warnings,
            'failures': self.failures,
            'total_files': self.total_files,
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
        return {
            'progress': self.progress,
            'current': self.current,
            'total': self.total,
            'done': self.done,
            'result': self.result,
            'error': self.error,
        }
