"""規則頁面的操作處理器與使用者提示組裝函式。"""

from __future__ import annotations

import asyncio
import math
import threading

import flet as ft

from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.design import C
from app.ui.dialogs import close_page_dialog, present_page_dialog
from app.ui.snack import show_snack
from app.ui.update_scope import update_regions
from translation_tool.utils.log_unit import log_error


def perform_reload(view):
    """执行规则重新加载，读取配置文件并更新界面"""
    try:
        rules_data = view._load_rules_core(force_reload=True)
        view._run_on_ui_thread(lambda: view._handle_reload_success(rules_data))
    except Exception as err:  # noqa: BLE001
        log_error(f"[Rules] 重新載入規則失敗：{err!r}", exc_info=True)
        view._run_on_ui_thread(lambda err=err: view._handle_reload_failure(err))


def start_reload_thread(view):
    """在后台线程启动规则重新加载流程"""
    view.loading_indicator.visible = True
    update_regions(view.loading_indicator)
    show_snack(view.page, "🔄 正在重新載入規則…", C.DIA)
    launched = launch_page_operation(
        view.page,
        lambda: perform_reload(view),
        name="規則重新載入",
        owner="rules",
        cancellation=CancellationPolicy.NON_CANCELLABLE,
        shutdown=ShutdownPolicy.DRAIN_ONLY,
    )
    if not launched:
        view.loading_indicator.visible = False
        update_regions(view.loading_indicator)
        show_snack(view.page, "應用程式正在關閉，無法啟動新工作", C.GOLD)


def confirm_discard_changes(page, on_confirm):
    """有未儲存的變更時，確認是否放棄並繼續（重新載入會覆蓋畫面）；回傳對話框。"""
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("放棄未儲存的變更？"),
        content=ft.Text(
            "重新載入會以磁碟上的 replace_rules.json 覆蓋畫面，"
            "尚未儲存的新增、刪除與修改都會遺失。"
        ),
    )

    def close(_e=None):
        close_page_dialog(page, dialog)

    def confirm(_e=None):
        close()
        on_confirm()

    dialog.actions = [
        ft.TextButton("取消", on_click=close),
        ft.TextButton(
            "放棄變更並重新載入",
            style=ft.ButtonStyle(color=C.RED),
            on_click=confirm,
        ),
    ]
    present_page_dialog(page, dialog)
    return dialog


def to_saved_rules(snapshot) -> list[dict]:
    """要寫進 replace_rules.json 的規則：拿掉 UI 專用的 _rid 與空白原詞，其他欄位原樣保留。"""
    return [
        {
            "from": rule.get("from", ""),
            "to": rule.get("to", ""),
            **{k: v for k, v in rule.items() if k not in ("from", "to", "_rid")},
        }
        for rule in snapshot
        if rule.get("from", "").strip()
    ]


def launch_validate_and_save(view, snapshot, finish, run_task):
    """在同一個 operation 內驗證並寫檔；結束後呼叫 ``finish(result)`` 回到 UI。"""
    result = {}
    worker_done = threading.Event()

    def validate_and_save():
        try:
            failure = view._validate_all(snapshot)
            result["failure"] = failure
            if failure is not None:
                return
            from app.services_impl.config_service import save_replace_rules

            save_replace_rules(to_saved_rules(snapshot), raise_on_error=True)
        except Exception as ex:
            result["error"] = ex
            raise
        finally:
            worker_done.set()
            if run_task is None:
                view._run_on_ui_thread(finish, result)

    operation = launch_page_operation(
        view.page,
        validate_and_save,
        name="規則驗證與儲存",
        owner="rules-save",
        cancellation=CancellationPolicy.NON_CANCELLABLE,
        commit=CommitPolicy.PARTIAL_ALLOWED,
        durability=DurabilityPolicy.USER_ACTION,
        shutdown=ShutdownPolicy.DRAIN_ONLY,
    )
    if not operation:
        view._saving = False
        show_snack(
            view.page,
            "應用程式正在關閉，未開始規則驗證或儲存",
            C.GOLD,
            text_color=C.ON_EM,
        )
        return

    if run_task is None:
        return

    async def _wait_for_validation_and_save():
        while not worker_done.is_set():
            await asyncio.sleep(0.02)
        finish(result)

    run_task(_wait_for_validation_and_save)


def calc_total_pages(total_rules: int, page_size: int) -> int:
    """计算规则列表的总页数"""
    return math.ceil(total_rules / page_size) if total_rules > 0 else 1
