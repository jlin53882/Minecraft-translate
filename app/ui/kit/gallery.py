"""UI kit 元件展示頁（開發用）。

    uv run python -m app.ui.kit.gallery

一次看完所有共用元件；右上角可切換深淺色。新增元件時，也在這裡加一個區塊，
方便檢查外觀是否與 ``docs/design/ui-redesign/`` 一致。
"""

from __future__ import annotations

import flet as ft

from app.ui import design, kit
from app.ui.design import C


def _block(title: str, *controls: ft.Control) -> ft.Control:
    return kit.section_card(
        title,
        ft.Column(list(controls), spacing=12, tight=True),
        icon=ft.Icons.WIDGETS_OUTLINED,
        tone="dia",
    )


def build_gallery() -> ft.Control:
    """組出展示內容（不依賴 Page，方便測試）。"""
    buttons, chips = _gallery_buttons_and_chips()
    stats = _gallery_stats()
    controls_row = _gallery_controls_row()
    bars, pager, states = _gallery_bars_pager_states()
    return ft.Column(
        [
            kit.page_header(
                "UI kit 元件",
                "新設計的共用元件；顏色全部來自設計系統，會跟著深淺色主題切換",
                icon=ft.Icons.WIDGETS_OUTLINED,
                tone="em",
                actions=[
                    kit.button("次要動作", "secondary", icon=ft.Icons.DOWNLOAD),
                    kit.button("主要動作", "primary", icon=ft.Icons.PLAY_ARROW),
                ],
            ),
            stats,
            _block("按鈕與標籤", buttons, chips),
            _block("開關列、分段切換、環形進度", controls_row),
            ft.Row(
                [
                    _block("進度條", bars),
                    kit.section_card("分頁器（表格底部）", pager, flush=True, expand=1),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            _block("空狀態 · 載入中 · 錯誤", states),
        ],
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def _gallery_buttons_and_chips():
    """按鈕與標籤展示。"""
    buttons = ft.Row(
        [
            kit.button("主要動作", "primary", icon=ft.Icons.PLAY_ARROW),
            kit.button("次要", "secondary", icon=ft.Icons.FOLDER_OPEN),
            kit.button("警示", "gold", icon=ft.Icons.TIMER),
            kit.button("危險", "danger", icon=ft.Icons.DELETE_OUTLINE),
            kit.button("幽靈", "ghost", icon=ft.Icons.REPLAY),
            kit.button("大 44", "primary", size="lg", icon=ft.Icons.ROCKET_LAUNCH),
            kit.button("小 30", "secondary", size="sm", icon=ft.Icons.ADD),
            kit.button("停用", "secondary", disabled=True),
        ],
        wrap=True,
        spacing=10,
        run_spacing=10,
    )
    chips = ft.Row(
        [
            kit.chip("完成", "em", icon=ft.Icons.CHECK),
            kit.chip("進行中", "gold", dot=True),
            kit.chip("資訊", "dia"),
            kit.chip("進階", "ench"),
            kit.chip("錯誤", "red", icon=ft.Icons.CLOSE),
            kit.chip("待處理"),
            kit.count_badge(3, "gold"),
            kit.count_badge(28, "red"),
            kit.kbd("Ctrl"),
            kit.kbd("P"),
        ],
        wrap=True,
        spacing=8,
        run_spacing=8,
    )
    return buttons, chips


def _gallery_stats():
    """統計卡展示。"""
    stats = ft.Row(
        [
            kit.stat_card(
                "模組總數",
                "412",
                icon=ft.Icons.INVENTORY_2_OUTLINED,
                tone="dia",
                delta="本週 +18 個新模組",
                expand=1,
            ),
            kit.stat_card(
                "已翻譯條目",
                "186,204",
                icon=ft.Icons.TRANSLATE,
                delta="今日 +3,912",
                expand=1,
            ),
            kit.stat_card(
                "快取命中率",
                "92.4%",
                icon=ft.Icons.STORAGE,
                tone="ench",
                delta="較昨日 +1.8%",
                expand=1,
            ),
            kit.stat_card(
                "今日 API 用量",
                "1,284 / 1,500",
                icon=ft.Icons.SPEED,
                tone="gold",
                delta="剩餘 216 次",
                delta_tone="gold",
                expand=1,
            ),
        ],
        spacing=14,
    )
    return stats


def _gallery_controls_row():
    """輸入控制項展示。"""
    controls_row = ft.Row(
        [
            ft.Container(
                width=420,
                content=ft.Column(
                    [
                        kit.SwitchRow(
                            "處理 zh_cn 路徑",
                            "主開關：關閉時所有 /zh_cn/ 路徑一律跳過",
                            True,
                        ),
                        kit.SwitchRow("略過已提取的模組", "依 JAR 雜湊判斷", False),
                        kit.SwitchRow(
                            "提取後自動開啟資料夾", None, True, divider=False
                        ),
                    ],
                    spacing=0,
                ),
            ),
            ft.Column(
                [
                    kit.Segmented(
                        [
                            ("lang", "Lang", ft.Icons.TRANSLATE),
                            ("book", "手冊", ft.Icons.MENU_BOOK),
                            ("dual", "雙模式", ft.Icons.LAYERS),
                        ],
                        "dual",
                    ),
                    kit.Segmented([("a", "ZIP 壓縮檔"), ("b", "資料夾")], "a"),
                ],
                spacing=12,
            ),
            ft.Row(
                [
                    kit.ProgressRing(0.87, size=96, stroke=9, tone="em", sub="已完成"),
                    kit.ProgressRing(
                        0.42,
                        size=96,
                        stroke=9,
                        tone="gold",
                        label="42%",
                        sub="步驟 3 / 4",
                    ),
                ],
                spacing=18,
            ),
        ],
        spacing=40,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )
    return controls_row


def _gallery_bars_pager_states():
    """進度條、分頁與狀態展示。"""
    bars = ft.Column(
        [
            kit.progress_bar(0.72, "em", height=8),
            kit.progress_bar(0.45, "gold", height=8),
            kit.progress_bar(0.88, "dia", height=8),
            kit.progress_bar(None, "ench", height=8),
        ],
        spacing=12,
        width=420,
    )
    pager = kit.Pager(1842, page=3, page_size=8)
    states = ft.Row(
        [
            ft.Container(
                expand=1,
                content=kit.empty_state(
                    "找不到符合的條目",
                    "試試更短的關鍵字，或放寬搜尋範圍",
                    action_text="清除篩選",
                ),
            ),
            ft.Container(expand=1, content=kit.loading_state("讀取快取索引中…")),
            ft.Container(
                expand=1,
                content=kit.error_state(
                    "無法讀取設定",
                    "config.json 格式錯誤（line 42）",
                    on_retry=lambda _e: None,
                ),
            ),
        ],
    )
    return bars, pager, states


def main(page: ft.Page) -> None:
    page.title = "UI kit"
    design.apply(page, "dark")
    page.padding = ft.Padding.symmetric(horizontal=28, vertical=22)

    def toggle(_e) -> None:
        design.apply(page, design.toggled_mode(page))
        page.update()

    page.appbar = None
    page.add(
        ft.Row(
            [
                ft.Text("深色 / 淺色", color=C.MUTED, size=12),
                ft.Switch(value=True, on_change=toggle),
            ],
            alignment=ft.MainAxisAlignment.END,
            spacing=8,
        ),
        build_gallery(),
    )


if __name__ == "__main__":
    ft.run(main)
