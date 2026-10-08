"""啟動時的「上次被中斷的任務」確認對話框（#151、#164、ADR-0001 方案 C）。

- 偵測只讀幾個小 JSON，不呼叫翻譯 API、不啟動任何任務。
- 可能同時有多個未完成的任務（機器翻譯、FTB、KubeJS、MD）：一個對話框列出全部，每個任務各自
  「續跑／放棄」；底部「稍後再說」關閉對話框、什麼都不做。
- 使用者按下某個任務的「續跑」之前不會開始任何翻譯；一次只續跑一個（避免同時搶快取與金鑰額度），
  其餘任務的標記保留，下次啟動會再詢問。
- 輸入與上次不同（指紋不符）時顯示無法續跑的原因，只能放棄或稍後，不會靜默忽略。
- 輸入比對要讀取輸入資料夾，所以每個任務在背景執行緒檢查，對話框先顯示「檢查中」。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from app.tasks.operation_registry import (
    CancellationPolicy,
    OperationPresentation,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.design import C
from app.ui.snack import show_snack

logger = logging.getLogger("main_app")


def describe_task(task: Any) -> str:
    """單一任務的說明：流程、輸入、輸出與已保存的進度。"""
    lines = []
    label = getattr(task, "label", "")
    if label:
        lines.append(f"任務：{label}")
    lines.append(f"輸入資料夾：{task.input_dir or '（未記錄）'}")
    if task.output_dir:
        lines.append(f"輸出資料夾：{task.output_dir}")
    if task.total:
        lines.append(f"已保存的進度：{task.completed} / {task.total} 筆")
    if task.updated_at:
        lines.append(f"最後更新：{task.updated_at}")
    return "\n".join(lines)


class _TaskRow:
    """對話框內的一個任務：說明、檢查狀態與「續跑／放棄」。"""

    def __init__(self, task: Any) -> None:
        self.task = task
        self.status = ft.Text("檢查輸入內容是否與上次相同…", size=12, color=C.DIM)
        self.resume_button = ft.TextButton("續跑", disabled=True)
        self.discard_button = ft.TextButton("放棄")
        self.view = ft.Column(
            [
                ft.Text(describe_task(task), size=13),
                self.status,
                ft.Row(
                    [self.resume_button, self.discard_button],
                    alignment=ft.MainAxisAlignment.END,
                ),
                ft.Divider(height=1),
            ],
            tight=True,
            spacing=6,
        )


class ResumePrompt:
    """偵測並詢問是否續跑上次中斷的翻譯任務。

    相依都以參數注入（測試不需要真的 Flet Page、檔案或執行緒）：

    - ``find_tasks()``：回傳上次未完成的任務清單（None 或空代表沒有；唯讀）。
    - ``check_resume(task)``：回傳有 ``ok`` / ``reason`` 的結果（會讀輸入資料夾，背景執行）。
    - ``discard(task)``：清除該任務的標記。
    - ``on_resume(task)``：使用者確認續跑時呼叫（切到對應頁面並帶入選項）。
    - ``run_background(work, done)``：在背景執行 ``work()``，再把結果交給 ``done``
      （``done`` 必須在 UI 執行緒執行）。預設用 daemon thread + ``page.run_task``。
    """

    def __init__(
        self,
        page: Any,
        *,
        find_tasks: Callable[[], Any],
        check_resume: Callable[[Any], Any],
        discard: Callable[[Any], None],
        on_resume: Callable[[Any], None],
        run_background: Callable[[Callable[[], Any], Callable[[Any], None]], None]
        | None = None,
    ) -> None:
        self.page = page
        self._find_tasks = find_tasks
        self._check_resume = check_resume
        self._discard = discard
        self._on_resume = on_resume
        self._run_background = run_background or self._default_run_background
        self.dialog: ft.AlertDialog | None = None
        self._rows: list[_TaskRow] = []
        self._body: ft.Column | None = None

    # -- 公開 ---------------------------------------------------------------

    def show_if_needed(self) -> bool:
        """有未完成的任務就顯示對話框並回傳 True；沒有、或頁面不支援對話框回傳 False。"""
        try:
            found = self._find_tasks()
        except Exception:
            logger.warning("偵測上次中斷的任務失敗", exc_info=True)
            return False
        tasks = list(found) if found else []
        if not tasks:
            return False
        show_dialog = getattr(self.page, "show_dialog", None)
        if not callable(show_dialog):
            logger.warning("頁面不支援對話框，略過上次中斷任務的提示")
            return False

        self._rows = [_TaskRow(task) for task in tasks]
        for row in self._rows:
            row.resume_button.on_click = lambda _e=None, r=row: self._confirm(r)
            row.discard_button.on_click = lambda _e=None, r=row: self._abandon(r)
        self._body = ft.Column(
            [row.view for row in self._rows],
            tight=True,
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )
        later = ft.TextButton("稍後再說")
        later.on_click = lambda _e=None: self._close()
        title = (
            "上次有未完成的翻譯"
            if len(tasks) == 1
            else f"上次有 {len(tasks)} 個未完成的翻譯任務"
        )
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(title),
            content=ft.Container(content=self._body, width=520),
            actions=[later],
        )
        show_dialog(self.dialog)
        for row in self._rows:
            self._run_background(
                lambda r=row: self._check_resume(r.task),
                lambda result, r=row: self._apply_check(r, result),
            )
        return True

    # -- 內部 ---------------------------------------------------------------

    def _apply_check(self, row: _TaskRow, result: Any) -> None:
        """背景檢查完成：可續跑就啟用「續跑」，否則顯示原因（只能放棄或稍後）。"""
        if self.dialog is None or row not in self._rows:
            return  # 使用者已關掉對話框或放棄了這個任務
        if getattr(result, "ok", False):
            row.status.value = "輸入內容與上次相同，可以續跑。"
            row.status.color = C.EM
            row.resume_button.disabled = False
        else:
            reason = getattr(result, "reason", "") or "原因不明"
            row.status.value = f"無法續跑：{reason}"
            row.status.color = C.RED
            row.resume_button.disabled = True
        self._update()

    def _confirm(self, row: _TaskRow) -> None:
        if row.resume_button.disabled:
            return
        self._close()
        self._on_resume(row.task)

    def _abandon(self, row: _TaskRow) -> None:
        try:
            self._discard(row.task)
        except Exception:
            logger.warning("清除上次中斷的任務標記失敗", exc_info=True)
            show_snack(self.page, "無法清除上次任務的標記，請稍後再試", C.RED)
            return
        if row in self._rows:
            self._rows.remove(row)
        if self._body is not None and row.view in self._body.controls:
            self._body.controls.remove(row.view)
        show_snack(self.page, "已放棄上次的任務（已翻譯的內容仍保留在快取）", C.GOLD)
        if self._rows:
            self._update()
        else:
            self._close()

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

        launch_page_operation(
            self.page,
            runner,
            name="檢查可續跑任務",
            owner="resume-prompt",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            presentation=OperationPresentation.MAINTENANCE,
        )
