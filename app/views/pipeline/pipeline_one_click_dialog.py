"""一鍵製作 Wizard 對話框

用途：
- 依序顯示 4 個步驟對話框（1/4 → 2/4 → 3/4 → 4/4）
- 每個步驟顯示唯讀的 input_path / output_path
- Step 1：執行模式、語言代碼勾選
- Step 2：語系比對設定（來源固定為步驟 1 的提取輸出）
- Step 3：翻譯設定（Dry Run、寫入新快取）
- Step 4：打包設定（ZIP 檔名、版本、封面圖片）
- 按「確定執行」後回呼叫 on_execute，所有設定資料透過回調傳回
- 邏輯不串接：只收集設定，回調外層實作
"""

import functools
import json
import os
import types

import flet as ft

from app.ui.design import C
from app.ui.dialogs import dispose_dialogs, present_dialog
from app.ui.sync_text_field import SyncTextField
from app.views.pipeline.pipeline_config import normalize_extract_mode
from translation_tool.utils.config_manager import load_config
from translation_tool.utils.log_unit import log_warning


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
        except Exception as exc:  # noqa: BLE001 - 讀不到版本資料時使用空設定，UI 仍可開啟
            log_warning(f"讀取版本資料失敗，使用空設定：{config_path}: {exc!r}")
            return {}
    return {}


