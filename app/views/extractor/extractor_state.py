"""extractor_state.py - JAR 提取頁狀態資料結構。

本模組定義 ExtractorView 的狀態資料類別，用於追蹤提取和預覽進度。

類別：
  - ExtractionState：提取任務的進度狀態
  - PreviewState：預覽任務的進度狀態
"""

from dataclasses import dataclass


@dataclass
class ExtractionState:
    """提取任務的進度狀態。

    屬性：
        progress: 進度百分比（0.0 ~ 1.0）
        current: 當前處理的 JAR 檔案索引
        total: 總 JAR 檔案數量
        done: 是否已完成
        error: 是否發生錯誤
    """
    progress: float = 0.0
    current: int = 0
    total: int = 0
    done: bool = False
    error: bool = False

    def as_dict(self) -> dict:
        """將狀態轉換為字典格式。

        Returns:
            包含所有欄位的字典
        """
        return {
            'progress': self.progress,
            'current': self.current,
            'total': self.total,
            'done': self.done,
            'error': self.error,
        }


@dataclass
class PreviewState:
    """預覽任務的進度狀態。

    屬性：
        progress: 進度百分比（0.0 ~ 1.0）
        current: 當前處理的 JAR 檔案索引
        total: 總 JAR 檔案數量
        done: 是否已完成
        result: 預覽結果資料（完成的話）
        error: 錯誤訊息（有的話）
    """
    progress: float = 0.0
    current: int = 0
    total: int = 0
    done: bool = False
    result: dict | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        """將 PreviewState 轉換為字典格式。

        Returns:
            包含所有欄位的字典
        """
        return {
            'progress': self.progress,
            'current': self.current,
            'total': self.total,
            'done': self.done,
            'result': self.result,
            'error': self.error,
        }