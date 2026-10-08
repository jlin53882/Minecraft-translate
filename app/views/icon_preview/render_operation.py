"""Registry-owned orchestration for preparing and rendering IconPreview rows."""

import asyncio

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    ShutdownPolicy,
    reserve_page_operation,
)
from app.views.icon_preview.icon_cache import _get_icon_cache_dir
from translation_tool.utils.log_unit import log_error


def render_current_page(view) -> None:
    """Prepare blocking row icons under an owner, then render on the UI loop."""
    entries = (
        view._detail_filtered_entries
        if view._detail_filtered_entries is not None
        else view.mods.get(view.current_modid, [])
    )
    view.total_pages = max(1, (len(entries) + view.page_size - 1) // view.page_size)
    start = view.current_page * view.page_size
    page_entries = list(entries[start : start + view.page_size])
    view.list_view.controls.clear()
    view.page_info.value = (
        f"{view.current_modid}｜第 {view.current_page + 1} / {view.total_pages} 頁"
    )
    view.prev_page_btn.disabled = view.current_page <= 0
    view.next_page_btn.disabled = view.current_page >= view.total_pages - 1
    run_task = getattr(view.page, "run_task", None)
    if run_task is None:
        view._fill_rows(page_entries, None)
        return

    view.update()
    view._render_generation += 1
    generation = view._render_generation
    icon_context = (view.source_root / "assets", _get_icon_cache_dir())
    operation = reserve_page_operation(
        view.page,
        name="IconPreview 頁面圖示準備",
        owner="icon-preview-cache",
        cancellation=CancellationPolicy.BOUNDARY_ONLY,
        commit=CommitPolicy.EPHEMERAL,
        shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
    )
    if not operation.admitted:
        return
    prepared_result = {}

    def prepare_icons():
        try:
            prepared_result["icons"] = view._prepare_row_icons(
                page_entries, icon_context
            )
        except Exception as ex:  # noqa: BLE001 - event loop displays failure
            prepared_result["error"] = ex

    if not operation.launch(prepare_icons):
        operation.finish(error=RuntimeError("icon prepare worker was not launched"))
        return

    async def prepare_and_fill():
        while not operation.done_event.is_set():
            await asyncio.sleep(0.02)
        if "error" in prepared_result:
            log_error(f"[IconPreview] 準備圖示失敗: {prepared_result['error']!r}")
            return
        if operation.handle and operation.handle.cancel_requested:
            return
        if generation == view._render_generation:
            view._fill_rows(page_entries, prepared_result["icons"])

    try:
        run_task(prepare_and_fill)
    except Exception as ex:  # noqa: BLE001 - scheduling failure must be reported
        log_error(f"[IconPreview] 無法排程圖示準備: {ex!r}")
