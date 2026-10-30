"""Shared forms and typed state for standalone and one-click pipeline dialogs.

Each dialog entry point owns its controls and runtime callbacks, while the
option rows, version selector, spacing and validation state are built here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import flet as ft

from app.services_impl.pack_format_service import (
    QUICK_VERSIONS,
    explain_version_input,
    resolve_max_input,
    resolve_version_input,
)
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    RESPONSIVE_FIELD_DATA_KEY,
    dialog_dimensions,
)
from app.ui.kit.basics import tone_icon
from app.ui.sync_text_field import SyncTextField


@dataclass
class ExtractFormState:
    mode: str = "lang"
    lang_codes: dict[str, bool] = field(default_factory=dict)


@dataclass
class MergeFormState:
    only_lang: bool = True
    process_zh_cn: bool = True
    patchouli_skip: bool = False
    patchouli_threshold: float = 0.5
    zh_en_threshold: int = 2


@dataclass
class TranslateFormState:
    dry_run: bool = False
    write_new_cache: bool = False


@dataclass
class BundleFormState:
    description: str = ""
    version_input: str = ""
    max_input: str = ""
    pack_image: str | None = None
    # 舊設定仍可放路徑字串；新版 UI 可放 ExtraBundleSource。
    extra_folders: list[object] = field(default_factory=list)
    zip_output: str = ""


@dataclass
class PipelineWizardState:
    """Typed mutable UI state; serialized only at the legacy action boundary."""

    step: int = 1
    extract: ExtractFormState = field(default_factory=ExtractFormState)
    merge: MergeFormState = field(default_factory=MergeFormState)
    translate: TranslateFormState = field(default_factory=TranslateFormState)
    bundle: BundleFormState = field(default_factory=BundleFormState)


@dataclass
class ExtractFormControls:
    content: ft.Column
    mode: ft.RadioGroup
    lang_checks: dict[str, ft.Checkbox]


@dataclass
class MergeFormControls:
    content: ft.Column
    only_lang: ft.Switch
    process_zh_cn: ft.Switch
    patchouli_skip: ft.Switch
    patchouli_threshold: SyncTextField
    zh_en_threshold: SyncTextField


@dataclass
class _MergeOptionControls:
    only_lang: ft.Switch
    process_zh_cn: ft.Switch
    patchouli_skip: ft.Switch
    patchouli_threshold: SyncTextField
    zh_en_threshold: SyncTextField


@dataclass
class TranslateFormControls:
    content: ft.Column
    dry_run: ft.Switch
    write_new_cache: ft.Switch


@dataclass
class VersionInputControls:
    content: ft.Column
    field: SyncTextField
    max_field: SyncTextField
    summary: ft.Text
    pack_format: Callable[[], int]
    max_pack_format: Callable[[], int | None]


def dialog_field_width(page: ft.Page, *, reserved_width: int = 340) -> int:
    """Return a responsive field width after reserving room for sibling controls."""
    width, _height = dialog_dimensions(page)
    return max(216, width - reserved_width)


def dialog_text_field(
    page: ft.Page,
    *,
    reserved_width: int = 340,
    **kwargs,
) -> SyncTextField:
    """Build a path/description field whose width follows an open dialog resize."""
    control = SyncTextField(
        width=dialog_field_width(page, reserved_width=reserved_width), **kwargs
    )
    control.data = {RESPONSIVE_FIELD_DATA_KEY: reserved_width}
    return control


def dialog_content(page: ft.Page, content: ft.Control) -> ft.Container:
    """Place a dialog body in the shared bounded viewport with visible footer."""
    width, height = dialog_dimensions(page)
    if isinstance(content, ft.Column):
        # Flet's scrollable Column inside a fixed-height Container was painting
        # an opaque empty viewport in CanvasKit for standalone dialogs. Use one
        # bounded ListView as the body scroll owner instead; keep all form
        # controls as its direct children so they share the same layout contract.
        content = ft.ListView(
            controls=content.controls,
            spacing=content.spacing,
            padding=0,
            expand=True,
            auto_scroll=False,
        )
    elif isinstance(content, ft.ListView):
        content.expand = True
        content.auto_scroll = False
    return ft.Container(
        content=content,
        width=width,
        height=height,
        data=RESPONSIVE_DIALOG_MARKER,
    )


def build_pipeline_dialog(
    page: ft.Page,
    *,
    title: str,
    content: ft.Control,
    actions: Sequence[ft.Control],
    step_label: ft.Control | None = None,
    icon: str | None = None,
    tone: str = "em",
    modal: bool = True,
) -> ft.AlertDialog:
    """建立所有流水線設定對話框共用的標題、內容與操作列。

    ``step_label`` 僅供一鍵精靈顯示 ``1/4``；其餘對話框使用相同標題骨架。
    表單工廠仍由呼叫者提供，因此這裡只統一殼層，不混入任何流程邏輯。
    """
    title_control = build_pipeline_dialog_title(
        title, step_label=step_label, icon=icon, tone=tone
    )
    return ft.AlertDialog(
        modal=modal,
        title=title_control,
        content=dialog_content(page, content),
        actions=list(actions),
        actions_alignment=ft.MainAxisAlignment.END,
    )


def build_pipeline_dialog_title(
    title: str,
    *,
    step_label: ft.Control | None = None,
    icon: str | None = None,
    tone: str = "em",
) -> ft.Row:
    """Build the shared dialog title row for initial and result states.

    Result states should replace the title through this helper instead of
    constructing a second visual language inline.  Keeping the text first
    also preserves the stable accessibility anchor used by existing tests and
    Web clients.
    """
    title_parts: list[ft.Control] = [
        ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color=C.TEXT),
    ]
    if step_label is not None:
        title_parts.append(ft.Container(content=step_label, padding=5))
    if icon:
        title_parts.append(tone_icon(icon, tone, size=18, box=34, radius=10))
    return ft.Row(
        title_parts,
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def dialog_button(
    label: str,
    on_click,
    *,
    role: str = "secondary",
    icon=None,
) -> ft.Button:
    """Use one Web-safe button class and shared role styling in every dialog."""
    kwargs = {"content": label, "on_click": on_click, "icon": icon}
    if role == "primary":
        kwargs.update(bgcolor=C.EM, color=C.ON_EM)
    elif role == "preview":
        kwargs.update(bgcolor=C.DIA, color=C.ON_EM)
    return ft.Button(**kwargs)


def build_extract_form(
    *,
    path_section: ft.Control,
    state: ExtractFormState,
    language_codes: Sequence[str],
    on_change: Callable[[], None] | None = None,
) -> ExtractFormControls:
    """Build the common extraction options for either pipeline entry point."""
    mode = ft.RadioGroup(
        content=ft.Column(
            [
                ft.Radio(label="提取 Lang", value="lang"),
                ft.Radio(label="提取 Book", value="book"),
                ft.Radio(label="全部執行（Lang + Book）", value="both"),
            ],
            spacing=4,
        ),
        value=state.mode,
    )
    checks = {
        code: ft.Checkbox(label=code, value=state.lang_codes.get(code, True))
        for code in language_codes
    }

    def update_mode(event):
        state.mode = event.control.value
        if on_change:
            on_change()

    def update_languages(_event=None):
        state.lang_codes.update(
            {key: bool(control.value) for key, control in checks.items()}
        )
        if on_change:
            on_change()

    mode.on_change = update_mode
    for checkbox in checks.values():
        checkbox.on_change = update_languages
    content = ft.Column(
        [
            path_section,
            ft.Text("執行模式", weight="bold", size=13),
            mode,
            ft.Text("處理的語言代碼", weight="bold", size=13),
            ft.Container(
                content=ft.Column([checks[key] for key in language_codes], spacing=2)
            ),
        ],
        spacing=10,
        tight=False,
    )
    return ExtractFormControls(content, mode, checks)


def _build_merge_option_controls(
    state: MergeFormState, zh_cn_enabled: bool
) -> _MergeOptionControls:
    only_lang = ft.Switch(label="只處理 lang 檔案", value=state.only_lang)
    process_zh_cn = ft.Switch(
        label="處理 zh_cn 檔案",
        value=state.process_zh_cn if zh_cn_enabled else True,
    )
    patchouli_skip = ft.Switch(
        label="允許 zh_cn 觸發跳過 en_us",
        value=state.patchouli_skip,
        disabled=not bool(process_zh_cn.value),
    )
    patchouli_threshold = SyncTextField(
        value=str(state.patchouli_threshold),
        width=100,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值 0.5",
        disabled=not bool(process_zh_cn.value),
    )
    zh_en_threshold = SyncTextField(
        value=str(state.zh_en_threshold),
        width=80,
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        text_align=ft.TextAlign.CENTER,
        hint_text="空白用預設值 2",
    )
    return _MergeOptionControls(
        only_lang,
        process_zh_cn,
        patchouli_skip,
        patchouli_threshold,
        zh_en_threshold,
    )


def _bind_merge_option_events(
    controls: _MergeOptionControls,
    state: MergeFormState,
    on_zh_cn_change: Callable | None,
    on_value_change: Callable | None,
) -> None:
    only_lang = controls.only_lang
    process_zh_cn = controls.process_zh_cn
    patchouli_skip = controls.patchouli_skip
    patchouli_threshold = controls.patchouli_threshold
    zh_en_threshold = controls.zh_en_threshold

    def update_thresholds(_event=None):
        try:
            state.patchouli_threshold = float((patchouli_threshold.value or "").strip())
        except (TypeError, ValueError):
            pass
        try:
            state.zh_en_threshold = int((zh_en_threshold.value or "").strip())
        except (TypeError, ValueError):
            pass
        if on_value_change:
            on_value_change()

    def update_zh_cn(event):
        control = getattr(event, "control", None)
        state.process_zh_cn = bool(getattr(control, "value", process_zh_cn.value))
        patchouli_skip.disabled = not state.process_zh_cn
        patchouli_threshold.disabled = not state.process_zh_cn
        if not state.process_zh_cn:
            patchouli_skip.value = False
            state.patchouli_skip = False
        if on_zh_cn_change:
            on_zh_cn_change(event)
        if on_value_change:
            on_value_change()

    def update_switches(_event=None):
        state.only_lang = bool(only_lang.value)
        state.patchouli_skip = bool(patchouli_skip.value)
        if on_value_change:
            on_value_change()

    only_lang.on_change = update_switches
    process_zh_cn.on_change = update_zh_cn
    patchouli_skip.on_change = update_switches
    patchouli_threshold.on_change = update_thresholds
    zh_en_threshold.on_change = update_thresholds


def _build_merge_form_content(
    path_section: ft.Control,
    db_card: ft.Control,
    controls: _MergeOptionControls,
    readonly_db_explanation: bool,
) -> ft.Column:
    only_lang = controls.only_lang
    process_zh_cn = controls.process_zh_cn
    patchouli_skip = controls.patchouli_skip
    patchouli_threshold = controls.patchouli_threshold
    zh_en_threshold = controls.zh_en_threshold

    db_section = ft.Column(
        (
            [
                ft.Text(
                    "合併步驟可獨立停用資料庫；版本選擇會顯示實際生效來源。",
                    size=11,
                    color=C.MUTED,
                )
            ]
            if readonly_db_explanation
            else []
        )
        + [db_card]
    )
    content = ft.Column(
        [
            path_section,
            ft.Divider(),
            db_section,
            ft.Divider(),
            ft.Text("語系過濾設定", weight="bold", size=13),
            only_lang,
            process_zh_cn,
            ft.Row(
                [
                    ft.Text("zh 英文含量閾值", weight=ft.FontWeight.W_500, size=12),
                    zh_en_threshold,
                    ft.Text("用於 lang 過濾", size=10, color=C.MUTED),
                ],
                wrap=True,
            ),
            ft.Divider(),
            ft.Text("Patchouli 進階設定", weight="bold", size=13),
            ft.Row(
                [
                    ft.Column(
                        [
                            patchouli_skip,
                            ft.Text(
                                "zh_cn 翻譯足夠好時跳過 en_us", size=10, color=C.MUTED
                            ),
                        ],
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                "en_us 跳過門檻", weight=ft.FontWeight.W_500, size=12
                            ),
                            patchouli_threshold,
                            ft.Text("有效翻譯比例 0.0～1.0", size=10, color=C.MUTED),
                        ],
                    ),
                ],
                spacing=8,
                wrap=True,
            ),
        ],
        spacing=10,
        tight=False,
    )
    return content


def build_merge_form(
    *,
    path_section: ft.Control,
    state: MergeFormState,
    db_card: ft.Control,
    zh_cn_enabled: bool = True,
    readonly_db_explanation: bool = False,
    on_zh_cn_change: Callable | None = None,
    on_value_change: Callable | None = None,
) -> MergeFormControls:
    """Build shared merge filters, Patchouli options and Mod DB controls."""
    options = _build_merge_option_controls(state, zh_cn_enabled)
    _bind_merge_option_events(options, state, on_zh_cn_change, on_value_change)
    content = _build_merge_form_content(
        path_section, db_card, options, readonly_db_explanation
    )
    return MergeFormControls(
        content,
        options.only_lang,
        options.process_zh_cn,
        options.patchouli_skip,
        options.patchouli_threshold,
        options.zh_en_threshold,
    )


def build_translate_form(
    *,
    path_section: ft.Control,
    state: TranslateFormState,
    db_card: ft.Control,
    on_value_change: Callable | None = None,
) -> TranslateFormControls:
    """Build the shared LM DB and translation execution options."""
    dry_run = ft.Switch(label="Dry Run（只分析不翻譯）", value=state.dry_run)
    write_cache = ft.Switch(
        label="每次寫入新快取分片（關閉時寫入編號分片）",
        value=state.write_new_cache,
    )

    def update(_event=None):
        state.dry_run = bool(dry_run.value)
        state.write_new_cache = bool(write_cache.value)
        if on_value_change:
            on_value_change()

    dry_run.on_change = update
    write_cache.on_change = update
    content = ft.Column(
        [
            path_section,
            ft.Divider(),
            db_card,
            ft.Divider(),
            ft.Text("執行選項", weight="bold", size=13),
            dry_run,
            write_cache,
        ],
        spacing=10,
        tight=False,
    )
    return TranslateFormControls(content, dry_run, write_cache)


def build_version_input(
    *,
    page: ft.Page,
    value: str,
    max_value: str = "",
    on_change: Callable[[str], None],
    on_max_change: Callable[[str], None],
    border_color,
) -> VersionInputControls:
    """版本輸入框（1.20.4 或 pack_format 整數）+ 選填的最大使用版本，下方即時換算。

    ``pack_format()``／``max_pack_format()`` 回傳要寫入的整數（最大版本空白時為 None）；
    輸入無效時拋出 ``ValueError``。
    """
    summary = ft.Text("", size=11, color=C.MUTED)

    def refresh(*_args) -> None:
        _pack_format, message, valid = explain_version_input(
            version_field.value, max_field.value
        )
        summary.value = message if valid else f"⚠️ {message}"
        summary.color = C.MUTED if valid else C.GOLD

    def handle_change(event) -> None:
        on_change(event.control.value or "")
        refresh()
        page.update()

    def handle_max_change(event) -> None:
        on_max_change(event.control.value or "")
        refresh()
        page.update()

    version_field = dialog_text_field(
        page,
        reserved_width=80,
        label="版本或 pack_format",
        hint_text="輸入遊戲版本，例如 1.21.1；或直接輸入 pack_format 整數；留空使用預設",
        value=value,
        dense=True,
        border_color=border_color,
        on_change=handle_change,
    )
    max_field = dialog_text_field(
        page,
        reserved_width=80,
        label="最大使用版本（max_inclusive，選填）",
        hint_text="填版本或整數，例如 1.21.4 或 999；留空使用預設",
        value=max_value,
        dense=True,
        border_color=border_color,
        on_change=handle_max_change,
    )

    def use_quick_version(version: str) -> None:
        version_field.value = version
        on_change(version)
        refresh()
        page.update()

    quick_row = ft.Row(
        [
            ft.Text("常用：", size=11, color=C.MUTED),
            *[
                dialog_button(v, lambda _event, v=v: use_quick_version(v))
                for v in QUICK_VERSIONS
            ],
        ],
        wrap=True,
        spacing=6,
    )
    refresh()
    return VersionInputControls(
        ft.Column(
            [version_field, quick_row, max_field, summary], spacing=6, tight=False
        ),
        version_field,
        max_field,
        summary,
        lambda: resolve_version_input(version_field.value).pack_format,
        lambda: resolve_max_input(max_field.value),
    )


def _form_section(title: str, controls: Sequence[ft.Control]) -> ft.Container:
    """一組相關欄位：標題加框線，讓長表單分成可掃讀的區塊。"""
    return ft.Container(
        content=ft.Column(
            [ft.Text(title, weight="bold", size=14), *controls],
            spacing=8,
            tight=True,
        ),
        padding=12,
        border=ft.Border.all(1, C.DIM),
        border_radius=8,
    )


def build_bundle_form(
    *,
    path_section: ft.Control,
    description_field: ft.Control,
    version_input: VersionInputControls,
    pack_image_row: ft.Control,
    extra_folders_section: ft.Control,
    feedback: ft.Control | None = None,
) -> ft.Column:
    """Assemble the common bundle form and fixed ordering for both entry points."""
    controls = [
        _form_section("路徑", [path_section]),
        _form_section(
            "資源包資訊",
            [
                ft.Text("檔案敘述", size=12, color=C.MUTED),
                description_field,
                ft.Text("Minecraft 版本", size=12, color=C.MUTED),
                version_input.content,
            ],
        ),
        _form_section(
            "封面與額外內容",
            [
                ft.Text("封面圖片（可留空）", size=12, color=C.MUTED),
                pack_image_row,
                ft.Text("其他指定資料夾", size=12, color=C.MUTED),
                extra_folders_section,
            ],
        ),
    ]
    if feedback is not None:
        controls.append(feedback)
    return ft.Column(controls, spacing=12, tight=False)
