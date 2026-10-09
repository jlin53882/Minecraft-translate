"""Shared forms and typed state for standalone and one-click pipeline dialogs.

Each dialog entry point owns its controls and runtime callbacks, while the
option rows, version selector, spacing and validation state are built here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import flet as ft

from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    RESPONSIVE_FIELD_DATA_KEY,
    dialog_dimensions,
)
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
    write_new_cache: bool = True


@dataclass
class BundleFormState:
    description: str = ""
    version: str = ""
    pack_image: str | None = None
    extra_folders: list[str] = field(default_factory=list)
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
class VersionPickerControls:
    content: ft.Column
    search: SyncTextField
    selection: ft.Container
    selected_label: ft.Text
    list_container: ft.Container
    list_view: ft.ListView
    refresh: Callable[[str], None]
    select: Callable[[str], None]
    toggle: Callable[..., None]


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
        label="寫入新快取（每次回傳單獨快取）", value=state.write_new_cache
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


def _build_version_picker_triggers(
    page: ft.Page,
    border_color,
    selected_label: ft.Text,
    list_container: ft.Container,
    refresh: Callable,
    toggle: Callable,
) -> tuple[ft.Control, ft.Control]:
    search = dialog_text_field(
        page,
        reserved_width=80,
        label="搜尋版本",
        hint_text="輸入版本關鍵字...",
        dense=True,
        border_color=border_color,
        on_change=lambda event: refresh(event.control.value or ""),
    )
    selection = ft.Container(
        content=ft.Row(
            [
                ft.Text("已選擇：", size=11, color=C.MUTED),
                selected_label,
                ft.Icon(ft.Icons.EXPAND_MORE, size=18),
            ]
        ),
        padding=8,
        border=ft.Border.all(1, C.DIM),
        border_radius=6,
        on_click=toggle,
    )
    return search, selection


def build_version_picker(
    *,
    page: ft.Page,
    versions: Sequence[str],
    selected: str,
    on_select: Callable[[str], None],
    border_color,
) -> VersionPickerControls:
    """Build the single searchable Minecraft pack-format picker for both UIs."""
    selected_label = ft.Text(
        selected or "點擊選擇版本", size=12, color=C.MUTED, expand=True
    )
    list_view = ft.ListView(expand=True, height=156, spacing=4, auto_scroll=False)
    list_container = ft.Container(
        content=list_view,
        height=196,
        border=ft.Border.all(1, C.DIM),
        border_radius=6,
        padding=4,
        visible=False,
    )
    expanded = False

    def refresh(query: str = "", *, update: bool = True):
        _populate_version_list(list_view, versions, query, select)
        if update:
            try:
                page.update()
            except RuntimeError:
                pass

    def select(version: str):
        nonlocal expanded
        on_select(version)
        selected_label.value = version or "點擊選擇版本"
        selected_label.color = None if version else C.MUTED
        expanded = False
        list_container.visible = False
        page.update()

    def toggle(_event=None):
        nonlocal expanded
        expanded = not expanded
        list_container.visible = expanded
        page.update()

    search, selection = _build_version_picker_triggers(
        page, border_color, selected_label, list_container, refresh, toggle
    )
    refresh(update=False)
    content = ft.Column([search, selection, list_container], spacing=6, tight=False)
    return VersionPickerControls(
        content,
        search,
        selection,
        selected_label,
        list_container,
        list_view,
        refresh,
        select,
        toggle,
    )


def _populate_version_list(
    list_view: ft.ListView,
    versions: Sequence[str],
    query: str,
    select: Callable[[str], None],
) -> None:
    list_view.controls.clear()
    normalized = (query or "").casefold().strip()
    matches = [value for value in versions if normalized in value.casefold()]
    if not matches:
        list_view.controls.append(ft.Text("無可用版本", size=12, color=C.DIM))
    for version in matches:
        list_view.controls.append(
            ft.Container(
                content=ft.Text(version, size=13),
                padding=8,
                border=ft.Border.all(1, C.DIM),
                border_radius=6,
                on_click=lambda _event, value=version: select(value),
            )
        )


def build_bundle_form(
    *,
    path_section: ft.Control,
    description_field: ft.Control,
    version_picker: VersionPickerControls,
    pack_image_row: ft.Control,
    extra_folders_section: ft.Control,
    feedback: ft.Control | None = None,
) -> ft.Column:
    """Assemble the common bundle form and fixed ordering for both entry points."""
    controls = [
        path_section,
        ft.Text("檔案敘述", weight="bold", size=13),
        description_field,
        ft.Text("Minecraft 版本", weight="bold", size=13),
        version_picker.content,
        ft.Text("封面圖片（可留空）", weight="bold", size=13),
        pack_image_row,
        ft.Text("其他指定資料夾", weight="bold", size=13),
        extra_folders_section,
    ]
    if feedback is not None:
        controls.append(feedback)
    return ft.Column(controls, spacing=10, tight=False)
