"""任務翻譯工作台：FTB Quests、KubeJS Tooltips、Markdown 三個流水線分頁。

三個分頁都是同一種工作台（來源分析 → ModDB 同步 → LM 翻譯 → 輸出）：
FTB／KubeJS 使用 ``KubejsFtbPipelineView``，Markdown 使用 ``MarkdownPipelineView``，
與側欄的流水線頁共用實作，只差是否顯示頁首。
"""

import flet as ft

from app.services_impl.resume_service import discard_interrupted
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.kubejs_ftb_pipeline_view import KubejsFtbPipelineView
from app.views.md_pipeline_view import MarkdownPipelineView
from translation_tool.utils.log_unit import log_warning

_MD_TAB_INDEX = 2


class TranslationView(ft.Column):
    """翻譯工作台：FTB / KubeJS / Markdown 三流程統一入口。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=16)
        self._page = page
        self.file_picker = file_picker
        self.ftb_view = KubejsFtbPipelineView(
            page, file_picker, mode="ftb", embedded=True
        )
        self.kjs_view = KubejsFtbPipelineView(
            page, file_picker, mode="kubejs", embedded=True
        )
        self.md_view = MarkdownPipelineView(page, file_picker, embedded=True)
        self.tabs = self._build_source_tabs()
        self.controls = [
            kit.page_header(
                "任務翻譯工具",
                "選擇翻譯來源分頁：FTB Quests、KubeJS Tooltips 或 Markdown；三者都是相同的流水線流程。",
                icon=ft.Icons.TRANSLATE,
                tone="ench",
                responsive=True,
            ),
            self.tabs,
        ]

    def _build_source_tabs(self):
        """三個翻譯來源分頁：FTB Quests、KubeJS Tooltips、Markdown。"""
        tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="FTB Quests"),
                ft.Tab(label="KubeJS Tooltips"),
                ft.Tab(label="Markdown"),
            ],
            indicator_color=C.EM,
            label_color=C.EM,
            unselected_label_color=C.MUTED,
            divider_color=C.LINE,
        )
        tab_view = ft.TabBarView(
            controls=[self.ftb_view, self.kjs_view, self.md_view],
            expand=True,
        )
        tab_selector = kit.section_card(
            "翻譯來源",
            tab_bar,
            icon=ft.Icons.TRANSLATE,
            tone="ench",
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )
        return ft.Tabs(
            content=ft.Column([tab_selector, tab_view], expand=True, spacing=12),
            length=3,
            selected_index=0,
            expand=True,
            animation_duration=180,
        )

    def resume_interrupted(self, task) -> None:
        """啟動時偵測到上次中斷的舊版 Markdown 翻譯：流水線以 ModDB 為準，不再續跑舊流程。

        已翻譯的內容都在 ModDB 或翻譯快取內，在 Markdown 分頁重新執行流水線即可沿用。
        """
        kind = getattr(task, "kind", "")
        log_warning(f"[Resume] 任務翻譯頁不支援續跑類型：{kind!r}")
        if kind == "md":
            discard_interrupted(
                task
            )  # 舊流程的續跑標記不再使用，清掉以免每次啟動都詢問
            self.tabs.selected_index = _MD_TAB_INDEX
            show_snack(
                self._page,
                "Markdown 翻譯已改為流水線：請在 Markdown 分頁重新執行，已翻譯的內容會沿用",
                C.GOLD,
            )

    @property
    def page(self):
        """回傳 Flet Page 實例。"""
        return self._page
