"""平行 worker 數量的共通解析：``translator.parallel_execution_workers`` 與各用途的負載上限。

設定值是「使用者想要的上限」，不同工作的負載性質不同（CPU 密集、磁碟 I/O、單檔很大），
所以各用途保有自己的預設值與上限，不強制相同；共通的只有解析規則：

- 設定值必須是正整數（``bool``、字串、浮點數、0、負數都視為無效）；無效時用該用途的預設值。
- 有效時取設定值，無效時取預設值；兩者都再套該用途的上限。
- 結果至少 1。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerPolicy:
    """某個用途的 worker 規則。``fallback``／``cap`` 都以 CPU 核心數計算。"""

    fallback: Callable[[int], int]
    cap: Callable[[int], int] | None = None


def _half(cpu: int) -> int:
    return max(1, cpu // 2)


MAX_CONFIGURED_WORKERS = 64


def _absolute_cap(_cpu: int) -> int:
    """限制手動設定的 worker 數，避免無界建立執行緒與待處理 future。"""
    return MAX_CONFIGURED_WORKERS


WORKER_POLICIES: dict[str, WorkerPolicy] = {
    # 預掃描／計數：輕量讀檔，預設 4。
    "count_prescan": WorkerPolicy(lambda cpu: 4, cap=_absolute_cap),
    # 翻譯前抽取項目（lm_translator／lm_resume）
    "lm_extract": WorkerPolicy(lambda cpu: 4, cap=_absolute_cap),
    # 翻譯快取分片載入
    "cache_load": WorkerPolicy(lambda cpu: 4, cap=_absolute_cap),
    # Mod 資料庫掃描：不超過 CPU 核心數
    "moddb_scan": WorkerPolicy(lambda cpu: 4, cap=lambda cpu: cpu),
    # 圖示索引／預覽快取
    "icon_index": WorkerPolicy(lambda cpu: 8, cap=_absolute_cap),
    "icon_cache": WorkerPolicy(lambda cpu: 4, cap=_absolute_cap),
    # 逐檔／逐 JAR 的檔案處理（翻譯、語系合併）：CPU 的一半
    "file_pipeline": WorkerPolicy(_half, cap=_half),
    # JAR 內容提取：預設 CPU 的一半，使用者設定最多 32
    "jar_extract": WorkerPolicy(_half, cap=lambda cpu: 32),
    # JAR 預覽掃描、JAR 瀏覽
    "jar_preview": WorkerPolicy(lambda cpu: min(4, cpu), cap=_absolute_cap),
    "jar_browser": WorkerPolicy(_half, cap=_absolute_cap),
}


def _valid_workers(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def configured_worker_value(config: Mapping[str, Any] | None) -> Any:
    """回傳設定檔中的原始 ``parallel_execution_workers``（缺漏為 ``None``），供診斷日誌使用。"""
    translator = (
        (config or {}).get("translator") if isinstance(config, Mapping) else None
    )
    return (
        translator.get("parallel_execution_workers")
        if isinstance(translator, Mapping)
        else None
    )


def resolve_worker_count(
    purpose: str,
    config: Mapping[str, Any] | None,
    cpu: int | None = None,
) -> int:
    """依用途解析 worker 數量。

    Args:
        purpose: ``WORKER_POLICIES`` 的鍵；未知用途會丟 ``KeyError``（避免默默用錯上限）。
        config: 完整設定（讀 ``translator.parallel_execution_workers``），可為 ``None``。
        cpu: CPU 核心數；``None`` 時取 ``os.cpu_count()``（取不到視為 2）。
    """
    policy = WORKER_POLICIES[purpose]
    cpu_count = max(1, cpu if cpu is not None else (os.cpu_count() or 2))
    workers = _valid_workers(configured_worker_value(config))
    if workers is None:
        workers = policy.fallback(cpu_count)
    if policy.cap is not None:
        workers = min(workers, policy.cap(cpu_count))
    return max(1, workers)
