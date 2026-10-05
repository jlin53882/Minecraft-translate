"""overlay 對話框的共用關閉流程。"""

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
