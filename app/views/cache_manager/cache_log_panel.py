"""app/views/cache_manager/cache_log_panel.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import flet as ft

from .cache_shared_widgets import bordered_block


def build_log_panel(
    *,
    sw_log_only_error: ft.Control,
    btn_log_copy: ft.Control,
    btn_log_clear: ft.Control,
    log_list: ft.Control,
    height: int = 260,
) -> ft.Container:
    """Cache 總覽頁右側日誌區塊（非查詢功能）。"""

    return ft.Container(
        height=height,
        content=bordered_block(
            expand=True,
            content=ft.Column(
                [
                    ft.Row([
                        ft.Text("日誌", weight=ft.FontWeight.BOLD),
                        sw_log_only_error,
                        btn_log_copy,
                        btn_log_clear,
                    ], wrap=True),
                    ft.Container(expand=True, content=log_list),
                ],
                expand=True,
            ),
        ),
    )
