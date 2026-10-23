"""圖示預覽阻塞載入階段的 operation registry 協調層。"""

import asyncio

from app.ui.design import C
from app.ui.snack import show_snack
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope
from translation_tool.utils.log_unit import log_error, log_info


def observe_async_owner(future, operation, *, on_cancel=None) -> None:
    """觀察 UI coroutine；若尚未開始就取消，釋放 registry 與畫面 owner。"""
    add_done_callback = getattr(future, "add_done_callback", None)
    if not callable(add_done_callback):
        return

    def on_done(done):
        if done.cancelled():
            operation.finish(reason="cancelled")
            if callable(on_cancel):
                on_cancel()
            return
        try:
            error = done.exception()
        except BaseException as ex:  # noqa: BLE001 - Future cancellation must release reservation
            error = ex
        if error is not None:
            operation.finish(error=error)
            if callable(on_cancel):
                on_cancel()

    add_done_callback(on_done)


async def _run_blocking(fn, *args):
    """Keep the owner alive until a thread survives cancellation and returns."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:  # noqa: BLE001 - drain even when blocking work fails
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


async def load_icon_preview(view, generation: int, operation=None) -> None:
    """Run mode detection, cache lookup, counting, and scan under one owner."""

    def current() -> bool:
        return view._load_generation == generation and not (
            operation and operation.handle and operation.handle.cancel_requested
        )

    mode = "unknown"
    entries: list = []
    cancelled = False
    failure: Exception | None = None
    try:
        with cancel_scope(lambda: not current()):
            try:
                mode = await _run_blocking(view._detect_source_mode)
                if not current():
                    return
                log_info(f"[IconPreview] 偵測到模式: {mode}")
                cached = await _run_blocking(view._lookup_cached_entries, mode)
                if not current():
                    return
                if cached is not None:
                    view._apply_cached_entries(cached[0], cached[1], mode)
                    return
                total_steps = await _run_blocking(view._count_scan_steps, mode)
                if not current():
                    return
                view._show_scan_started(mode, total_steps)
                entries = await _run_blocking(
                    view._scan_entries, mode, total_steps, generation
                )
            except TaskCancelled:
                cancelled = True
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as ex:  # noqa: BLE001 - 錯誤顯示在 UI
                if not current():
                    return
                failure = ex
                log_error(f"[IconPreview] 掃描失敗: {ex!r}")
                show_snack(
                    view.page,
                    f"❌ 掃描失敗：{ex}",
                    color=C.RED,
                    clear_existing=True,
                    duration=4000,
                )
                entries = []
    finally:
        active_operation = getattr(view, "_load_operation", operation)
        owns_loading_state = active_operation is operation
        if owns_loading_state:
            release_owner = getattr(view, "_release_load_owner", None)
            if callable(release_owner):
                release_owner(operation)
            else:
                view._loading = False
                if getattr(view, "_load_operation", None) is operation:
                    view._load_operation = None
                if current():
                    view._update_load_state()
        if operation is not None:
            operation.finish(
                reason="cancelled" if cancelled or not current() else "completed",
                error=failure,
            )
    if current():
        view._finish_load(entries, mode)
