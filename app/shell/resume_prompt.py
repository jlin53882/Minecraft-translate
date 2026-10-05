"""啟動時的「上次被中斷的任務」確認對話框（#151、ADR-0001 方案 C）。

- 偵測只讀一個小 JSON，不呼叫翻譯 API、不啟動任何任務。
- 使用者按下「續跑」之前不會開始翻譯；「放棄」會清除標記；「稍後」什麼都不做。
- 輸入與上次不同（指紋不符）時顯示無法續跑的原因，只能放棄或稍後，不會靜默忽略。
- 輸入比對要讀取輸入資料夾，所以在背景執行緒執行，對話框先顯示「檢查中」。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import flet as ft

from app.ui.design import C
from app.ui.snack import show_snack

logger = logging.getLogger("main_app")


def describe_task(task: Any) -> str:
    """對話框內文：上次任務的輸入、輸出與進度。"""
    lines = [f"輸入資料夾：{task.input_dir or '（未記錄）'}"]
    if task.output_dir:
        lines.append(f"輸出資料夾：{task.output_dir}")
    if task.total:
        lines.append(f"上次已處理：{task.completed} / {task.total} 筆")
    if task.updated_at:
        lines.append(f"最後更新：{task.updated_at}")
    lines.append("已完成的譯文保存在翻譯快取，續跑時只會翻譯尚未完成的部分。")
    return "\n".join(lines)


class ResumePrompt:
    """偵測並詢問是否續跑上次中斷的機器翻譯。

    相依都以參數注入（測試不需要真的 Flet Page、檔案或執行緒）：

    - ``find_task()``：回傳上次未完成的任務或 None（唯讀）。
    - ``check_resume(task)``：回傳有 ``ok`` / ``reason`` 的結果（會讀輸入資料夾，背景執行）。
    - ``discard()``：清除標記。
    - ``on_resume(task)``：使用者確認續跑時呼叫（切到機器翻譯頁並帶入選項）。
    - ``run_background(work, done)``：在背景執行 ``work()``，再把結果交給 ``done``
      （``done`` 必須在 UI 執行緒執行）。預設用 daemon thread + ``page.run_task``。
    """

    def __init__(
        self,
        page: Any,
        *,
        find_task: Callable[[], Any],
        check_resume: Callable[[Any], Any],
        discard: Callable[[], None],
        on_resume: Callable[[Any], None],
        run_background: Callable[[Callable[[], Any], Callable[[Any], None]], None]
        | None = None,
    ) -> None:
        self.page = page
        self._find_task = find_task
        self._check_resume = check_resume
        self._discard = discard
        self._on_resume = on_resume
        self._run_background = run_background or self._default_run_background
        self.dialog: ft.AlertDialog | None = None

    # -- 公開 ---------------------------------------------------------------

    def show_if_needed(self) -> bool:
        """有未完成的任務就顯示對話框並回傳 True；沒有、或頁面不支援對話框回傳 False。"""
        try:
            task = self._find_task()
        except Exception:
            logger.warning("偵測上次中斷的任務失敗", exc_info=True)
            return False
        if task is None:
            return False
        show_dialog = getattr(self.page, "show_dialog", None)
        if not callable(show_dialog):
            logger.warning("頁面不支援對話框，略過上次中斷任務的提示")
            return False

        self._status = ft.Text("檢查輸入內容是否與上次相同…", size=12, color=C.DIM)
        self._resume_button = ft.TextButton("續跑", disabled=True)
        self._resume_button.on_click = lambda _e=None: self._confirm(task)
        discard_button = ft.TextButton("放棄")
        discard_button.on_click = lambda _e=None: self._abandon()
        later_button = ft.TextButton("稍後再說")
        later_button.on_click = lambda _e=None: self._close()

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("上次有未完成的機器翻譯"),
            content=ft.Column(
                [ft.Text(describe_task(task)), self._status],
                tight=True,
                spacing=12,
            ),
            actions=[self._resume_button, discard_button, later_button],
        )
        show_dialog(self.dialog)
        self._run_background(
            lambda: self._check_resume(task),
            lambda result: self._apply_check(task, result),
        )
        return True

    # -- 內部 ---------------------------------------------------------------

    def _apply_check(self, task: Any, result: Any) -> None:
        """背景檢查完成：可續跑就啟用「續跑」，否則顯示原因（只能放棄或稍後）。"""
        if self.dialog is None:
            return  # 使用者已在檢查途中關掉對話框
        if getattr(result, "ok", False):
            self._status.value = "輸入內容與上次相同，可以續跑。"
            self._status.color = C.EM
            self._resume_button.disabled = False
        else:
            reason = getattr(result, "reason", "") or "原因不明"
            self._status.value = f"無法續跑：{reason}"
            self._status.color = C.RED
            self._resume_button.disabled = True
        self._update()

    def _confirm(self, task: Any) -> None:
        if self._resume_button.disabled:
            return
        self._close()
        self._on_resume(task)

    def _abandon(self) -> None:
        self._close()
        try:
            self._discard()
        except Exception:
            logger.warning("清除上次中斷的任務標記失敗", exc_info=True)
            show_snack(self.page, "無法清除上次任務的標記，請稍後再試", C.RED)
            return
        show_snack(self.page, "已放棄上次的任務（已翻譯的內容仍保留在快取）", C.GOLD)

    def _close(self) -> None:
        self.dialog = None
        try:
            self.page.pop_dialog()
        except Exception:
            logger.debug("關閉續跑對話框失敗", exc_info=True)

    def _update(self) -> None:
        try:
            self.page.update()
        except Exception:
            logger.debug("更新續跑對話框失敗", exc_info=True)

    def _default_run_background(
        self, work: Callable[[], Any], done: Callable[[Any], None]
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                logger.warning("檢查能否續跑時發生錯誤", exc_info=True)

                class _Failed:
                    ok = False
                    reason = f"檢查失敗：{exc}"

                result = _Failed()

            async def deliver() -> None:
                done(result)

            try:
                self.page.run_task(deliver)
            except Exception:
                logger.debug("無法回到 UI 執行緒更新續跑對話框", exc_info=True)

        threading.Thread(target=runner, daemon=True).start()
