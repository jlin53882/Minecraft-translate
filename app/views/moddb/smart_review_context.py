"""智慧校對視窗共用的 UI 上下文：世代、資料庫身分與背景操作的過期結果防護。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import flet as ft

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)


class SmartReviewContext:
    """集中管理智慧校對視窗的資料庫身分、UI 世代與背景操作。

    視窗內的各個分頁面板共用同一個上下文：關閉視窗會讓世代遞增，之後所有
    背景工作回來的結果都會被丟棄；資料庫被換掉或內容世代改變時同樣丟棄。
    """

    def __init__(self, page: ft.Page, get_db: Callable[[], Any], on_complete) -> None:
        """保存頁面、資料庫取得函式與上層完成通知。"""
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self.generation = 0
        self.closed = False

    def close(self) -> None:
        """標記視窗已關閉，並讓所有進行中的背景結果失效。"""
        self.closed = True
        self.generation += 1

    def notify_parent(self, result: Any = None) -> None:
        """通知上層頁面資料已變更（候選套用或執行完成後重新整理）。"""
        if not callable(self.on_complete):
            return
        try:
            self.on_complete(result) if result is not None else self.on_complete()
        except TypeError:
            self.on_complete()

    def database(self) -> tuple[Path, tuple[int, ...]] | None:
        """回傳目前資料庫路徑與來源優先序；沒有可用資料庫時回傳 None。"""
        db = self.get_db()
        if db is None or not getattr(db, "path", None):
            return None
        return Path(db.path).resolve(), tuple(getattr(db, "priority", ()))

    def db_identity(
        self, path: Path | None = None, *, include_generation: bool = True
    ) -> str | None:
        """回傳資料庫檔案身分，必要時附加可偵測內容變更的 cache generation。"""
        db = self.get_db()
        current_path = path
        if current_path is None and db is not None and getattr(db, "path", None):
            current_path = Path(db.path)
        if current_path is None:
            return None
        identity = str(current_path.resolve())
        try:
            stat = current_path.stat()
        except OSError:
            stat = None
        if stat is not None:
            identity += f"::{stat.st_dev}:{stat.st_ino}"
        if not include_generation:
            return identity
        generation_getter = getattr(db, "_cache_generation", None)
        if callable(generation_getter):
            try:
                identity += f"::{generation_getter()}"
            except (OSError, RuntimeError, sqlite3.Error):
                pass
        return identity

    def is_current(self, generation: int, db_identity: str | None = None) -> bool:
        """驗證結果仍屬於目前的 UI 世代與資料庫。"""
        if generation != self.generation:
            return False
        if db_identity is None:
            return True
        current = self.db_identity()
        stable = self.db_identity(include_generation=False)
        return db_identity in {current, stable}

    def post(self, callback: Callable[..., None], *args: Any) -> None:
        """把背景工作結果安全送回 UI 執行緒。"""
        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):

            async def runner() -> None:
                """在 UI 執行緒中呼叫回呼。"""
                callback(*args)

            run_task(runner)
        else:
            callback(*args)

    def update(self) -> None:
        """在 UI 仍然有效時安全更新頁面。"""
        try:
            self.page.update()
        except RuntimeError:
            pass

    def run_background(
        self,
        work: Callable[[], Any],
        on_done: Callable[[Any, str | None], None],
        *,
        name: str,
        commit: CommitPolicy = CommitPolicy.EPHEMERAL,
        durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
        stable_identity: bool = False,
        still_valid: Callable[[], bool] | None = None,
    ) -> bool:
        """在背景執行 ``work``，結果仍有效時才以 ``on_done(result, error)`` 回到 UI。

        ``stable_identity`` 用於會寫入資料庫的操作：寫入本身會讓 cache generation
        遞增，因此只比對檔案身分，內容衝突交給 service 層的 CAS。
        ``still_valid`` 讓呼叫端額外丟棄過期的查詢（例如已被新頁碼取代）。
        回傳是否成功啟動。
        """
        generation = self.generation
        identity = self.db_identity(include_generation=not stable_identity)

        def deliver(result: Any, error: str | None) -> None:
            """丟棄過期結果，其餘交給呼叫端處理。"""
            if self.closed or not self.is_current(generation, identity):
                return
            if still_valid is not None and not still_valid():
                return
            on_done(result, error)

        def target() -> None:
            """執行背景工作並把結果或錯誤送回 UI。"""
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self.post(deliver, None, str(exc) or type(exc).__name__)
                return
            self.post(deliver, result, None)

        try:
            launched = launch_page_operation(
                self.page,
                target,
                name=name,
                owner="moddb-smart-review",
                cancellation=CancellationPolicy.NON_CANCELLABLE
                if commit is not CommitPolicy.PARTIAL_ALLOWED
                else CancellationPolicy.COOPERATIVE,
                commit=commit,
                durability=durability,
                shutdown=ShutdownPolicy.CANCEL_AND_DRAIN,
            )
        except (AttributeError, RuntimeError) as exc:
            self.post(deliver, None, f"操作未啟動：{exc}")
            return False
        if not launched:
            self.post(deliver, None, "應用程式正在關閉，未啟動操作。")
            return False
        return True
