"""app/views/qc_base.py 模組。

用途：QCView 共用的基礎類別，封裝執行緒任務與 UI 更新邏輯。
維護注意：本模組提供 task_worker 給各 QC 檢查器使用。
"""

import threading
import traceback
from collections.abc import Callable
from typing import Any

import flet as ft

from app.ui.design import C
from app.ui.ui_batcher import UiBatcher
from app.views._log import LogView
from translation_tool.utils.log_unit import log_error
from translation_tool.utils.ui_mirror import in_new_task, mirror_lines

_UI_FLUSH_INTERVAL_SEC = 0.2


def _guess_level(line: str) -> str:
    """依訊息內容推測 log 等級（service 只回傳字串）。"""
    if "錯誤" in line or "失敗" in line or "ERROR" in line or "❌" in line:
        return "error"
    if "警告" in line or "WARN" in line or "⚠" in line:
        return "warning"
    return "info"


class QCBase:
    """QCBase 基礎類別。

    用途：封裝執行緒任務執行與 UI 更新邏輯，供各 QC 檢查器重用。
    維護注意：修改此類會影響所有使用 task_worker 的 QC 檢查器。

    PR refactor/unified-log-view: log_view 改為 LogView widget（統一深色容器 + 等寬字）。
    LogView.add() 內部處理 show_levels 過濾 + max_lines 截斷 + 等級顏色。
    """

    def __init__(self, page: ft.Page, progress_bar: ft.ProgressBar, log_view: LogView):
        """初始化 QCBase。

        參數：
            page: Flet Page 物件
            progress_bar: 共用的 ProgressBar 元件
            log_view: LogView widget（取代裸 ft.ListView）
        """
        self._page = page
        self.progress_bar = progress_bar
        self.log_view = log_view

    def task_worker(
        self,
        service_func: Callable[..., Any],
        args_tuple: tuple[Any, ...],
        on_complete: Callable[[], None] | None = None,
        controls_to_disable: list[ft.Control] | None = None,
    ):
        """執行品質檢查服務工作執行緒。

        參數：
            service_func: 服務函式（需為 generator）
            args_tuple: 傳給服務函式的參數元組
            on_complete: 任務完成後的回調函式
            controls_to_disable: 任務執行期間要禁用的控制項列表
        """
        # 禁用控制項
        if controls_to_disable:
            for ctrl in controls_to_disable:
                ctrl.disabled = True
            self._page.update()

        def apply_ui(lines, state):
            if lines:
                self.log_view.add_many(lines)
            if state.get("progress") is not None:
                self.progress_bar.value = state["progress"]
            if state.get("error"):
                self.progress_bar.color = C.RED
            done = state.get("done", False)
            if done:
                self.progress_bar.value = 0
                self.progress_bar.color = C.EM
                if controls_to_disable:
                    for ctrl in controls_to_disable:
                        ctrl.disabled = False
            self._page.update()
            if done and on_complete:
                on_complete()

        # 節流 + 背壓：背景任務可能每秒產生上千行，只定期交給 event loop 刷新
        batcher = UiBatcher(self._page, apply_ui, interval=_UI_FLUSH_INTERVAL_SEC)

        def run():
            try:
                for update in service_func(*args_tuple):
                    lines = [
                        (line, _guess_level(line))
                        for line in str(update.get("log") or "").split("\n")
                        if line.strip()
                    ]
                    if lines:
                        mirror_lines(lines, prefix="[QC] ")
                        batcher.add_lines(lines)
                    if "progress" in update:
                        batcher.set_state(progress=update["progress"])
                    if update.get("error"):
                        batcher.set_state(error=True)
                    batcher.flush()
            except Exception as ex:  # noqa: BLE001 - 背景執行緒需把錯誤回報到 UI
                log_error(f"QC 任務失敗: {ex!r}\n{traceback.format_exc()}")
                batcher.add_lines([(f"[錯誤] 任務執行失敗：{ex}", "error")])
                batcher.set_state(error=True)
            finally:
                batcher.set_state(done=True)
                batcher.flush(force=True)

        threading.Thread(target=in_new_task("qc", run), daemon=True).start()

    @property
    def page(self):
        return self._page
