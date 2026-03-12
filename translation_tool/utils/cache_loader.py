"""translation_tool/utils/cache_loader.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import orjson as json


def load_shard_file(path: Path) -> dict[str, Any]:
    """load_shard_file 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    try:
        data = json.loads(path.read_bytes())
        return data if isinstance(data, dict) else {}
    except Exception:
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
    """load_cache_type 的用途說明。

    Args:
        參數請見函式簽名。
    Returns:
        回傳內容依實作而定；若無顯式回傳則為 None。
    Side Effects:
        可能包含檔案 I/O、網路呼叫或 log 輸出等副作用（依實作而定）。
    """
    if cache_type not in translation_cache:
        translation_cache[cache_type] = {}

    type_dir = cache_root / cache_type
    type_dir.mkdir(parents=True, exist_ok=True)
    cache_file_path[cache_type] = type_dir / f"{cache_type}_cache_main.json"

    json_files = sorted(type_dir.glob("*.json"))
    if not json_files:
        translation_cache[cache_type] = {}
        return

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        results = list(executor.map(load_shard_file, json_files))

    temp_cache: dict[str, Any] = {}
    loaded_count = 0
    for data in results:
        if data:
            temp_cache.update(data)
            loaded_count += len(data)

    translation_cache[cache_type] = temp_cache
    logger.info(f"🚀 高速載入完成：{cache_type} 共 {loaded_count} 條翻譯 (分片數: {len(json_files)})")
