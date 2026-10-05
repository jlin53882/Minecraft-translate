"""流水線的 session／worker／輪詢／取消生命週期（#114）。

``PipelineView`` 只負責組版與事件委派；「一個步驟怎麼跑」集中在這裡：

- **worker**：每個步驟（或一鍵的整串步驟）在背景執行緒執行，透過可注入的
  ``launch_worker`` 啟動（預設 ``threading.Thread(daemon=True)``；測試可注入同步啟動器）。
- **session**：每個步驟使用獨立 ``TaskSession``（可注入 ``session_factory``）。
- **輪詢**：日誌／進度由 event loop 上的 watcher 同步到面板，watcher 由 ``PollerHandle`` 持有：
  ``on_unmount()`` 停止它（背景步驟照常執行、不再碰已卸載的控制項）、``on_mount()`` 接續
  （以 ``last_seq`` 避免重複日誌）。
- **卸載期間不碰 UI**：View 卸載後 worker／session 照常執行，但所有會動到控制項的更新
  （最終日誌同步、步驟／整串完成、按鈕恢復）都先暫存，``on_mount()`` 時依序套用一次。
- **取消**：``cancel_event`` 由 View 的取消按鈕設定，步驟在檢查點停止，後續步驟不再執行。

UI 更新一律透過 ``ui()``（``page.run_task``）排回 event loop；worker 不直接改控制項。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from app.tasks.task_session import TaskSession, add_log_unmirrored, tag_session
from app.ui.poller import PollerHandle
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope
from translation_tool.utils.log_unit import log_error, log_warning

# 步驟 worker 等待最後一次日誌同步的上限（秒）
_FINAL_SYNC_TIMEOUT_SEC = 30


def default_session_factory() -> TaskSession:
    return tag_session(TaskSession(), "一鍵流水線", "pipeline")


def default_worker_launcher(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


@dataclass
class _Watch:
    """一個步驟的輪詢狀態：``last_seq`` 讓卸載後重新掛載能接續而不重複日誌。"""

    session: TaskSession
    done: threading.Event = field(default_factory=threading.Event)
    last_seq: int = -1


class PipelineRunner:
    """步驟執行器：建立 session、執行 service、同步進度、處理取消與例外。"""

    # 輪詢間隔：背景任務的日誌/進度以這個頻率批次推到畫面
    POLL_INTERVAL_SEC = 0.2

    def __init__(
        self,
        page,
        panel,
        update_progress: Callable[[float, str], None],
        *,
        session_factory: Callable[[], TaskSession] | None = None,
        launch_worker: Callable[[Callable[[], None]], None] | None = None,
        session_failed: Callable[[TaskSession], bool],
    ) -> None:
        self._page = page
        self._panel = panel
        self._update_progress = update_progress
        self._session_factory = session_factory or default_session_factory
        self._launch = launch_worker or default_worker_launcher
        self._session_failed = session_failed
        self.cancel_event = threading.Event()
        self.current_session: TaskSession | None = None
        self.poller = PollerHandle()  # 日誌／進度 watcher 的 owner
        self._watch: _Watch | None = None
        # 只在 event loop 上讀寫：View 是否掛載、卸載期間暫存的 UI 更新
        self._mounted = True
        self._pending: list[tuple[Callable, tuple]] = []

    # ------------------------------------------------------------------ UI marshal

    def ui(self, fn, *args, **kwargs):
        """在 Flet event loop 上執行 UI 更新（背景執行緒不可直接呼叫 page.update）。"""

        async def _run():
            fn(*args, **kwargs)

        return self._page.run_task(_run)

    # ------------------------------------------------------------------ 生命週期

    def ui_view(self, fn, *args) -> None:
        """排回 event loop 更新 View 的控制項；View 已卸載時改為暫存，掛載時再套用。"""
        self.ui(self._deliver, fn, args)

    def _deliver(self, fn, args) -> None:
        if self._mounted:
            fn(*args)
        else:
            self._pending.append((fn, args))

    def on_unmount(self) -> None:
        """換頁／關閉（event loop）：停止 watcher、之後的 UI 更新暫存；背景步驟照常執行。"""
        self._mounted = False
        self.poller.stop()

    def on_mount(self) -> None:
        """（重新）掛載（event loop）：套用卸載期間暫存的更新，步驟仍在追蹤就接續輪詢。"""
        self._mounted = True
        pending, self._pending = self._pending, []
        for fn, args in pending:
            fn(*args)
        if self._watch is not None:
            self._start_watch()

    def reset_cancel(self) -> None:
        self.cancel_event.clear()

    def request_cancel(self) -> bool:
        """要求取消；已經在取消中回傳 False。"""
        if self.cancel_event.is_set():
            return False
        self.cancel_event.set()
        session = self.current_session
        if session is not None:
            session.request_cancel()
        return True

    # ------------------------------------------------------------------ 啟動

    def start_single(self, step_num: int, name: str, service_fn, on_end) -> None:
        """在背景執行單一步驟；結束後在 event loop 呼叫 ``on_end``。"""

        def worker():
            try:
                self.run_step(step_num, name, service_fn)
            finally:
                self.ui_view(on_end)

        self._launch(worker)

    def start_sequence(self, steps, on_end) -> None:
        """在背景依序執行步驟；任一步驟失敗／取消就停止，最後在 event loop 呼叫 ``on_end``。"""

        def worker():
            success = False
            try:
                for step_num, name, fn in steps:
                    if not self.run_step(step_num, name, fn):
                        return
                success = True
            except Exception as ex:  # noqa: BLE001 - 背景執行緒邊界，確保按鈕會恢復
                # UI 只顯示一行摘要；完整堆疊只寫後台，排查時才有根因
                log_error(f"[Pipeline] 一鍵製作失敗：{ex}\n{traceback.format_exc()}")
                self.ui_view(self._panel.add_log, f"❌ 流程失敗：{ex}", "error")
            finally:
                self.ui_view(self._finish_sequence, success, on_end)

        self._launch(worker)

    def _finish_sequence(self, success: bool, on_end) -> None:
        cancelled = self.cancel_event.is_set()
        self._panel.finish_all(success, cancelled=cancelled)
        if success:
            self._panel.add_log("✅ 一鍵製作完成！")
        elif cancelled:
            self._panel.add_log("⏹ 一鍵製作已取消", "warning")
        on_end()

    # ------------------------------------------------------------------ 步驟

    def run_step(self, step_num: int, name: str, service_fn) -> bool:
        """在目前（背景）執行緒執行一個步驟，回傳是否成功。

        service 回傳 generator 時會完整迭代（merge / bundle service 都是 generator，
        未迭代就不會執行）。
        """
        if self.cancel_event.is_set():
            return False
        session = self._session_factory()
        self.current_session = session
        watch = _Watch(session)
        self._watch = watch
        self.ui_view(self._panel.set_step_running, step_num, name)
        self.ui_view(self._panel.add_log, f"▶ 開始：{name}")
        self.ui(self._start_watch)
        try:
            # 取消檢查：翻譯在批次之間 / 等待限流時、提取在 JAR 之間停止
            with cancel_scope(self.cancel_event.is_set):
                result = service_fn(session)
                if inspect.isgenerator(result):
                    for _ in result:
                        if self.cancel_event.is_set():
                            result.close()
                            # generator 被 close 時 yield 之後的 finish 不會執行：補 terminal
                            session.finish()
                            break
        except TaskCancelled:
            session.finish()  # 取消也要 terminal（TaskManager 不可殘留 active；重複 finish 無害）
        except Exception as ex:  # noqa: BLE001 - 背景步驟邊界：任何錯誤都轉成步驟失敗
            log_error(f"[Pipeline] {name} 失敗：{ex}\n{traceback.format_exc()}")
            # 完整堆疊已在上面寫入後台，畫面只顯示例外類型與訊息，不重複鏡像
            add_log_unmirrored(
                session,
                f"❌ 錯誤：{type(ex).__name__}: {ex}（完整堆疊已寫入後台 log）",
                "error",
            )
            session.set_error()
            session.finish()  # 安全網：順序 set_error() → finish()
        finally:
            self.current_session = None
            if self.cancel_event.is_set():
                session.add_log(f"⏹ {name} 已取消", level="warning")
            watch.done.set()
        # watcher 看到 done 會做最後一次同步後結束；已卸載（watcher 被 stop）則立刻回傳，
        # 之後的最終同步由 event loop 上的 _final_sync 補上，不會漏掉日誌
        if not self.poller.wait_idle(timeout=_FINAL_SYNC_TIMEOUT_SEC):
            log_warning(f"[Pipeline] {name} 日誌同步未完成")
        self.ui_view(self._final_sync, watch)

        cancelled = self.cancel_event.is_set()
        ok = not cancelled and not self._session_failed(session)

        def _finish():
            self._panel.finish_step(step_num, ok, cancelled=cancelled)
            if cancelled:
                self._panel.add_log(f"⏹ {name} 已取消", "warning")
                self._update_progress(1.0, "已取消")
                return
            message = f"✅ {name} 完成" if ok else f"❌ {name} 失敗"
            self._panel.add_log(message)
            self._update_progress(1.0, "完成" if ok else "失敗")

        self.ui_view(_finish)
        return ok

    # ------------------------------------------------------------------ 輪詢（event loop）

    def _start_watch(self) -> None:
        if self._watch is not None and self._mounted:
            self.poller.start(self._page, self._watch_loop)

    async def _watch_loop(self, alive) -> None:
        """在 event loop 上輪詢 session，依 seq 只取新日誌並批次刷新畫面。"""
        while alive():
            watch = self._watch
            if watch is None:
                return
            finished = watch.done.is_set()
            self._sync(watch)
            if finished:
                return
            await asyncio.sleep(self.POLL_INTERVAL_SEC)

    def _final_sync(self, watch: _Watch) -> None:
        """步驟結束後（event loop）補最後一次同步並釋放追蹤。"""
        self._sync(watch)
        if self._watch is watch:
            self._watch = None

    def _sync(self, watch: _Watch) -> None:
        snap = watch.session.snapshot()
        logs = snap.get("logs", [])
        if logs and logs[-1].seq < watch.last_seq:  # session.start() 會重置 seq
            watch.last_seq = -1
        new_items = [(e.text, e.level) for e in logs if e.seq > watch.last_seq]
        if logs:
            watch.last_seq = logs[-1].seq
        if new_items:
            self._panel.log_view.add_many(
                [(f">> {text}", level) for text, level in new_items]
            )
        progress = float(snap.get("progress", 0) or 0)
        self._update_progress(progress, f"{int(progress * 100)}%")
