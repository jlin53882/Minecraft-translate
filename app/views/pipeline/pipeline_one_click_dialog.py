"""一鍵製作 Wizard 對話框

用途：
- 依序顯示 4 個步驟對話框（1/4 → 2/4 → 3/4 → 4/4）
- 每個步驟顯示唯讀的 input_path / output_path
- Step 1：執行模式、語言代碼勾選
- Step 2：語系比對設定（folder/zip 切換）
- Step 3：翻譯設定（Dry Run、寫入新快取）
- Step 4：打包設定（ZIP 檔名、版本、封面圖片）
- 按「確定執行」後回呼叫 on_execute，所有設定資料透過回調傳回
- 邏輯不串接：只收集設定，回調外層實作
"""

import flet as ft
import os
import json

from app.ui.theme import (
    BLUE_600, BLUE_700, GREEN_700, TEAL_700, PURPLE_700,
    GREY_500, GREY_600, CYAN_700, GREEN_600,
    WHITE, BLUE_50, GREY_200, BLUE_400,
    GREEN_50, RED_400, RED_50,
)
from translation_tool.utils.config_manager import load_config


def _load_version_data():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "translation_tool",
        "core",
        "resource_pack_version.json",
    )
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def open_one_click_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    input_path: str,
    output_path: str,
    on_execute,
    show_snack_bar,
):
    """打開一鍵製作 Wizard。

    Args:
        page: Flet Page 實例
        file_picker: Flet FilePicker 實例
        input_path: Mod 來源路徑（唯讀）
        output_path: 輸出目錄路徑（唯讀）
        on_execute: 回調：(config: dict) -> void
            config 結構：
            {
                "mode": str,           # "lang" | "book" | "dual"
                "lang_codes": list[str],
                "only_lang": bool,
                "process_zh_cn": bool,
                "patchouli_skip": bool,
                "patchouli_threshold": float,
                "zh_en_threshold": int,
                "dry_run": bool,
                "write_new_cache": bool,
                "description": str,
                "version": str,
                "pack_image": str | None,
                "extra_folders": list[str],
                "zip_output": str,
            }
        show_snack_bar: 回調：(message: str, color: str) -> void
    """

    cfg = load_config()
    lang_merger_cfg = cfg.get("lang_merger", {})
    bundler_cfg = cfg.get("output_bundler", {})
    jar_extractor_cfg = cfg.get("jar_extractor", {})

    lang_codes_default = jar_extractor_cfg.get("lang_codes", ["en_us", "zh_cn", "zh_tw"])
    organized_folder = lang_merger_cfg.get("pending_organized_folder_name", "待翻譯整理需翻譯")
    translate_output_subfolder = lang_merger_cfg.get("lm_translate_folder_name", "_翻譯輸出")
    output_zip_name = bundler_cfg.get("output_zip_name", "可使用翻譯.zip")

    state = {
        "step": 1,
        "mode": "lang",
        "lang_codes": {code: True for code in lang_codes_default},
        "only_lang": True,
        "process_zh_cn": True,
        "patchouli_skip": lang_merger_cfg.get("patchouli_skip_en_us_when_zh_cn_exists", False),
        "patchouli_threshold": lang_merger_cfg.get("patchouli_effective_translation_threshold", 0.5),
        "zh_en_threshold": lang_merger_cfg.get("zh_en_letter_threshold", 2),
        "dry_run": False,
        "write_new_cache": True,
        "description": "",
        "version": "",
        "pack_image": None,
        "extra_folders": [],
        "zip_output": os.path.join(output_path, output_zip_name) if output_path else "",
        "merge_input_mode": "folder",
        "merge_selected_zips": [],
        "translate_input": "",
        "translate_output": os.path.join(output_path, "lm_translate") if output_path else "",
        "bundle_input": os.path.join(output_path, "lm_translate", translate_output_subfolder) if output_path else "",
    }

    dialogs: list[ft.AlertDialog] = []
    step_label = ft.Text(f"{state['step']}/4", size=12, color=GREY_600, weight=ft.FontWeight.W_500)
    wizard_content: ft.Container = None

    def rebuild_ui():
        for d in list(dialogs):
            d.open = False
            if d in page.overlay:
                page.overlay.remove(d)
        dialogs.clear()

        step = state["step"]

        step_label.value = f"{step}/4"

        dlg = build_dialog(step)
        dialogs.append(dlg)
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def close_all():
        for d in list(dialogs):
            d.open = False
            if d in page.overlay:
                page.overlay.remove(d)
        dialogs.clear()
        page.update()

    def _build_step_content(step: int):
        if step == 1:
            return _build_step1()
        elif step == 2:
            return _build_step2()
        elif step == 3:
            return _build_step3()
        elif step == 4:
            return _build_step4()

    def _build_step1():
        radio_group = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(label="提取 Lang", value="lang"),
                ft.Radio(label="提取 Book", value="book"),
                ft.Radio(label="全部執行（Lang + Book）", value="both"),
            ], spacing=4),
            value=state["mode"],
        )

        def on_mode_changed(e):
            state["mode"] = e.control.value

        radio_group.on_change = on_mode_changed

        lang_checks = {}
        for code in lang_codes_default:
            cb = ft.Checkbox(label=code, value=state["lang_codes"].get(code, True))
            lang_checks[code] = cb

        def on_lang_check(e=None):
            for code, cb in lang_checks.items():
                state["lang_codes"][code] = cb.value

        lang_section = ft.Column([lang_checks[code] for code in lang_codes_default], spacing=2)

        return ft.Column([
            ft.Text("Mod 來源（唯讀）", weight="bold", size=13),
            ft.Text(input_path or "未設定", size=11, color=GREY_600),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(output_path or "未設定", size=11, color=GREY_600),
            ft.Divider(),
            ft.Text("執行模式", weight="bold", size=13),
            radio_group,
            ft.Text("處理的語言代碼", weight="bold", size=13),
            ft.Container(content=lang_section),
        ], spacing=10, tight=False)

    def _build_step2():
        def on_input_mode_changed(e=None):
            state["merge_input_mode"] = e.control.value if e else "folder"

        input_mode_group = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(label="資料夾", value="folder"),
                ft.Radio(label="ZIP", value="zip"),
            ], spacing=4),
            value=state["merge_input_mode"],
            on_change=on_input_mode_changed,
        )

        folder_field = ft.TextField(
            label="Mod 來源",
            hint_text="留空使用上方設定的路徑",
            value=input_path,
            expand=True,
            border_color=TEAL_700,
            read_only=True,
        )

        zip_list_view = ft.ListView(expand=False, height=80, spacing=2)
        for path in state["merge_selected_zips"]:
            name = os.path.basename(path)
            zip_list_view.controls.append(
                ft.Row([
                    ft.Text(name, expand=True, size=12),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE, icon_size=16,
                        on_click=lambda e, p=path: _remove_zip(p),
                    ),
                ])
            )

        def _remove_zip(path: str):
            if path in state["merge_selected_zips"]:
                state["merge_selected_zips"].remove(path)
                rebuild_ui()

        patchouli_skip_cb = ft.Switch(
            label="允許 zh_cn 觸發跳過 en_us",
            value=state["patchouli_skip"],
        )

        def on_patchouli_skip(e):
            state["patchouli_skip"] = e.control.value

        patchouli_skip_cb.on_change = on_patchouli_skip

        patchouli_thresh_field = ft.TextField(
            value=str(state["patchouli_threshold"]),
            width=100,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            hint_text="空白用預設值",
        )

        zh_en_field = ft.TextField(
            value=str(state["zh_en_threshold"]),
            width=80,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            hint_text="空白用預設值",
        )

        return ft.Column([
            ft.Text("Mod 來源", weight="bold", size=13),
            input_mode_group,
            ft.Container(content=folder_field),
            ft.Container(content=zip_list_view),
            ft.Divider(),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(output_path or "未設定", size=11, color=GREY_600),
            ft.Divider(),
            ft.Text("語系過濾設定", weight="bold", size=13),
            ft.Switch(label="只處理 lang 檔案", value=state["only_lang"]),
            ft.Switch(label="處理 zh_cn 檔案", value=state["process_zh_cn"]),
            ft.Divider(),
            ft.Text("zh 英文含量閾值", weight=ft.FontWeight.W_500, size=12),
            zh_en_field,
            ft.Divider(),
            ft.Text("Patchouli 進階設定", weight="bold", size=13),
            ft.Column([
                ft.Text("允許 zh_cn 觸發跳過 en_us", weight=ft.FontWeight.W_500, size=12),
                patchouli_skip_cb,
                ft.Text("en_us 跳過門檻", weight=ft.FontWeight.W_500, size=12),
                patchouli_thresh_field,
            ]),
        ], spacing=10, tight=False)

    def _build_step3():
        dry_run_sw = ft.Switch(
            label="Dry Run（只分析不翻譯）",
            value=state["dry_run"],
        )

        def on_dry_run(e):
            state["dry_run"] = e.control.value

        dry_run_sw.on_change = on_dry_run

        write_cache_sw = ft.Switch(
            label="寫入新快取（每次回傳單獨快取）",
            value=state["write_new_cache"],
        )

        def on_write_cache(e):
            state["write_new_cache"] = e.control.value

        write_cache_sw.on_change = on_write_cache

        translate_input_field = ft.TextField(
            label="翻譯目標",
            hint_text=f"自動帶入整理後的待翻譯資料夾",
            value=state["translate_input"],
            expand=True,
            border_color=BLUE_700,
        )
        translate_output_field = ft.TextField(
            label="輸出目錄",
            hint_text=f"自動帶入：{{output}}/lm_translate/",
            value=state["translate_output"],
            expand=True,
            border_color=BLUE_700,
        )

        return ft.Column([
            ft.Text("翻譯目標（唯讀）", weight="bold", size=13),
            ft.Text(
                os.path.join(output_path, "locale_sort", "_整理輸出", organized_folder) if output_path else "未設定",
                size=11, color=GREY_600,
            ),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(os.path.join(output_path, "lm_translate") if output_path else "未設定", size=11, color=GREY_600),
            ft.Divider(),
            ft.Text("執行選項", weight="bold", size=13),
            dry_run_sw,
            write_cache_sw,
        ], spacing=10, tight=False)

    def _build_step4():
        bundle_input_field = ft.TextField(
            label="輸入來源",
            hint_text="自動帶入翻譯完成後的輸出",
            value=state["bundle_input"],
            expand=True,
            border_color=PURPLE_700,
        )
        zip_output_field = ft.TextField(
            label="輸出 ZIP 檔案",
            value=state["zip_output"],
            expand=True,
            border_color=PURPLE_700,
        )
        desc_field = ft.TextField(
            label="檔案敘述",
            hint_text="直接輸入文字，或使用 § 顏色代碼",
            value=state["description"],
            expand=True,
            border_color=PURPLE_700,
        )
        pack_image_field = ft.TextField(
            label="封面圖片（可留空）",
            value=state["pack_image"] or "",
            expand=True,
            border_color=PURPLE_700,
            read_only=True,
        )

        version_data = _load_version_data()
        version_toggle_label = ft.Text(state["version"] or "點擊選擇版本", expand=True, size=12, color=GREY_600)
        version_expanded = False
        version_list = ft.ListView(expand=True, height=140, spacing=4)

        def _refresh_versions(search=""):
            version_list.controls.clear()
            filtered = [v for v in version_data.keys() if search.lower() in v.lower()]
            if not filtered:
                version_list.controls.append(ft.Text("無可用版本", size=12, color=GREY_500))
            for v in filtered:
                version_list.controls.append(
                    ft.Container(
                        content=ft.Text(v, size=13),
                        padding=8,
                        border=ft.Border.all(1, GREY_500),
                        border_radius=6,
                        on_click=lambda e, ver=v: _select_version(ver),
                    )
                )

        def _select_version(v: str):
            state["version"] = v
            version_toggle_label.value = v
            version_toggle_label.color = None
            page.update()

        _refresh_versions()

        def _toggle_version(e=None):
            nonlocal version_expanded
            version_expanded = not version_expanded
            version_dropdown.visible = version_expanded
            page.update()

        version_dropdown = ft.Container(
            content=version_list,
            height=140,
            border=ft.Border.all(1, GREY_500),
            border_radius=6,
            padding=4,
            visible=False,
        )

        extra_view = ft.ListView(height=60, spacing=2)

        def _refresh_extra():
            extra_view.controls.clear()
            for path in state["extra_folders"]:
                extra_view.controls.append(
                    ft.Row([
                        ft.Text(os.path.basename(path), expand=True, size=12),
                        ft.IconButton(icon=ft.Icons.CLOSE, icon_size=14,
                                      on_click=lambda e, p=path: _remove_extra(p)),
                    ])
                )

        def _remove_extra(p: str):
            if p in state["extra_folders"]:
                state["extra_folders"].remove(p)
                _refresh_extra()
                page.update()

        _refresh_extra()

        def _add_extra(e=None):
            async def do():
                result = await file_picker.get_directory_path()
                if result and result not in state["extra_folders"]:
                    state["extra_folders"].append(result)
                    _refresh_extra()
                    page.update()
            page.run_task(do)

        return ft.Column([
            ft.Text("輸入來源", weight="bold", size=13),
            ft.Row([bundle_input_field]),
            ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
            ft.Row([zip_output_field]),
            ft.Text("檔案敘述", weight="bold", size=13),
            desc_field,
            ft.Text("Minecraft 版本", weight="bold", size=13),
            ft.Container(
                content=ft.Row([
                    version_toggle_label,
                    ft.Icon(ft.Icons.EXPAND_MORE, size=18),
                ]),
                padding=8,
                border=ft.Border.all(1, GREY_500),
                border_radius=6,
                on_click=_toggle_version,
            ),
            version_dropdown,
            ft.Text("封面圖片（可留空）", weight="bold", size=13),
            ft.Row([pack_image_field]),
            ft.Text("其他指定資料夾", weight="bold", size=13),
            ft.Container(content=extra_view, border=ft.Border.all(1, GREY_500), border_radius=6, padding=4),
            ft.Button("+ 新增資料夾", icon=ft.Icons.FOLDER_OPEN, on_click=_add_extra),
        ], spacing=10, tight=False)

    dialog_width = int(page.width * 0.6)

    step_label = ft.Text(f"{state['step']}/4", size=12, color=GREY_600, weight=ft.FontWeight.W_500)

    def build_dialog(step: int):
        titles = {
            1: "📦 抽取資源設定",
            2: "🔍 語系比對設定",
            3: "🔄 啟動翻譯設定",
            4: "📦 打包資源設定",
        }

        actions = []
        if step > 1:
            actions.append(
                ft.TextButton("上一個", on_click=lambda e: _go_prev())
            )
        if step < 4:
            actions.append(
                ft.TextButton("下一個", on_click=lambda e: _go_next())
            )
        else:
            actions.append(
                ft.Button("確定執行", icon=ft.Icons.CHECK, bgcolor=GREEN_700, color=WHITE,
                          on_click=lambda e: _do_execute())
            )
        actions.append(
            ft.TextButton("取消", on_click=lambda e: close_all())
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Text(titles[step], weight="bold"),
                ft.Container(content=step_label, padding=5),
            ]),
            content=ft.Container(content=_build_step_content(step), width=dialog_width),
            actions=actions,
        )
        return dlg

    def _go_prev():
        if state["step"] > 1:
            state["step"] -= 1
            rebuild_ui()

    def _go_next():
        if state["step"] < 4:
            state["step"] += 1
            rebuild_ui()

    def _do_execute():
        close_all()
        config = {
            "mode": state["mode"],
            "lang_codes": [code for code, v in state["lang_codes"].items() if v],
            "only_lang": state["only_lang"],
            "process_zh_cn": state["process_zh_cn"],
            "patchouli_skip": state["patchouli_skip"],
            "patchouli_threshold": state["patchouli_threshold"],
            "zh_en_threshold": state["zh_en_threshold"],
            "dry_run": state["dry_run"],
            "write_new_cache": state["write_new_cache"],
            "description": state["description"],
            "version": state["version"],
            "pack_image": state["pack_image"],
            "extra_folders": list(state["extra_folders"]),
            "zip_output": state["zip_output"],
            "merge_input": input_path,
        }
        on_execute(config)

    d1 = build_dialog(1)
    dialogs.append(d1)
    page.overlay.append(d1)
    d1.open = True
    page.update()