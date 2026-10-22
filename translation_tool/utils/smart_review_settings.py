"""智慧校對設定的單一定義：預設值、欄位說明、正規化與不可變設定物件。

config schema、config.example.json 產生、設定驗證與執行層（服務、history store、UI）都從這裡
取得預設值與有效設定；``tests/test_smart_review_settings_contract.py`` 檢查它們沒有分歧。

載入時機：``SmartReviewSettings.from_config`` 在呼叫當下讀取設定。Store 在建立時、服務與 UI 在每次
任務開始（或開啟視窗）時取得一份快照；進行中的任務維持原設定。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from typing import Any

DEFAULT_SMART_REVIEW_BATCH_SIZE = 20
DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE = 50
DEFAULT_SMART_REVIEW_STORAGE_CHUNK_SIZE = 200

# 設定鍵 -> 顯示名稱（驗證訊息與設定頁共用）。
SMART_REVIEW_SETTING_LABELS: dict[str, str] = {
    "default_batch_size": "智慧校對預設每批筆數",
    "max_batch_size": "智慧校對每批安全上限",
    "storage_chunk_size": "智慧校對歷史 JSON 分片筆數",
}


def positive_int_setting(value: object, fallback: int) -> int:
    """把設定值正規化為正整數；設定頁以外的呼叫也要有安全保底。"""
    if isinstance(value, bool):
        return fallback
    try:
        return max(1, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True, eq=False)
class SmartReviewSettings(Mapping[str, int]):
    """智慧校對執行與 JSON 儲存共用的有效設定（不可變）。

    ``default_batch_size`` 只決定 UI 預填與未明確指定批次時的預設值；
    ``max_batch_size`` 是執行層安全上限；``storage_chunk_size`` 只控制歷史 JSON
    分片大小，與送給模型的 API 批次大小分離。同時是唯讀 Mapping，舊的
    ``settings["max_batch_size"]`` 寫法仍可使用。
    """

    default_batch_size: int = DEFAULT_SMART_REVIEW_BATCH_SIZE
    max_batch_size: int = DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE
    storage_chunk_size: int = DEFAULT_SMART_REVIEW_STORAGE_CHUNK_SIZE

    @classmethod
    def from_block(cls, block: object) -> SmartReviewSettings:
        """從 ``config["smart_review"]`` 區塊建立有效設定；缺漏或非法值回到預設。"""
        data: Mapping[str, Any] = block if isinstance(block, dict) else {}
        max_batch_size = positive_int_setting(
            data.get("max_batch_size"), DEFAULT_SMART_REVIEW_MAX_BATCH_SIZE
        )
        return cls(
            default_batch_size=min(
                positive_int_setting(
                    data.get("default_batch_size"), DEFAULT_SMART_REVIEW_BATCH_SIZE
                ),
                max_batch_size,
            ),
            max_batch_size=max_batch_size,
            storage_chunk_size=positive_int_setting(
                data.get("storage_chunk_size"), DEFAULT_SMART_REVIEW_STORAGE_CHUNK_SIZE
            ),
        )

    @classmethod
    def from_config(cls, config: object) -> SmartReviewSettings:
        """從完整設定 dict 建立有效設定。"""
        block = config.get("smart_review") if isinstance(config, dict) else None
        return cls.from_block(block)

    def as_dict(self) -> dict[str, int]:
        """回傳可寫入設定檔的 dict 複本。"""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def __getitem__(self, key: str) -> int:
        """以鍵取值；未知鍵丟出 ``KeyError``。"""
        if key not in SMART_REVIEW_SETTING_LABELS:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        """依欄位順序走訪設定鍵。"""
        return iter(SMART_REVIEW_SETTING_LABELS)

    def __len__(self) -> int:
        """設定鍵數量。"""
        return len(SMART_REVIEW_SETTING_LABELS)

    def __hash__(self) -> int:
        """以欄位值計算雜湊；相等判斷沿用 Mapping，可與一般 dict 比較。"""
        return hash(tuple(self.as_dict().values()))
