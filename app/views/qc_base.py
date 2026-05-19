"""app/views/qc_base.py 模組。

用途：QCView 共用的基礎類別，封裝執行緒任務與 UI 更新邏輯。
維護注意：本模組提供 task_worker 給各 QC 檢查器使用。
"""

import flet as ft
import threading
from typing import Callable, Tuple, Any, Optional, List
from app.ui import theme


class QCBase:
    """QCBase 基礎類別。

    用途：封裝執行緒任務執行與 UI 更新邏輯，供各 QC 檢查器重用。
    維護注意：修改此類會影響所有使用 task_worker 的 QC 檢查器。
    """

    def __init__(self, page: ft.Page, progress_bar: ft.ProgressBar, log_view: ft.ListView):
        """初始化 QCBase。

        參數：
            page: Flet Page 物件
            progress_bar: 共用的 ProgressBar 元件
            log_view: 共用的 ListView 用於顯示日誌
        """
        self._page = page
        self.progress_bar = progress_bar
        self.log_view = log_view

    def task_worker(
        self,
        service_func: Callable[..., Any],
        args_tuple: Tuple[Any, ...],
        on_complete: Optional[Callable[[], None]] = None,
        controls_to_disable: Optional[List[ft.Control]] = None,
    ):
        """執行品質檢查服務工作執行緒。

        參數：
            service_func: 服務函式（需為 generator）
            args_tuple: 傳給服務函式的參數元組
            on_complete: 任務完成後的回調函式
            controls_to_disable: 任務執行期間要禁用的控制項列表
        """
        if controls_to_disable:
            for ctrl in controls_to_disable:
                ctrl.disabled = True
            self._page.update()

        def run():
            try:
                for update in service_func(*args_tuple):
                    log_msg = update.get("log", "")
                    for line in log_msg.split("\n"):
                        if line.strip():
                            async def _do_append(line=line):
                                self.log_view.controls.append(ft.Text(line))
                            self._page.run_task(_do_append, None)

                    if "progress" in update:
                        progress = update["progress"]
                        async def _do_progress(p=progress):
                            self.progress_bar.value = p
                        self._page.run_task(_do_progress, None)

                    if update.get("error"):
                        async def _do_err(_=None):
                            self.progress_bar.color = theme.ERROR
                        self._page.run_task(_do_err, None)

                    async def _do_scroll(_=None):
                        try:
                            await self.log_view.scroll_to(offset=-1, duration=100)
                        except TypeError:
                            self.log_view.scroll_to(offset=-1, duration=100)
                        self._page.update()
                    self._page.run_task(_do_scroll, None)
            finally:
                async def _do_finish(_=None):
                    self.progress_bar.value = 0
                    self.progress_bar.color = None
                    if controls_to_disable:
                        for ctrl in controls_to_disable:
                            ctrl.disabled = False
                    self._page.update()

                self._page.run_task(_do_finish, None)

                if on_complete:
                    on_complete()

        threading.Thread(target=run, daemon=True).start()

    @property
    def page(self):
        return self._page