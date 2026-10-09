"""Startup tasks for main.py entrypoint."""

from __future__ import annotations

import logging
import threading

from app.services_impl.cache.cache_services import cache_rebuild_index_service
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    OperationHandle,
    OperationPresentation,
    OperationRegistry,
    ShutdownPolicy,
    launch_task_thread,
)
from translation_tool.utils import cache_manager

logger = logging.getLogger("main_app")


def rebuild_index_on_startup() -> bool:
    """啟動時執行的全域搜尋索引重建任務（快取未變動時略過）。"""
    try:
        if cache_manager.is_search_index_current():
            logger.info("快取未變動，沿用現有搜尋索引")
            return True
        result = cache_rebuild_index_service()
        if result.get("success"):
            logger.info("啟動時全域搜尋索引重建完成")
            return True
        else:
            logger.error(f"啟動時全域搜尋索引重建失敗: {result.get('error')}")
            return False
    except Exception as ex:
        logger.error(f"啟動時索引重建失敗: {ex!r}", exc_info=True)  # noqa: G201
        return False


def start_background_startup_tasks(
    operation_registry: OperationRegistry | None = None,
    *,
    worker_launcher=None,
) -> threading.Thread | OperationHandle | None:
    """啟動索引重建；正式 App 先向 owner registry admission。"""
    if operation_registry is not None:

        def rebuild() -> None:
            if not rebuild_index_on_startup():
                raise RuntimeError("啟動時全域搜尋索引重建失敗")

        return launch_task_thread(
            operation_registry,
            rebuild,
            name="啟動搜尋索引重建",
            owner="startup-index",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            commit=CommitPolicy.PARTIAL_ALLOWED,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            presentation=OperationPresentation.MAINTENANCE,
            launcher=worker_launcher,
        )
    thread = threading.Thread(target=rebuild_index_on_startup, daemon=True)
    thread.start()
    return thread
