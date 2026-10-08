"""translation_tool/utils/cache_loader.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import orjson as json

from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cache_shards import list_shards_oldest_first
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor

logger = logging.getLogger(__name__)


def load_shard_file(path: Path) -> dict[str, Any]:
    """載入並解析單一分片（Shard）的 JSON 檔案，將其轉換為記憶體中的快取物件。

    若 shard 檔案為空（0 bytes），會記錄警告並回傳空 dict。
    """
    try:
        file_size = path.stat().st_size
        if file_size == 0:
            logger.warning(f"空 shard 檔案（將跳過）: {path}")
            return {}
        data = json.loads(path.read_bytes())
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001 載入失敗不應中斷其他分片
        logger.warning(f"載入分片失敗 {path}: {e!r}")
        return {}


def load_cache_type(
    cache_type: str,
    *,
    translation_cache: dict[str, dict[str, Any]],
    cache_file_path: dict[str, Path],
    cache_root: Path,
    parallel_workers: int,
    logger: logging.Logger,
) -> None:
    """載入指定類型的快取。"""
    if cache_type not in translation_cache:
        translation_cache[cache_type] = {}

    type_dir = cache_root / cache_type
    type_dir.mkdir(parents=True, exist_ok=True)
    cache_file_path[cache_type] = type_dir / f"{cache_type}_cache_main.json"

    # 依分片寫入序號由舊到新載入，後寫入者覆蓋先寫入者（見 cache_shards 的 freshness contract）
    json_files = list_shards_oldest_first(type_dir)
    if not json_files:
        translation_cache[cache_type] = {}
        return

    results: list[dict[str, Any] | None] = [None] * len(json_files)

    def submit_shard(executor, item: tuple[int, Path]):
        _index, path = item
        return executor.submit(load_shard_file, path)

    with (
        ContextThreadPoolExecutor(max_workers=parallel_workers) as executor,
        bounded_as_completed(
            executor,
            enumerate(json_files),
            submit_shard,
            max_in_flight=max(1, parallel_workers * 2),
        ) as completed,
    ):
        for future, (index, _path) in completed:
            results[index] = future.result()

    temp_cache: dict[str, Any] = {}
    loaded_count = 0
    for data in results:
        if data:
            temp_cache.update(data)
            loaded_count += len(data)

    translation_cache[cache_type] = temp_cache
    logger.info(
        f"🚀 高速載入完成：{cache_type} 共 {loaded_count} 條翻譯 (分片數: {len(json_files)})"
    )