def _parse_number(text, cast, default):
    """欄位文字轉數字；空白或格式錯誤時回傳預設值（與欄位提示「空白用預設值」一致）。"""
    try:
        return cast(str(text).strip())
    except (TypeError, ValueError):
        return default


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
                "min_format": int | None,   # 由所選版本對應；未選為 None
                "max_format": int | None,
                "pack_image": str | None,
                "extra_folders": list[str],
                "zip_output": str,
            }
        show_snack_bar: 回調：(message: str, color: str) -> void
    """
    ctx = types.SimpleNamespace(
        page=page,
        file_picker=file_picker,
        input_path=input_path,
        output_path=output_path,
        on_execute=on_execute,
        show_snack_bar=show_snack_bar,
    )
    # Flet 0.85+ 的 Web 對話框必須交給 page.show_dialog / pop_dialog 管理；
    # 測試用頁面或舊版 Flet 沒有這組 API 時，才退回 overlay 相容流程。
    ctx.uses_dialog_api = callable(getattr(page, "show_dialog", None)) and callable(
        getattr(page, "pop_dialog", None)
    )
    lang_merger_cfg, output_zip_name, translate_output_subfolder = (
        _one_click_init_config_defaults(ctx)
    )
    _one_click_init_state(
        ctx, lang_merger_cfg, output_zip_name, translate_output_subfolder
    )

    ctx.rebuild_ui = functools.partial(_one_click_rebuild_ui, ctx)

    ctx.close_all = functools.partial(_one_click_close_all, ctx)

    ctx._build_step_content = functools.partial(_one_click__build_step_content, ctx)

    ctx._build_step1 = functools.partial(_one_click__build_step1, ctx)

    ctx._build_step2 = functools.partial(_one_click__build_step2, ctx)

    ctx._build_step3 = functools.partial(_one_click__build_step3, ctx)

    ctx._build_step4 = functools.partial(_one_click__build_step4, ctx)

    ctx.dialog_width = int(ctx.page.width * 0.6)

    ctx.step_label = ft.Text(
        f"{ctx.state['step']}/4", size=12, color=C.MUTED, weight=ft.FontWeight.W_500
    )

    ctx.build_dialog = functools.partial(_one_click_build_dialog, ctx)

    ctx._go_prev = functools.partial(_one_click__go_prev, ctx)

    ctx._go_next = functools.partial(_one_click__go_next, ctx)

    ctx._do_execute = functools.partial(_one_click__do_execute, ctx)

    present_dialog(ctx, ctx.build_dialog(1))


def _one_click_init_config_defaults(ctx):
    """一鍵製作對話框：讀取設定預設值。"""

    cfg = load_config()
    lang_merger_cfg = cfg.get("lang_merger", {})
    bundler_cfg = cfg.get("output_bundler", {})
    jar_extractor_cfg = cfg.get("jar_extractor", {})

    ctx.lang_codes_default = jar_extractor_cfg.get(
        "lang_codes", ["en_us", "zh_cn", "zh_tw"]
    )
    ctx.organized_folder = lang_merger_cfg.get(
        "pending_organized_folder_name", "待翻譯整理需翻譯"
    )
    translate_output_subfolder = lang_merger_cfg.get(
        "lm_translate_folder_name", "_翻譯輸出"
    )
    output_zip_name = bundler_cfg.get("output_zip_name", "可使用翻譯.zip")
    ctx.translate_output_subfolder = translate_output_subfolder
    return lang_merger_cfg, output_zip_name, translate_output_subfolder


def _one_click_init_state(
    ctx, lang_merger_cfg, output_zip_name, translate_output_subfolder
) -> None:
    """一鍵製作對話框：狀態與步驟標籤。"""

    ctx.patchouli_threshold_default = lang_merger_cfg.get(
        "patchouli_effective_translation_threshold", 0.5
    )
    ctx.zh_en_threshold_default = lang_merger_cfg.get("zh_en_letter_threshold", 2)
    ctx.state = {
        "step": 1,
        "mode": "lang",
        "lang_codes": {code: True for code in ctx.lang_codes_default},
        "only_lang": True,
        "process_zh_cn": True,
        "patchouli_skip": lang_merger_cfg.get(
            "patchouli_skip_en_us_when_zh_cn_exists", False
        ),
        "patchouli_threshold": ctx.patchouli_threshold_default,
        "zh_en_threshold": ctx.zh_en_threshold_default,
        "dry_run": False,
        "write_new_cache": True,
        "description": "",
        "version": "",
        "pack_image": None,
        "extra_folders": [],
        "zip_output": os.path.join(ctx.output_path, output_zip_name)
        if ctx.output_path
        else "",
        "translate_input": "",
        "translate_output": os.path.join(ctx.output_path, "lm_translate")
        if ctx.output_path
        else "",
        "bundle_input": os.path.join(
            ctx.output_path, "lm_translate", translate_output_subfolder
        )
        if ctx.output_path
        else "",
    }

    ctx.dialogs: list[ft.AlertDialog] = []
    ctx.step_label = ft.Text(
        f"{ctx.state['step']}/4", size=12, color=C.MUTED, weight=ft.FontWeight.W_500
    )


def _one_click_rebuild_ui(ctx):
    dispose_dialogs(ctx)

    step = ctx.state["step"]

    ctx.step_label.value = f"{step}/4"

    dlg = ctx.build_dialog(step)
    present_dialog(ctx, dlg)


def _one_click_close_all(ctx):
    dispose_dialogs(ctx)
    ctx.page.update()


def _one_click__build_step_content(ctx, step: int):
    if step == 1:
        return ctx._build_step1()
    elif step == 2:
        return ctx._build_step2()
    elif step == 3:
        return ctx._build_step3()
    elif step == 4:
        return ctx._build_step4()


def _one_click__build_step1(ctx):
    radio_group = ft.RadioGroup(
        content=ft.Column(
            [
                ft.Radio(label="提取 Lang", value="lang"),
                ft.Radio(label="提取 Book", value="book"),
                ft.Radio(label="全部執行（Lang + Book）", value="both"),
            ],
            spacing=4,
        ),
        value=ctx.state["mode"],
    )

    def on_mode_changed(e):
        ctx.state["mode"] = e.control.value

    radio_group.on_change = on_mode_changed

    lang_checks = {}
    for code in ctx.lang_codes_default:
        cb = ft.Checkbox(label=code, value=ctx.state["lang_codes"].get(code, True))
        lang_checks[code] = cb

    def on_lang_check(e=None):
        for code, cb in lang_checks.items():
            ctx.state["lang_codes"][code] = cb.value

    for cb in lang_checks.values():
        cb.on_change = on_lang_check

    lang_section = ft.Column(
        [lang_checks[code] for code in ctx.lang_codes_default], spacing=2
    )

    return ft.Column(
        [
            ft.Text("Mod 來源（唯讀）", weight="bold", size=13),
            ft.Text(ctx.input_path or "未設定", size=11, color=C.MUTED),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(ctx.output_path or "未設定", size=11, color=C.MUTED),
            ft.Divider(),
            ft.Text("執行模式", weight="bold", size=13),
            radio_group,
            ft.Text("處理的語言代碼", weight="bold", size=13),
            ft.Container(content=lang_section),
        ],
        spacing=10,
        tight=False,
    )


def _one_click__build_step2(ctx):
    patchouli_skip_cb, patchouli_thresh_field, zh_en_field = _one_click_step2_widgets(
        ctx
    )

    def on_only_lang(e):
        ctx.state["only_lang"] = e.control.value

    def on_process_zh_cn(e):
        ctx.state["process_zh_cn"] = e.control.value

    return ft.Column(
        [
            ft.Text("合併來源（唯讀）", weight="bold", size=13),
            # 一鍵流程的語系比對固定處理「步驟 1 的提取輸出」，不是另外指定的 Mod 來源
            ft.Text("步驟 1 的提取輸出（自動帶入）", size=11, color=C.MUTED),
            ft.Divider(),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(ctx.output_path or "未設定", size=11, color=C.MUTED),
            ft.Divider(),
            ft.Text("語系過濾設定", weight="bold", size=13),
            ft.Switch(
                label="只處理 lang 檔案",
                value=ctx.state["only_lang"],
                on_change=on_only_lang,
            ),
            ft.Switch(
                label="處理 zh_cn 檔案",
                value=ctx.state["process_zh_cn"],
                on_change=on_process_zh_cn,
            ),
            ft.Divider(),
            ft.Text("zh 英文含量閾值", weight=ft.FontWeight.W_500, size=12),
            zh_en_field,
            ft.Divider(),
            ft.Text("Patchouli 進階設定", weight="bold", size=13),
            ft.Column(
                [
                    ft.Text(
                        "允許 zh_cn 觸發跳過 en_us",
                        weight=ft.FontWeight.W_500,
                        size=12,
                    ),
                    patchouli_skip_cb,
                    ft.Text("en_us 跳過門檻", weight=ft.FontWeight.W_500, size=12),
                    patchouli_thresh_field,
                ]
            ),
        ],
        spacing=10,
        tight=False,
    )


def _one_click_step2_widgets(ctx):
    """步驟 2：Patchouli 選項與閾值欄位。"""

    patchouli_skip_cb = ft.Switch(
        label="允許 zh_cn 觸發跳過 en_us",
        value=ctx.state["patchouli_skip"],
    )

    def on_patchouli_skip(e):
        ctx.state["patchouli_skip"] = e.control.value

    patchouli_skip_cb.on_change = on_patchouli_skip

    def on_patchouli_threshold(e):
        ctx.state["patchouli_threshold"] = _parse_number(
            e.control.value, float, ctx.patchouli_threshold_default
        )

    def on_zh_en_threshold(e):
        ctx.state["zh_en_threshold"] = _parse_number(
            e.control.value, int, ctx.zh_en_threshold_default
        )

    patchouli_thresh_field = SyncTextField(
        on_change=on_patchouli_threshold,
        value=str(ctx.state["patchouli_threshold"]),
        width=100,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值",
    )

    zh_en_field = SyncTextField(
        on_change=on_zh_en_threshold,
        value=str(ctx.state["zh_en_threshold"]),
        width=80,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值",
    )
    return patchouli_skip_cb, patchouli_thresh_field, zh_en_field


def _one_click__build_step3(ctx):
    dry_run_sw = ft.Switch(
        label="Dry Run（只分析不翻譯）",
        value=ctx.state["dry_run"],
    )

    def on_dry_run(e):
        ctx.state["dry_run"] = e.control.value

    dry_run_sw.on_change = on_dry_run

    write_cache_sw = ft.Switch(
        label="寫入新快取（每次回傳單獨快取）",
        value=ctx.state["write_new_cache"],
    )

    def on_write_cache(e):
        ctx.state["write_new_cache"] = e.control.value

    write_cache_sw.on_change = on_write_cache

    _translate_input_field = SyncTextField(
        label="翻譯目標",
        hint_text="自動帶入整理後的待翻譯資料夾",
        value=ctx.state["translate_input"],
        expand=True,
        border_color=C.DIA,
        path_input=True,
    )
    _translate_output_field = SyncTextField(
        label="輸出目錄",
        hint_text="自動帶入：{output}/lm_translate/<翻譯輸出子資料夾>",
        value=ctx.state["translate_output"],
        expand=True,
        border_color=C.DIA,
        path_input=True,
    )

    return ft.Column(
        [
            ft.Text("翻譯目標（唯讀）", weight="bold", size=13),
            ft.Text(
                os.path.join(
                    ctx.output_path,
                    "locale_sort",
                    "_整理輸出",
                    "lang_output",
                    ctx.organized_folder,
                )
                if ctx.output_path
                else "未設定",
                size=11,
                color=C.MUTED,
            ),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(
                os.path.join(
                    ctx.output_path, "lm_translate", ctx.translate_output_subfolder
                )
                if ctx.output_path
                else "未設定",
                size=11,
                color=C.MUTED,
            ),
            ft.Divider(),
            ft.Text("執行選項", weight="bold", size=13),
            dry_run_sw,
            write_cache_sw,
        ],
        spacing=10,
        tight=False,
    )


def _one_click__build_step4(ctx):
    (
        _toggle_version,
        bundle_input_field,
        desc_field,
        extra_view,
        pack_image_field,
        version_dropdown,
        version_toggle_label,
        zip_output_field,
    ) = _one_click_step4_version_widgets(ctx)

    def _refresh_extra():
        extra_view.controls.clear()
        for path in ctx.state["extra_folders"]:
            extra_view.controls.append(
                ft.Row(
                    [
                        ft.Text(os.path.basename(path), expand=True, size=12),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=14,
                            on_click=lambda e, p=path: _remove_extra(p),
                        ),
                    ]
                )
            )

    def _remove_extra(p: str):
        if p in ctx.state["extra_folders"]:
            ctx.state["extra_folders"].remove(p)
            _refresh_extra()
            ctx.page.update()

    _refresh_extra()

    def _add_extra(e=None):
        async def do():
            result = await ctx.file_picker.get_directory_path()
            if result and result not in ctx.state["extra_folders"]:
                ctx.state["extra_folders"].append(result)
                _refresh_extra()
                ctx.page.update()

        ctx.page.run_task(do)

    return ft.Column(
        [
            ft.Text("輸入來源（唯讀）", weight="bold", size=13),
            ft.Text(
                "一鍵流程固定使用步驟 2（語系合併）與步驟 3（翻譯）的輸出，不可在此修改",
                size=11,
                color=C.MUTED,
            ),
            ft.Row([bundle_input_field]),
            ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
            ft.Row([zip_output_field]),
            ft.Text("檔案敘述", weight="bold", size=13),
            desc_field,
            ft.Text("Minecraft 版本", weight="bold", size=13),
            ft.Container(
                content=ft.Row(
                    [
                        version_toggle_label,
                        ft.Icon(ft.Icons.EXPAND_MORE, size=18),
                    ]
                ),
                padding=8,
                border=ft.Border.all(1, C.DIM),
                border_radius=6,
                on_click=_toggle_version,
            ),
            version_dropdown,
            ft.Text("封面圖片（可留空）", weight="bold", size=13),
            _one_click_pack_image_row(ctx, pack_image_field),
            ft.Text("其他指定資料夾", weight="bold", size=13),
            ft.Container(
                content=extra_view,
                border=ft.Border.all(1, C.DIM),
                border_radius=6,
                padding=4,
            ),
            ft.Button("+ 新增資料夾", icon=ft.Icons.FOLDER_OPEN, on_click=_add_extra),
        ],
        spacing=10,
        tight=False,
    )


def _one_click_pack_image_row(ctx, pack_image_field) -> ft.Row:
    """封面圖片列：唯讀路徑欄位 + 選擇檔案 + 移除。"""
    return ft.Row(
        [
            pack_image_field,
            ft.Button(
                "選擇檔案...",
                icon=ft.Icons.IMAGE,
                on_click=lambda e: _one_click_pick_pack_image(ctx, pack_image_field),
            ),
            ft.TextButton(
                "移除",
                icon=ft.Icons.DELETE_OUTLINE,
                on_click=lambda e: _one_click_clear_pack_image(ctx, pack_image_field),
            ),
        ]
    )


_PACK_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def _one_click_pick_pack_image(ctx, field) -> None:
    """選擇封面圖片（Flet 1.0：``pick_files`` 直接回傳清單）；副檔名與打包對話框一致。"""

    async def do():
        result = await ctx.file_picker.pick_files(
            dialog_title="選擇封面圖片",
            allowed_extensions=["png", "jpg", "jpeg"],
        )
        if not result:
            return
        path = getattr(result[0], "path", None)
        if not path:
            return
        if os.path.splitext(path)[1].lower() not in _PACK_IMAGE_EXTENSIONS:
            ctx.show_snack_bar("⚠️ 封面圖片只支援 .png/.jpg")
            return
        ctx.state["pack_image"] = path
        field.value = path
        ctx.page.update()

    ctx.page.run_task(do)


def _one_click_clear_pack_image(ctx, field) -> None:
    ctx.state["pack_image"] = None
    field.value = ""
    ctx.page.update()


def _one_click_step4_version_widgets(ctx):
    """步驟 4：輸出、版本與額外資料夾控制項（版本切換使用 nonlocal，必須同處定義）。"""

    def on_description(e):
        ctx.state["description"] = e.control.value

    def on_zip_output(e):
        ctx.state["zip_output"] = e.control.value

    bundle_input_field = SyncTextField(
        read_only=True,  # 只顯示：實際打包來源由流程自動決定（見 PipelineConfig.bundle_sources）
        label="輸入來源",
        hint_text="自動帶入翻譯完成後的輸出",
        value=ctx.state["bundle_input"],
        expand=True,
        border_color=C.ENCH,
    )
    zip_output_field = SyncTextField(
        on_change=on_zip_output,
        label="輸出 ZIP 檔案",
        value=ctx.state["zip_output"],
        expand=True,
        border_color=C.ENCH,
        path_input=True,
    )

    desc_field = SyncTextField(
        on_change=on_description,
        label="檔案敘述",
        hint_text="直接輸入文字，或使用 § 顏色代碼",
        value=ctx.state["description"],
        expand=True,
        border_color=C.ENCH,
    )
    pack_image_field = SyncTextField(
        label="封面圖片（可留空）",
        value=ctx.state["pack_image"] or "",
        expand=True,
        border_color=C.ENCH,
        read_only=True,
    )

    version_data = _load_version_data()
    version_toggle_label = ft.Text(
        ctx.state["version"] or "點擊選擇版本", expand=True, size=12, color=C.MUTED
    )
    version_expanded = False
    version_list = ft.ListView(expand=True, height=140, spacing=4)

    def _refresh_versions(search=""):
        version_list.controls.clear()
        filtered = [v for v in version_data if search.lower() in v.lower()]
        if not filtered:
            version_list.controls.append(ft.Text("無可用版本", size=12, color=C.DIM))
        for v in filtered:
            version_list.controls.append(
                ft.Container(
                    content=ft.Text(v, size=13),
                    padding=8,
                    border=ft.Border.all(1, C.DIM),
                    border_radius=6,
                    on_click=lambda e, ver=v: _select_version(ver),
                )
            )

    def _select_version(v: str):
        ctx.state["version"] = v
        version_toggle_label.value = v
        version_toggle_label.color = None
        ctx.page.update()

    _refresh_versions()

    def _toggle_version(e=None):
        nonlocal version_expanded
        version_expanded = not version_expanded
        version_dropdown.visible = version_expanded
        ctx.page.update()

    version_dropdown = ft.Container(
        content=version_list,
        height=140,
        border=ft.Border.all(1, C.DIM),
        border_radius=6,
        padding=4,
        visible=False,
    )

    extra_view = ft.ListView(height=60, spacing=2)
    return (
        _toggle_version,
        bundle_input_field,
        desc_field,
        extra_view,
        pack_image_field,
        version_dropdown,
        version_toggle_label,
        zip_output_field,
    )


def _one_click_build_dialog(ctx, step: int):
    titles = {
        1: "📦 抽取資源設定",
        2: "🔍 語系比對設定",
        3: "🔄 啟動翻譯設定",
        4: "📦 打包資源設定",
    }

    actions = []
    if step > 1:
        # Web 版使用標準 Button，避免 AlertDialog 內的 TextButton 事件未送達。
        actions.append(ft.Button("上一個", on_click=lambda e: ctx._go_prev()))
    if step < 4:
        actions.append(ft.Button("下一個", on_click=lambda e: ctx._go_next()))
    else:
        actions.append(
            ft.Button(
                "確定執行",
                icon=ft.Icons.CHECK,
                bgcolor=C.EM,
                color=C.ON_EM,
                on_click=lambda e: ctx._do_execute(),
            )
        )
    actions.append(ft.Button("取消", on_click=lambda e: ctx.close_all()))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Text(titles[step], weight="bold"),
                ft.Container(content=ctx.step_label, padding=5),
            ]
        ),
        content=ft.Container(
            content=ctx._build_step_content(step), width=ctx.dialog_width
        ),
        actions=actions,
    )
    return dlg


def _one_click__go_prev(ctx):
    if ctx.state["step"] > 1:
        ctx.state["step"] -= 1
        ctx.rebuild_ui()


def _one_click__go_next(ctx):
    if ctx.state["step"] < 4:
        ctx.state["step"] += 1
        ctx.rebuild_ui()


def _one_click__do_execute(ctx):
    ctx.close_all()
    version_info = _load_version_data().get(ctx.state["version"], {})
    config = {
        "mode": normalize_extract_mode(ctx.state["mode"]),
        "lang_codes": [code for code, v in ctx.state["lang_codes"].items() if v],
        "only_lang": ctx.state["only_lang"],
        "process_zh_cn": ctx.state["process_zh_cn"],
        "patchouli_skip": ctx.state["patchouli_skip"],
        "patchouli_threshold": ctx.state["patchouli_threshold"],
        "zh_en_threshold": ctx.state["zh_en_threshold"],
        "dry_run": ctx.state["dry_run"],
        "write_new_cache": ctx.state["write_new_cache"],
        "description": ctx.state["description"],
        "version": ctx.state["version"],
        "min_format": version_info.get("min_format"),
        "max_format": version_info.get("max_format"),
        "pack_image": ctx.state["pack_image"],
        "extra_folders": list(ctx.state["extra_folders"]),
        "zip_output": ctx.state["zip_output"],
        "merge_input": ctx.input_path,
    }
    ctx.on_execute(config)
