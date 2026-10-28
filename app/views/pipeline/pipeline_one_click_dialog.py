"""一鍵製作 Wizard 對話框

用途：
- 依序顯示 4 個步驟對話框（1/4 → 2/4 → 3/4 → 4/4）
- 每個步驟顯示唯讀的 input_path / output_path
- Step 1：執行模式、語言代碼勾選
- Step 2：語系比對設定（來源固定為步驟 1 的提取輸出）
- Step 3：翻譯設定（Dry Run、每次寫入新快取分片）
- Step 4：打包設定（ZIP 檔名、版本、封面圖片）
- 按「確定執行」後回呼叫 on_execute，所有設定資料透過回調傳回
- 邏輯不串接：只收集設定，回調外層實作
"""

import functools
import os
import types

import flet as ft

from app.services_impl.moddb_service import load_db_settings
from app.services_impl.pack_format_service import (
    build_pack_mcmeta,
    resolve_max_input,
    resolve_version_input,
)
from app.services_impl.pipelines.bundle_service import normalize_output_zip_name
from app.ui.design import C
from app.ui.dialogs import dispose_dialogs, present_dialog
from app.views.merge.merge_db_options import MergeDbOptions
from app.views.moddb.lm_db_options import LmDbOptions
from app.views.pipeline.extra_bundle_sources import (
    append_extra_source,
    build_extra_source_controls,
    clear_extra_source_controls,
    read_extra_source_controls,
    refresh_extra_source_preview,
    set_extra_source_feedback,
)
from app.views.pipeline.pipeline_config import (
    RESOURCE_PACK_INSTALL_HINT,
    PipelineConfig,
    normalize_extract_mode,
)
from app.views.pipeline.pipeline_forms import (
    BundleFormState,
    ExtractFormState,
    MergeFormState,
    PipelineWizardState,
    TranslateFormState,
    build_bundle_form,
    build_extract_form,
    build_merge_form,
    build_translate_form,
    dialog_button,
    dialog_content,
    dialog_dimensions,
)
from app.views.pipeline.pipeline_one_click_bundle_widgets import (
    build_step4_version_widgets,
)
from translation_tool.core.bundle_sources import (
    RESOURCE_PACK_MODE,
    ExtraBundleSource,
    serialize_extra_bundle_sources,
)
from translation_tool.utils.config_manager import (
    get_lm_translate_folder_name,
    load_config,
)


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
                "version_input": str,   # 使用者輸入的版本或 pack_format；空白代表預設
                "pack_format": int,     # 由 version_input 換算；空白為預設（1.21.1 = 34）
                "max_pack_format": int | None,  # 使用者填的最大使用版本；None 用預設
                "pack_image": str | None,
                "extra_folders": list[str | dict],
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
    ctx.config_snapshot = load_config()
    ctx.pipeline_config = PipelineConfig(
        input_path, output_path, config=ctx.config_snapshot
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
    _one_click_bind_context(ctx)

    initial_dialog = ctx.build_dialog(1)
    ctx.active_step_dialog = initial_dialog
    present_dialog(ctx, initial_dialog)


def _one_click_bind_context(ctx):
    """Bind the database controls and stateful callbacks for one wizard instance."""
    ctx.global_db_settings = load_db_settings()
    ctx.merge_db_options = MergeDbOptions(
        ctx.page.update, settings=ctx.global_db_settings
    )
    ctx.lm_db_options = LmDbOptions(
        ctx.page.update,
        settings=ctx.global_db_settings,
        step2_version_provider=lambda settings: (
            ctx.merge_db_options.resolve_target_for_inheritance(settings)
        ),
    )
    ctx.closed = False
    ctx.executed = False
    ctx.active_step_dialog = None
    ctx.feedback = ft.Text("", size=12, color=C.GOLD, visible=False)
    callbacks = {
        "rebuild_ui": _one_click_rebuild_ui,
        "close_all": _one_click_close_all,
        "_build_step_content": _one_click__build_step_content,
        "_build_step1": _one_click__build_step1,
        "_build_step2": _one_click__build_step2,
        "_build_step3": _one_click__build_step3,
        "_build_step4": _one_click__build_step4,
        "build_dialog": _one_click_build_dialog,
        "_go_prev": _one_click__go_prev,
        "_go_next": _one_click__go_next,
        "_do_execute": _one_click__do_execute,
    }
    for name, callback in callbacks.items():
        setattr(ctx, name, functools.partial(callback, ctx))
    ctx.dialog_width, ctx.dialog_height = dialog_dimensions(ctx.page)
    ctx.step_label = ft.Text(
        f"{ctx.state.step}/4", size=12, color=C.MUTED, weight=ft.FontWeight.W_500
    )


def _one_click_init_config_defaults(ctx):
    """一鍵製作對話框：讀取設定預設值。"""

    cfg = ctx.config_snapshot
    lang_merger_cfg = cfg.get("lang_merger", {})
    bundler_cfg = cfg.get("output_bundler", {})
    jar_extractor_cfg = cfg.get("jar_extractor", {})

    ctx.lang_codes_default = jar_extractor_cfg.get(
        "lang_codes", ["en_us", "zh_cn", "zh_tw"]
    )
    ctx.organized_folder = lang_merger_cfg.get(
        "pending_organized_folder_name", "待翻譯整理需翻譯"
    )
    translate_output_subfolder = get_lm_translate_folder_name(cfg)
    output_zip_name = normalize_output_zip_name(bundler_cfg.get("output_zip_name"))
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
    ctx.state = PipelineWizardState(
        extract=ExtractFormState(
            mode="lang",
            lang_codes={code: True for code in ctx.lang_codes_default},
        ),
        merge=MergeFormState(
            patchouli_skip=lang_merger_cfg.get(
                "patchouli_skip_en_us_when_zh_cn_exists", False
            ),
            patchouli_threshold=ctx.patchouli_threshold_default,
            zh_en_threshold=ctx.zh_en_threshold_default,
        ),
        translate=TranslateFormState(),
        bundle=BundleFormState(
            zip_output=os.path.join(ctx.output_path, output_zip_name)
            if ctx.output_path
            else ""
        ),
    )

    ctx.dialogs: list[ft.AlertDialog] = []
    ctx.step_label = ft.Text(
        f"{ctx.state.step}/4", size=12, color=C.MUTED, weight=ft.FontWeight.W_500
    )


def _one_click_rebuild_ui(ctx):
    if ctx.closed:
        return
    # Clear ownership before requesting the old native dialog's asynchronous dismiss event.
    ctx.active_step_dialog = None
    dispose_dialogs(ctx)

    step = ctx.state.step

    ctx.step_label.value = f"{step}/4"

    dlg = ctx.build_dialog(step)
    ctx.active_step_dialog = dlg
    present_dialog(ctx, dlg)


def _one_click_close_all(ctx):
    if ctx.closed:
        return
    ctx.closed = True
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
    paths = ft.Column(
        [
            ft.Text("Mod 來源（唯讀）", weight="bold", size=13),
            ft.Text(
                ctx.input_path or "未設定", size=11, color=C.MUTED, selectable=True
            ),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(
                ctx.output_path or "未設定", size=11, color=C.MUTED, selectable=True
            ),
        ],
        spacing=8,
    )
    controls = build_extract_form(
        path_section=paths,
        state=ctx.state.extract,
        language_codes=ctx.lang_codes_default,
    )
    ctx.radio_group = controls.mode
    ctx.lang_code_checks_local = controls.lang_checks
    return controls.content


def _one_click__build_step2(ctx):
    paths = ft.Column(
        [
            ft.Text("步驟 1 的提取輸出（自動帶入）", weight="bold", size=13),
            ft.Text("實際合併來源目錄（唯讀）", weight="bold", size=12),
            ft.Text(
                ctx.pipeline_config.merge_input_dir,
                size=11,
                color=C.MUTED,
                selectable=True,
            ),
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(
                ctx.pipeline_config.merge_output_dir,
                size=11,
                color=C.MUTED,
                selectable=True,
            ),
        ],
        spacing=8,
    )
    controls = build_merge_form(
        path_section=paths,
        state=ctx.state.merge,
        db_card=ctx.merge_db_options.card,
        readonly_db_explanation=True,
        on_zh_cn_change=lambda _event: ctx.page.update(),
    )
    ctx.merge_only_lang_checkbox = controls.only_lang
    ctx.merge_process_zh_cn_switch = controls.process_zh_cn
    ctx.merge_patchouli_skip_switch = controls.patchouli_skip
    ctx.merge_patchouli_threshold_field = controls.patchouli_threshold
    ctx.merge_zh_en_threshold_field = controls.zh_en_threshold
    return controls.content


def _one_click__build_step3(ctx):
    paths = ft.Column(
        [
            ft.Text("翻譯目標（唯讀）", weight="bold", size=13),
            *[
                ft.Text(path, size=11, color=C.MUTED, selectable=True)
                for path in ctx.pipeline_config.translate_input_dirs
            ],
            ft.Text("輸出目錄（唯讀）", weight="bold", size=13),
            ft.Text(
                ctx.pipeline_config.translate_output_dir,
                size=11,
                color=C.MUTED,
                selectable=True,
            ),
        ],
        spacing=8,
    )
    controls = build_translate_form(
        path_section=paths,
        state=ctx.state.translate,
        db_card=ctx.lm_db_options.card,
    )
    ctx.dry_run_switch = controls.dry_run
    ctx.write_new_cache_switch = controls.write_new_cache
    return controls.content


def _one_click__build_step4(ctx):
    widgets = build_step4_version_widgets(ctx)
    ctx.bundle_widgets = widgets

    def _remove_extra(p: object):
        if p in ctx.state.bundle.extra_folders:
            ctx.state.bundle.extra_folders.remove(p)
            _refresh_extra()
            ctx.page.update()

    def _add_extra(e=None):
        async def do():
            result = await ctx.file_picker.get_directory_path()
            if result:
                controls = ctx.extra_source_controls
                mode = controls.mode_field.value or RESOURCE_PACK_MODE
                if (
                    mode == RESOURCE_PACK_MODE
                    and not (controls.target_field.value or "").strip()
                ):
                    value: object = result
                else:
                    value = ExtraBundleSource(
                        result,
                        mode=mode,
                        recursive=bool(controls.recursive_field.value),
                        target=(controls.target_field.value or "").strip(),
                    )
                added, message = append_extra_source(
                    ctx.state.bundle.extra_folders,
                    value,
                    allow_unvalidated=True,
                )
                if not added:
                    set_extra_source_feedback(controls, message, error=True)
                    ctx.page.update()
                    return
                clear_extra_source_controls(controls)
                _refresh_extra()
                ctx.page.update()

        ctx.page.run_task(do)

    def _add_extra_manually(e=None):
        del e
        controls = ctx.extra_source_controls
        try:
            value = read_extra_source_controls(controls)
            added, message = append_extra_source(ctx.state.bundle.extra_folders, value)
        except ValueError as exc:
            added, message = False, str(exc)
        if not added:
            set_extra_source_feedback(controls, message, error=True)
            ctx.page.update()
            return
        clear_extra_source_controls(controls)
        _refresh_extra()
        ctx.page.update()

    ctx.extra_source_controls = build_extra_source_controls(
        on_pick=_add_extra,
        on_manual_add=_add_extra_manually,
        picker_label="+ 新增資料夾",
        list_height=100,
    )
    widgets.extra_folders_view = ctx.extra_source_controls.source_list

    def _refresh_extra():
        refresh_extra_source_preview(
            ctx.extra_source_controls,
            ctx.state.bundle.extra_folders,
            _remove_extra,
        )

    _refresh_extra()

    path_section = ft.Column(
        [
            ft.Text("輸入來源（唯讀）", weight="bold", size=13),
            ft.Text(
                "打包 staging 由下列來源合併建立，路徑由流程自動帶入且不可在此修改。",
                size=11,
                color=C.MUTED,
            ),
            ft.Row([widgets.input_field], wrap=True),
            *[
                ft.Text(f"來源：{path}", size=10, color=C.MUTED, selectable=True)
                for path in ctx.pipeline_config.bundle_sources
            ],
            ft.Text("輸出 ZIP 檔案", weight="bold", size=13),
            ft.Row([widgets.zip_output_field], wrap=True),
            ft.Text(RESOURCE_PACK_INSTALL_HINT, size=10, color=C.MUTED),
        ],
        spacing=6,
    )
    extras = ft.Column(
        [
            ctx.extra_source_controls.content,
        ],
        spacing=6,
    )
    return build_bundle_form(
        path_section=path_section,
        description_field=widgets.description_field,
        version_input=widgets.version_input,
        pack_image_row=_one_click_pack_image_row(ctx, widgets.pack_image_field),
        extra_folders_section=extras,
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
            dialog_button(
                "移除",
                lambda e: _one_click_clear_pack_image(ctx, pack_image_field),
                icon=ft.Icons.DELETE_OUTLINE,
            ),
        ],
        wrap=True,
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
        ctx.state.bundle.pack_image = path
        field.value = path
        ctx.page.update()

    ctx.page.run_task(do)


def _one_click_clear_pack_image(ctx, field) -> None:
    ctx.state.bundle.pack_image = None
    field.value = ""
    ctx.page.update()


def _one_click_build_dialog(ctx, step: int):
    titles = {
        1: "📦 抽取資源設定",
        2: "🔍 語系比對設定",
        3: "🔄 啟動翻譯設定",
        4: "📦 打包資源設定",
    }

    actions = []
    if step > 1:
        actions.append(dialog_button("上一個", lambda e, s=step: ctx._go_prev(s)))
    if step < 4:
        actions.append(dialog_button("下一個", lambda e, s=step: ctx._go_next(s)))
    else:
        actions.append(
            dialog_button(
                "確定執行",
                on_click=lambda e, s=step: ctx._do_execute(s),
                icon=ft.Icons.CHECK,
                role="primary",
            )
        )
    actions.append(dialog_button("取消", lambda e: ctx.close_all()))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Text(titles[step], weight="bold"),
                ft.Container(content=ctx.step_label, padding=5),
            ]
        ),
        content=dialog_content(
            ctx.page,
            ft.Column(
                [ctx._build_step_content(step), ctx.feedback], spacing=8, tight=False
            ),
        ),
        actions=actions,
    )
    dlg.on_dismiss = lambda _event: _one_click__on_dialog_dismiss(ctx, dlg)
    return dlg


