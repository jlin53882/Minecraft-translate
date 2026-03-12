"""Startup tasks for main.py entrypoint."""

from __future__ import annotations

import logging
import threading

from app.services_impl.cache.cache_services import cache_rebuild_index_service

logger = logging.getLogger('main_app')


def rebuild_index_on_startup() -> None:
    try:
        cache_rebuild_index_service()
        logger.info('啟動時全域搜尋索引重建完成')
    except Exception as ex:
        logger.error(f'啟動時索引重建失敗: {ex}', exc_info=True)


def start_background_startup_tasks() -> threading.Thread:
    thread = threading.Thread(target=rebuild_index_on_startup, daemon=True)
    thread.start()
    return thread
