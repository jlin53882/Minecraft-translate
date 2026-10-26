"""Startup tasks for main.py entrypoint."""

from __future__ import annotations

import importlib
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


def warm_view_imports() -> None:
    """在背景預先匯入各頁面模組（不建立頁面）。

    第一次切到頁面時才匯入，會在 UI 事件迴圈上多花 0.2～0.35 秒（translation_db、requests、
    jwt 等）；啟動後先在背景匯入，切頁時只剩建立控制項的時間。匯入失敗不影響啟動，
    等使用者真的開啟該頁時會照常回報錯誤。
    """
    from app.view_registry import VIEW_SPECS

    for spec in VIEW_SPECS:
        try:
            importlib.import_module(spec.module)
        except Exception:  # 預熱只是優化；錯誤留給開啟頁面時回報
            logger.debug("預先匯入 %s 失敗", spec.module, exc_info=True)


def start_background_startup_tasks(
    operation_registry: OperationRegistry | None = None,
    *,
    worker_launcher=None,
) -> threading.Thread | OperationHandle | None:
    """啟動索引重建；正式 App 先向 owner registry admission。"""
    if operation_registry is not None:

        def rebuild() -> None:
            warm_view_imports()
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

    def run() -> None:
        warm_view_imports()
        rebuild_index_on_startup()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