def _one_click__on_dialog_dismiss(ctx, dialog):
    """Retire the wizard only when its currently owned step is dismissed externally."""
    if ctx.active_step_dialog is not dialog or ctx.closed:
        return
    ctx.closed = True
    ctx.active_step_dialog = None
    if dialog in ctx.dialogs:
        ctx.dialogs.remove(dialog)


def _one_click__go_prev(ctx, expected_step=None):
    if (
        ctx.closed
        or not getattr(ctx.active_step_dialog, "open", False)
        or (expected_step is not None and ctx.state.step != expected_step)
    ):
        return
    if ctx.state.step > 1:
        ctx.state.step -= 1
        ctx.rebuild_ui()


def _one_click__go_next(ctx, expected_step=None):
    if (
        ctx.closed
        or not getattr(ctx.active_step_dialog, "open", False)
        or (expected_step is not None and ctx.state.step != expected_step)
    ):
        return
    if ctx.state.step < 4:
        ctx.state.step += 1
        ctx.rebuild_ui()


def _one_click__sync_state_from_controls(ctx) -> None:
    if hasattr(ctx, "radio_group"):
        ctx.state.extract.mode = ctx.radio_group.value
        ctx.state.extract.lang_codes.update(
            {
                code: bool(control.value)
                for code, control in ctx.lang_code_checks_local.items()
            }
        )
    if hasattr(ctx, "merge_only_lang_checkbox"):
        ctx.state.merge.only_lang = bool(ctx.merge_only_lang_checkbox.value)
        ctx.state.merge.process_zh_cn = bool(ctx.merge_process_zh_cn_switch.value)
        ctx.state.merge.patchouli_skip = bool(ctx.merge_patchouli_skip_switch.value)
        try:
            ctx.state.merge.patchouli_threshold = float(
                (ctx.merge_patchouli_threshold_field.value or "").strip()
            )
        except (TypeError, ValueError):
            pass
        try:
            ctx.state.merge.zh_en_threshold = int(
                (ctx.merge_zh_en_threshold_field.value or "").strip()
            )
        except (TypeError, ValueError):
            pass
    if hasattr(ctx, "dry_run_switch"):
        ctx.state.translate.dry_run = bool(ctx.dry_run_switch.value)
        ctx.state.translate.write_new_cache = bool(ctx.write_new_cache_switch.value)
    if hasattr(ctx, "bundle_widgets"):
        ctx.state.bundle.description = (
            ctx.bundle_widgets.description_field.value or ""
        ).strip()
        ctx.state.bundle.zip_output = ctx.bundle_widgets.zip_output_field.value or ""
        ctx.state.bundle.version_input = (
            ctx.bundle_widgets.version_input.field.value or ""
        ).strip()
        ctx.state.bundle.max_input = (
            ctx.bundle_widgets.version_input.max_field.value or ""
        ).strip()
        ctx.state.bundle.pack_image = (
            ctx.bundle_widgets.pack_image_field.value or ""
        ) or None


