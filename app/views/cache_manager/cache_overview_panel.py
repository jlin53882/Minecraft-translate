"""app/views/cache_manager/cache_overview_panel.py：快取總覽頁（非查詢區）的版面組裝。

把大段 UI 結構從 cache_view.py 抽離，讓主檔專心處理事件與資料狀態。
外觀使用 ``app.ui.kit``（統計卡 + 區塊卡片），深 / 淺色主題皆適用。
"""

from __future__ import annotations

from collections.abc import Sequence

import flet as ft

from app.ui import kit

from .cache_log_panel import build_log_panel

HELP_LINES = (
    ("重新載入", "重新讀取全部分類快取（記憶體重建）"),
    ("刷新統計", "只刷新 UI 顯示數據，不做寫入"),
    ("重建搜尋索引", "建立全文搜尋索引（提升查詢速度 10~100 倍）"),
    ("新分片", "把該分類新資料寫到新 shard"),
    ("補滿舊檔", "回填既有 shard（覆寫模式）"),
    ("輪替分片", "強制切到下一個 active shard"),
    ("分析", "顯示該分類目前狀態與使用率"),
    ("切換查詢", "跳到查詢頁並帶入分類"),
)


def _build_actions_block(
    overview_status: ft.Control,
    overview_trace: ft.Control,
    btn_reload_all: ft.Control,
    btn_refresh_stats: ft.Control,
    btn_rebuild_index: ft.Control,
    chk_danger_confirm: ft.Control,
) -> ft.Control:
    """總覽頁的「操作」卡片。"""
    return kit.section_card(
        "操作",
        ft.Column(
            [
                overview_status,
                overview_trace,
                ft.Row(
                    [btn_reload_all, btn_refresh_stats, btn_rebuild_index],
                    wrap=True,
                    spacing=10,
                    run_spacing=10,
                ),
                chk_danger_confirm,
            ],
            spacing=8,
        ),
        icon=ft.Icons.TUNE,
        tone="em",
    )


def _build_help_block() -> ft.Control:
    """總覽頁的「按鈕說明」卡片（預設收合：展開時內容很長，會把下方日誌擠到看不見）。"""
    return kit.section_card(
        "按鈕說明",
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(name, size=12, weight=ft.FontWeight.W_600, width=96),
                        ft.Text(desc, size=12, expand=True),
                    ],
                    spacing=8,
                )
                for name, desc in HELP_LINES
            ],
            spacing=6,
        ),
        icon=ft.Icons.HELP_OUTLINE,
        tone="dia",
        collapsible=True,
        collapsed=True,
    )


def build_overview_page(
    *,
    overview_text: ft.Control,
    type_list: ft.Control,
    overview_status: ft.Control,
    overview_trace: ft.Control,
    btn_reload_all: ft.Control,
    btn_refresh_stats: ft.Control,
    btn_rebuild_index: ft.Control,  # A3 搜尋功能
    chk_danger_confirm: ft.Control,
    sw_log_only_error: ft.Control,
    btn_log_copy: ft.Control,
    btn_log_clear: ft.Control,
    log_list: ft.Control,
    page: ft.Page | None = None,
    stat_cards: Sequence[ft.Control] = (),
) -> ft.Control:
    """Cache 總覽頁（非查詢區）組裝。

    ``stat_cards`` 是頁面頂端的統計卡（由 CacheView 持有、載入資料後更新）。
    """
    actions_block = _build_actions_block(
        overview_status,
        overview_trace,
        btn_reload_all,
        btn_refresh_stats,
        btn_rebuild_index,
        chk_danger_confirm,
    )

    help_block = _build_help_block()

    left_panel = kit.section_card(
        "分類狀態清單",
        ft.Column(
            [
                kit.hint_text("卡片可捲動瀏覽，避免分類過多被截斷"),
                type_list,
            ],
            expand=True,
        ),
        icon=ft.Icons.STORAGE_OUTLINED,
        tone="ench",
        expand=True,
    )

    right_panel = ft.Column(
        [
            actions_block,
            help_block,
            build_log_panel(
                sw_log_only_error=sw_log_only_error,
                btn_log_copy=btn_log_copy,
                btn_log_clear=btn_log_clear,
                log_list=log_list,
            ),
        ],
        expand=True,
        spacing=12,
    )

    controls: list[ft.Control] = []
    if stat_cards:
        controls.append(ft.Row(list(stat_cards), spacing=14))
    controls.append(
        kit.section_card(
            "詳細資訊",
            overview_text,
            icon=ft.Icons.DASHBOARD_OUTLINED,
            tone="gold",
            collapsible=True,
            collapsed=True,
        )
    )
    controls.append(
        ft.ResponsiveRow(
            expand=True,
            controls=[
                ft.Container(col={"xs": 12, "md": 7}, expand=True, content=left_panel),
                ft.Container(col={"xs": 12, "md": 5}, expand=True, content=right_panel),
            ],
        )
    )
    return ft.Column(expand=True, spacing=14, controls=controls)
