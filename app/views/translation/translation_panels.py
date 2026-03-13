from __future__ import annotations

import flet as ft

from app.ui.components import primary_button, secondary_button, styled_card

def build_path_row(view, field: ft.TextField) -> ft.Control:
    return ft.Row(
        [
            ft.Container(expand=True, content=field),
            ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                icon_color=ft.Colors.BLUE_GREY_700,
                tooltip='選擇資料夾',
                on_click=lambda e: view._pick_directory_into(field),
            ),
        ],
        spacing=6,
    )

def build_action_row(*, view, on_start, on_dry_run, on_reset, trailing=None) -> ft.Control:
    controls = [
        primary_button('開始翻譯', icon=ft.Icons.PLAY_ARROW, tooltip='依照目前設定執行完整翻譯流程', on_click=on_start),
        secondary_button('Dry-run 開始模擬翻譯', icon=ft.Icons.SEARCH, tooltip='依照目前設定執行翻譯流程，但不實際修改檔案', on_click=on_dry_run),
        ft.TextButton('Reset', icon=ft.Icons.REFRESH, tooltip='重置輸入與輸出資料夾，並恢復所有步驟為預設值', on_click=on_reset),
    ]
    if trailing:
        controls.extend(trailing)
    return ft.Row(controls=controls, wrap=True, spacing=10)

def build_ftb_tab(view) -> ft.Control:
    view.ftb_in_dir = ft.TextField(label='輸入資料夾（模組包根目錄）', hint_text='例如：C:\\Modpack', expand=True, dense=True, border_color=ft.Colors.OUTLINE, text_size=14, content_padding=14, prefix_icon=ft.Icons.FOLDER)
    view.ftb_out_dir = ft.TextField(label='輸出資料夾（可選）', hint_text='留空使用 <input>/Output', expand=True, dense=True, border_color=ft.Colors.OUTLINE, text_size=14, content_padding=14, prefix_icon=ft.Icons.FOLDER_COPY)
    view.ftb_step_export = ft.Checkbox(label='Step 1：Export Raw（抽取）', value=True)
    view.ftb_step_clean = ft.Checkbox(label='Step 2：Clean（補洞/產生待翻譯）', value=True)
    view.ftb_step_translate = ft.Checkbox(label='Step 3：LM 翻譯（待翻譯 JSON）', value=True)
    view.ftb_step_inject = ft.Checkbox(label='Step 4：Inject（寫回 zh_tw/*.snbt）', value=True)
    view.ftb_write_new_cache = ft.Switch(label='寫入新快取（write_new_cache）', value=True)
    return ft.Column([
        styled_card(title='路徑設定', icon=ft.Icons.FOLDER, content=ft.Column([build_path_row(view, view.ftb_in_dir), build_path_row(view, view.ftb_out_dir)], spacing=10)),
        styled_card(title='步驟與選項', icon=ft.Icons.FACT_CHECK, content=ft.Column([view.ftb_step_export, view.ftb_step_clean, view.ftb_step_translate, view.ftb_step_inject], spacing=6)),
        build_action_row(view=view, on_start=lambda e: view._run_ftb(dry_run=False), on_dry_run=lambda e: view._run_ftb(dry_run=True), on_reset=lambda e: view._reset_ftb_inputs(), trailing=[view.ftb_write_new_cache]),
    ], spacing=12, expand=True)

def build_kjs_tab(view) -> ft.Control:
    view.kjs_in_dir = ft.TextField(label='輸入資料夾（模組包根目錄）', hint_text='例如：C:\\Modpack', expand=True, dense=True, border_color=ft.Colors.OUTLINE, text_size=14, content_padding=14, prefix_icon=ft.Icons.FOLDER)
    view.kjs_out_dir = ft.TextField(label='輸出資料夾（可選）', hint_text='留空使用 <input>/Output', expand=True, dense=True, border_color=ft.Colors.OUTLINE, text_size=14, content_padding=14, prefix_icon=ft.Icons.FOLDER_COPY)
    view.kjs_step_extract = ft.Checkbox(label='Step 1：Export Raw + Clean', value=True)
    view.kjs_step_translate = ft.Checkbox(label='Step 2：LM 翻譯（待翻譯 JSON）', value=True)
    view.kjs_step_inject = ft.Checkbox(label='Step 3：Inject 回 scripts', value=True)
    view.kjs_write_new_cache = ft.Switch(label='寫入新快取（write_new_cache）', value=True)
    return ft.Column([
        styled_card(title='路徑設定', icon=ft.Icons.FOLDER, content=ft.Column([build_path_row(view, view.kjs_in_dir), build_path_row(view, view.kjs_out_dir)], spacing=10)),
        styled_card(title='步驟與選項', icon=ft.Icons.FACT_CHECK, content=ft.Column([view.kjs_step_extract, view.kjs_step_translate, view.kjs_step_inject], spacing=6)),
        build_action_row(view=view, on_start=lambda e: view._run_kjs(dry_run=False), on_dry_run=lambda e: view._run_kjs(dry_run=True), on_reset=lambda e: view._reset_kjs_inputs(), trailing=[view.kjs_write_new_cache]),
    ], spacing=12, expand=True)

def build_md_tab(view) -> ft.Control:
    view.md_in_dir = ft.TextField(label='輸入資料夾（遞迴掃描 .md）', hint_text='例如：C:\\Modpack\\config\\patchouli_books', expand=True, dense=True, border_color=ft.Colors.OUTLINE, text_size=14, content_padding=14, prefix_icon=ft.Icons.FOLDER)
    view.md_out_dir = ft.TextField(label='輸出資料夾（可選）', hint_text='留空使用 <input>/Output/md', expand=True, dense=True, border_color=ft.Colors.OUTLINE, text_size=14, content_padding=14, prefix_icon=ft.Icons.FOLDER_COPY)
    view.md_step_extract = ft.Checkbox(label='Step 1：Extract（產生待翻譯）', value=True)
    view.md_step_translate = ft.Checkbox(label='Step 2：LM 翻譯（待翻譯 JSON）', value=True)
    view.md_step_inject = ft.Checkbox(label='Step 3：Inject（寫回 md）', value=True)
    view.md_write_new_cache = ft.Switch(label='寫入新快取（write_new_cache）', value=True)
    view.md_lang_mode = ft.Dropdown(label='抽取語言模式（lang_mode）', value='non_cjk_only', dense=True, options=[ft.dropdown.Option(key='non_cjk_only', text='僅抽取非中文（non_cjk_only）'), ft.dropdown.Option(key='cjk_only', text='僅抽取中文（cjk_only）'), ft.dropdown.Option(key='all', text='抽取全部（all）')])
    return ft.Column([
        styled_card(title='路徑設定', icon=ft.Icons.FOLDER, content=ft.Column([build_path_row(view, view.md_in_dir), build_path_row(view, view.md_out_dir), view.md_lang_mode], spacing=10)),
        styled_card(title='步驟與選項', icon=ft.Icons.FACT_CHECK, content=ft.Column([view.md_step_extract, view.md_step_translate, view.md_step_inject], spacing=6)),
        build_action_row(view=view, on_start=lambda e: view._run_md(dry_run=False), on_dry_run=lambda e: view._run_md(dry_run=True), on_reset=lambda e: view._reset_md_inputs(), trailing=[view.md_write_new_cache]),
    ], spacing=12, expand=True)
