"""智慧校對「執行批次」層級的狀態：共用的列舉、顯示文字與色調。

與候選（candidate）層級的 ``SmartReviewStatus`` 不同：這裡描述一整次批次跑完的結果，
執行頁、歷史頁與背景任務提示都從這份單一來源取得標籤與色調，避免同一狀態在不同頁面
出現不同說法或顏色。
"""

from __future__ import annotations

from enum import StrEnum

from app.ui.design import tone as get_tone


class SmartReviewRunStatus(StrEnum):
    """一次智慧校對批次的狀態（資料庫 run 狀態字面值）。"""

    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


# 狀態 -> (顯示文字, 色調)。已取消是使用者主動停止，與部分完成同屬「未跑完」，用 gold。
RUN_STATUS_STYLES: dict[str, tuple[str, str]] = {
    SmartReviewRunStatus.RUNNING: ("執行中", "gold"),
    SmartReviewRunStatus.COMPLETED: ("完成", "em"),
    SmartReviewRunStatus.PARTIAL: ("部分完成", "gold"),
    SmartReviewRunStatus.CANCELLED: ("已取消", "gold"),
    SmartReviewRunStatus.INTERRUPTED: ("意外中斷", "red"),
    SmartReviewRunStatus.FAILED: ("失敗", "red"),
}


def run_status_label(status: str, *, active: bool = True) -> tuple[str, str]:
    """回傳批次狀態的顯示文字與色調；資料庫標記執行中但實際沒在跑代表已中斷。"""
    if status == SmartReviewRunStatus.RUNNING and not active:
        return "可能已中斷", "red"
    return RUN_STATUS_STYLES.get(status, (status or "未知", "neutral"))


def run_status_color(status: str) -> str:
    """回傳批次狀態對應的文字色（供提示訊息與狀態文字使用）。"""
    return get_tone(run_status_label(status)[1]).fg
