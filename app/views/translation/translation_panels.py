"""任務翻譯頁的三個流程面板（FTB / KubeJS / Markdown）。

每個面板：路徑設定 → 流程步驟卡（可勾選）→ 操作列。步驟用 ``kit.StepCard``，
背後仍是 ``ft.Checkbox``，所以 ``view.ftb_step_export.value`` 這類讀寫維持不變。
"""

from __future__ import annotations

import flet as ft

from app.ui import kit


def build_path_row(view, field: ft.TextField) -> ft.Control:
    """建立路徑輸入欄位與資料夾選擇按鈕的橫向排列。"""
    return ft.Row(
        [
            field,
            kit.pick_button(
                ft.Icons.FOLDER_OPEN_OUTLINED,
                "選擇資料夾",
                lambda e: view._pick_directory_into(field),
            ),
        ],
        spacing=8,
    )


def build_action_row(
    *, view, on_start, on_dry_run, on_reset, trailing=None
) -> ft.Control:
    """建立翻譯操作按鈕列（開始、Dry-run、Reset）。"""
    controls = [
        kit.button(
            "開始翻譯",
            "primary",
            icon=ft.Icons.PLAY_ARROW,
            tooltip="依照目前設定執行完整翻譯流程",
            on_click=on_start,
        ),
        kit.button(
            "Dry-run 開始模擬翻譯",
            "secondary",
            icon=ft.Icons.SEARCH,
            tooltip="依照目前設定執行翻譯流程，但不實際修改檔案",
            on_click=on_dry_run,
        ),
        kit.button(
            "Reset",
            "ghost",
            icon=ft.Icons.REFRESH,
            tooltip="重置輸入與輸出資料夾，並恢復所有步驟為預設值",
            on_click=on_reset,
        ),
    ]
    if trailing:
        controls.extend(trailing)
    return ft.Row(
        controls=controls,
        wrap=True,
        spacing=10,
        run_spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _step_row(view, steps: list[tuple[ft.Checkbox, str, str, str, str]]) -> ft.Row:
    """把 (checkbox, 標題, 說明, 圖示, 色組) 排成一列步驟卡。"""
    cards = [
        kit.StepCard(box, index, title, desc, icon=icon, tone=tone)
        for index, (box, title, desc, icon, tone) in enumerate(steps, start=1)
    ]
    view._step_cards.extend(cards)  # reset 之後要靠它刷新外觀
    return ft.Row(cards, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START)


def _cache_switch(view, attr: str) -> ft.Control:
    """「寫入新快取」開關；Switch 本體掛到 ``view.<attr>`` 讓外部讀寫。"""
    row = kit.SwitchRow(
        "寫入新快取", "翻譯結果同步存入快取（write_new_cache）", True, divider=False
    )
    setattr(view, attr, row.switch)
    return row


def _tab(
    view, paths: list[ft.Control], steps: ft.Row, cache: ft.Control, actions: ft.Control
):
    return ft.Column(
        [
            kit.section_card(
                "路徑設定",
                ft.Column(paths, spacing=12),
                icon=ft.Icons.FOLDER_OPEN,
                tone="em",
            ),
            kit.section_card(
                "翻譯步驟",
                ft.Column([steps, cache], spacing=14),
                icon=ft.Icons.CHECKLIST,
                tone="ench",
            ),
            actions,
        ],
        spacing=16,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )


def build_ftb_tab(view) -> ft.Control:
    """建立 FTB (Forge 模組包) 翻譯面板的完整 UI。"""
    view.ftb_in_dir = kit.text_field(
        "輸入資料夾（模組包根目錄）",
        hint="例如：./mods",
        mono=True,
        expand=True,
        path=True,
    )
    view.ftb_out_dir = kit.text_field(
        "輸出資料夾（可選）",
        hint="留空使用 <input>/Output",
        mono=True,
        expand=True,
        path=True,
    )
    view.ftb_step_export = ft.Checkbox(value=True)
    view.ftb_step_clean = ft.Checkbox(value=True)
    view.ftb_step_translate = ft.Checkbox(value=True)
    view.ftb_step_inject = ft.Checkbox(value=True)
    cache = _cache_switch(view, "ftb_write_new_cache")
    steps = _step_row(
        view,
        [
            (
                view.ftb_step_export,
                "匯出",
                "Export Raw：抽取任務文字",
                ft.Icons.DOWNLOAD,
                "dia",
            ),
            (
                view.ftb_step_clean,
                "清理",
                "Clean：補洞 / 產生待翻譯",
                ft.Icons.AUTO_FIX_HIGH,
                "ench",
            ),
            (
                view.ftb_step_translate,
                "翻譯",
                "LM 翻譯（待翻譯 JSON）",
                ft.Icons.AUTO_AWESOME,
                "gold",
            ),
            (
                view.ftb_step_inject,
                "寫回",
                "Inject：寫回 zh_tw/*.snbt",
                ft.Icons.UPLOAD,
                "em",
            ),
        ],
    )
    actions = build_action_row(
        view=view,
        on_start=lambda e: view._run_ftb(dry_run=False),
        on_dry_run=lambda e: view._run_ftb(dry_run=True),
        on_reset=lambda e: view._reset_ftb_inputs(),
    )
    return _tab(
        view,
        [build_path_row(view, view.ftb_in_dir), build_path_row(view, view.ftb_out_dir)],
        steps,
        cache,
        actions,
    )


def build_kjs_tab(view) -> ft.Control:
    """建立 KubeJS 翻譯面板的完整 UI。"""
    view.kjs_in_dir = kit.text_field(
        "輸入資料夾（模組包根目錄）",
        hint="例如：./mods",
        mono=True,
        expand=True,
        path=True,
    )
    view.kjs_out_dir = kit.text_field(
        "輸出資料夾（可選）",
        hint="留空使用 <input>/Output",
        mono=True,
        expand=True,
        path=True,
    )
    view.kjs_step_extract = ft.Checkbox(value=True)
    view.kjs_step_translate = ft.Checkbox(value=True)
    view.kjs_step_inject = ft.Checkbox(value=True)
    cache = _cache_switch(view, "kjs_write_new_cache")
    steps = _step_row(
        view,
        [
            (
                view.kjs_step_extract,
                "匯出與清理",
                "Export Raw + Clean",
                ft.Icons.DOWNLOAD,
                "dia",
            ),
            (
                view.kjs_step_translate,
                "翻譯",
                "LM 翻譯（待翻譯 JSON）",
                ft.Icons.AUTO_AWESOME,
                "gold",
            ),
            (view.kjs_step_inject, "寫回", "Inject 回 scripts", ft.Icons.UPLOAD, "em"),
        ],
    )
    actions = build_action_row(
        view=view,
        on_start=lambda e: view._run_kjs(dry_run=False),
        on_dry_run=lambda e: view._run_kjs(dry_run=True),
        on_reset=lambda e: view._reset_kjs_inputs(),
    )
    return _tab(
        view,
        [build_path_row(view, view.kjs_in_dir), build_path_row(view, view.kjs_out_dir)],
        steps,
        cache,
        actions,
    )


def build_md_tab(view) -> ft.Control:
    """建立 Markdown (Patchouli) 翻譯面板的完整 UI。"""
    view.md_in_dir = kit.text_field(
        "輸入資料夾（遞迴掃描 .md）",
        hint="例如：./config/patchouli_books",
        mono=True,
        expand=True,
        path=True,
    )
    view.md_in_dir.helper = "只處理路徑中含 en_us / zh_tw 資料夾的 .md（例如 docs/en_us/intro.md）；程式碼區塊不會送翻譯"
    view.md_out_dir = kit.text_field(
        "輸出資料夾（可選）",
        hint="留空使用 <input>/Output/md",
        mono=True,
        expand=True,
        path=True,
    )
    view.md_step_extract = ft.Checkbox(value=True)
    view.md_step_translate = ft.Checkbox(value=True)
    view.md_step_inject = ft.Checkbox(value=True)
    cache = _cache_switch(view, "md_write_new_cache")
    view.md_lang_mode = ft.Dropdown(
        label="抽取語言模式（lang_mode）",
        value="non_cjk_only",
        dense=True,
        options=[
            ft.dropdown.Option(key="non_cjk_only", text="僅抽取非中文（non_cjk_only）"),
            ft.dropdown.Option(key="cjk_only", text="僅抽取中文（cjk_only）"),
            ft.dropdown.Option(key="all", text="抽取全部（all）"),
        ],
    )
    steps = _step_row(
        view,
        [
            (
                view.md_step_extract,
                "抽取",
                "Extract：產生待翻譯",
                ft.Icons.DOWNLOAD,
                "dia",
            ),
            (
                view.md_step_translate,
                "翻譯",
                "LM 翻譯（待翻譯 JSON）",
                ft.Icons.AUTO_AWESOME,
                "gold",
            ),
            (view.md_step_inject, "寫回", "Inject：寫回 md", ft.Icons.UPLOAD, "em"),
        ],
    )
    actions = build_action_row(
        view=view,
        on_start=lambda e: view._run_md(dry_run=False),
        on_dry_run=lambda e: view._run_md(dry_run=True),
        on_reset=lambda e: view._reset_md_inputs(),
    )
    return _tab(
        view,
        [
            build_path_row(view, view.md_in_dir),
            build_path_row(view, view.md_out_dir),
            view.md_lang_mode,
        ],
        steps,
        cache,
        actions,
    )
