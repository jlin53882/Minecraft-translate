"""Startup tasks for main.py entrypoint."""

from __future__ import annotations

import logging
import threading

from app.services_impl.cache.cache_services import cache_rebuild_index_service
from translation_tool.utils import cache_manager

logger = logging.getLogger("main_app")


def rebuild_index_on_startup() -> None:
    """啟動時執行的全域搜尋索引重建任務（快取未變動時略過）。"""
    try:
        if cache_manager.is_search_index_current():
            logger.info("快取未變動，沿用現有搜尋索引")
            return
        result = cache_rebuild_index_service()
        if result.get("success"):
            logger.info("啟動時全域搜尋索引重建完成")
        else:
            logger.error(f"啟動時全域搜尋索引重建失敗: {result.get('error')}")
    except Exception as ex:
        logger.error(f"啟動時索引重建失敗: {ex!r}", exc_info=True)  # noqa: G201


def start_background_startup_tasks() -> threading.Thread:
    """以 Daemon 執行緒啟動後台啟動任務並回傳執行緒物件。"""
    thread = threading.Thread(target=rebuild_index_on_startup, daemon=True)
    thread.start()
    return thread
