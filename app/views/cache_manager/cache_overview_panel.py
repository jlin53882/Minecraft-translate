"""app/views/cache_manager/cache_overview_panel.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

from __future__ import annotations

import flet as ft

from .cache_log_panel import build_log_panel
from .cache_shared_widgets import bordered_block


def build_overview_page(
    *,
    overview_text: ft.Control,
    type_list: ft.Control,
    overview_status: ft.Control,
    overview_trace: ft.Control,
    btn_reload_all: ft.Control,
    btn_refresh_stats: ft.Control,
    btn_rebuild_index: ft.Control,  # A3 搜尋功能
    sw_log_only_error: ft.Control,
    btn_log_copy: ft.Control,
    btn_log_clear: ft.Control,
    log_list: ft.Control,
) -> ft.Control:
    """Cache 總覽頁（非查詢區）組裝。

    把大段 UI 結構從 cache_view.py 抽離，讓主檔更容易閱讀與維護。
    """

    help_block = bordered_block(
        content=ft.Column(
            [
                overview_status,
                overview_trace,
                ft.Row([btn_reload_all, btn_refresh_stats, btn_rebuild_index], wrap=True),
                ft.Divider(height=8),
                ft.Text("按鈕說明", weight=ft.FontWeight.BOLD),
                ft.Text("重新載入：重新讀取全部分類快取（記憶體重建）", size=11, color=ft.Colors.GREY_700),
                ft.Text("刷新統計：只刷新 UI 顯示數據，不做寫入", size=11, color=ft.Colors.GREY_700),
                ft.Text("🔍 重建搜尋索引：建立全文搜尋索引（提升查詢速度 10~100 倍）", size=11, color=ft.Colors.BLUE_700),
                ft.Text("分類卡按鈕（在左側每張卡片上）", size=11, color=ft.Colors.GREY_700),
                ft.Text("• 重新載入：只重載該分類", size=11, color=ft.Colors.GREY_700),
                ft.Text("• 新分片：把該分類新資料寫到新 shard", size=11, color=ft.Colors.GREY_700),
                ft.Text("• 補滿舊檔：回填既有 shard（覆寫模式）", size=11, color=ft.Colors.GREY_700),
                ft.Text("• 輪替分片：強制切到下一個 active shard", size=11, color=ft.Colors.GREY_700),
                ft.Text("• 分析：顯示該分類目前狀態與使用率", size=11, color=ft.Colors.GREY_700),
                ft.Text("• 切換查詢：跳到查詢頁並帶入分類", size=11, color=ft.Colors.GREY_700),
            ],
            spacing=8,
        ),
    )

    left_panel = bordered_block(
        expand=True,
        content=ft.Column(
            [
                ft.Text("分類狀態清單", weight=ft.FontWeight.BOLD),
                ft.Text("卡片可捲動瀏覽，避免分類過多被截斷", size=11, color=ft.Colors.GREY_700),
                type_list,
            ],
            expand=True,
        ),
    )

    right_panel = ft.Column(
        [
            help_block,
            build_log_panel(
                sw_log_only_error=sw_log_only_error,
                btn_log_copy=btn_log_copy,
                btn_log_clear=btn_log_clear,
                log_list=log_list,
            ),
        ],
        expand=True,
        spacing=8,
    )

    return ft.Column(
        expand=True,
        spacing=10,
        controls=[
            bordered_block(content=overview_text),
            ft.ResponsiveRow(
                expand=True,
                controls=[
                    ft.Container(col={"xs": 12, "md": 7}, expand=True, content=left_panel),
                    ft.Container(col={"xs": 12, "md": 5}, expand=True, content=right_panel),
                ],
            ),
        ],
    )