def _one_click__build_execute_config(ctx, lang_codes: list[str]) -> dict:
    global_settings = load_db_settings()
    merge_db_snapshot = ctx.merge_db_options.snapshot_for_run(global_settings)
    lm_db_snapshot = ctx.lm_db_options.snapshot_for_run(global_settings)
    return {
        "mode": normalize_extract_mode(ctx.state.extract.mode),
        "lang_codes": lang_codes,
        "only_lang": ctx.state.merge.only_lang,
        "process_zh_cn": ctx.state.merge.process_zh_cn,
        "patchouli_skip": ctx.state.merge.patchouli_skip,
        "patchouli_threshold": ctx.state.merge.patchouli_threshold,
        "zh_en_threshold": ctx.state.merge.zh_en_threshold,
        "dry_run": ctx.state.translate.dry_run,
        "write_new_cache": ctx.state.translate.write_new_cache,
        "description": ctx.state.bundle.description,
        "version_input": ctx.state.bundle.version_input,
        "merge_db_snapshot": merge_db_snapshot,
        "lm_db_snapshot": lm_db_snapshot,
        "pack_format": resolve_version_input(
            ctx.state.bundle.version_input
        ).pack_format,
        "max_pack_format": resolve_max_input(ctx.state.bundle.max_input),
        "pack_image": ctx.state.bundle.pack_image,
        "extra_folders": serialize_extra_bundle_sources(ctx.state.bundle.extra_folders),
        "zip_output": ctx.state.bundle.zip_output,
        "merge_input": ctx.input_path,
    }


