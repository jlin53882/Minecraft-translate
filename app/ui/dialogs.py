"""對話框的共用顯示／關閉流程（單一對話框與多步 Wizard 都用這裡）。"""

import flet as ft


def close_overlay_dialog(page: ft.Page, dialog: ft.AlertDialog) -> None:
    """關閉並移除 overlay 內的對話框。

    必須先把 ``open=False`` 送到前端（``page.update``），再從 overlay 移除；
    直接移除會讓前端的 dialog route 留在畫面上（殘影並擋住整個頁面）。
    只設 ``open=False`` 不移除則每次開啟都會多留一個已關閉的對話框在 overlay。
    """
    dialog.open = False
    page.update()
    if dialog in page.overlay:
        page.overlay.remove(dialog)
        page.update()


def dispose_dialogs(ctx) -> None:
    """關閉並移除 ``ctx.dialogs`` 內的對話框（``ctx`` 需有 ``page``、``dialogs``、可選 ``uses_dialog_api``）。

    必須先把 ``open=False`` 送到前端（page.update），再從 overlay 移除；
    直接移除會讓前端的 dialog route 留在畫面上（殘影＋擋住整個頁面）。
    """
    for d in ctx.dialogs:
        d.open = False
    ctx.page.update()

    removed = False
    if getattr(ctx, "uses_dialog_api", False) and ctx.dialogs:
        # page.pop_dialog() 會同步清理 Flet 內部的 dialog stack；若測試替身
        # 仍把對話框放在 overlay，下面的相容移除則確保兩種環境結果一致。
        ctx.page.pop_dialog()
        removed = True
    for d in ctx.dialogs:
        if d in ctx.page.overlay:
            ctx.page.overlay.remove(d)
            removed = True
    ctx.dialogs.clear()
    if removed:
        # 移除後也要再推一次：否則緊接著的 SnackBar／進度面板（輸入驗證失敗時）
        # 會與「移除 overlay」擠在同一次更新，對話框遮罩殘留、提示被蓋住。
        ctx.page.update()


def present_dialog(ctx, dialog) -> None:
    """使用 Flet 對話框生命週期 API 顯示一步 Wizard。"""
    ctx.dialogs.append(dialog)
    if getattr(ctx, "uses_dialog_api", False):
        ctx.page.show_dialog(dialog)
    else:
        ctx.page.overlay.append(dialog)
        dialog.open = True
        ctx.page.update()