def _one_click__do_execute(ctx, expected_step=4):
    if (
        ctx.closed
        or ctx.executed
        or ctx.state.step != expected_step
        or not getattr(ctx.active_step_dialog, "open", False)
    ):
        return
    _one_click__sync_state_from_controls(ctx)
    lang_codes = [
        code for code, enabled in ctx.state.extract.lang_codes.items() if enabled
    ]
    if not lang_codes:
        ctx.feedback.value = "請至少勾選一個語言代碼，修正後再執行。"
        ctx.feedback.visible = True
        ctx.page.update()
        return
    try:
        build_pack_mcmeta(
            "",
            resolve_version_input(ctx.state.bundle.version_input).pack_format,
            resolve_max_input(ctx.state.bundle.max_input),
        )
    except ValueError as exc:
        ctx.feedback.value = f"版本或 pack_format 無效：{exc}"
        ctx.feedback.visible = True
        ctx.page.update()
        return
    config = _one_click__build_execute_config(ctx, lang_codes)
    merge_snapshot = config.get("merge_db_snapshot")
    if merge_snapshot is not None and merge_snapshot.blocking_warning:
        ctx.feedback.value = (
            "⚠️ "
            + merge_snapshot.blocking_warning
            + "；請回到步驟 2 選擇有效版本，或關閉 Mod 資料庫補譯。"
        )
        ctx.feedback.visible = True
        ctx.page.update()
        return
    result = ctx.on_execute(config)
    if result is False:
        ctx.feedback.value = "設定驗證未通過；請修正必要欄位後再執行。"
        ctx.feedback.visible = True
        ctx.page.update()
        return
    ctx.executed = True
    ctx.close_all()
