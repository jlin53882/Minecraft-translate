"""Event and operation lifecycle for Mod DB old-AI retranslation."""

from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path

import flet as ft

from app.services_impl.moddb_retranslate_service import (
    SameSourceAIRepairPreview,
    cache_profile_label,
    preview_same_source_ai_retranslation_from_path,
)
from app.services_impl.moddb_service import format_tokens, token_category
from app.services_impl.moddb_source_service import source_catalog_for, source_label
from app.services_impl.moddb_translate_service import TranslateOptions
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import kit
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.format_diff_review import FormatDiffReview
from app.views.moddb.formatting import format_count, visible_breaks
from translation_tool.utils.log_unit import log_warning

_PREVIEW_SOURCE_ALL = "__all__"
_PREVIEW_ISSUE_ALL = "all"
PREVIEW_PAGE_SIZE = 50  # 預覽候選每頁顯示的翻譯 Key 數
_PREVIEW_ISSUE_LABELS = {
    "all": "全部異常類型",
    "minecraft": "Minecraft 格式碼 §",
    "newline": "實際換行",
    "placeholder": "佔位符",
    "patchouli": "Patchouli 標記",
    "other": "其他格式",
}


def launch_standalone_worker(target) -> None:
    """Compatibility launcher used only when the Page has no AppShell registry."""
    threading.Thread(target=target, daemon=True).start()


def database_identity(db) -> tuple[str, tuple[int, ...]] | None:
    if db is None:
        return None
    path = getattr(db, "path", None)
    path_identity = (
        str(Path(path).resolve()) if path is not None else f"object:{id(db)}"
    )
    return path_identity, tuple(getattr(db, "priority", ()))


def clear_preview(panel) -> None:
    panel._repair_preview_generation += 1
    panel._repair_preview = None
    panel._repair_preview_db_identity = None
    panel.repair_preview_text.value = (
        "範圍已變更；舊預覽結果將丟棄，待查詢完成後可重新預覽。"
        if panel._repair_preview_running
        else panel._repair_default_text()
    )
    panel.repair_samples.controls = []
    panel.repair_samples.height = 0
    panel.repair_samples.visible = False
    panel.repair_preview_pager.visible = False
    panel.repair_preview_search.value = ""
    kit.set_dropdown_options(
        panel.repair_preview_source_filter,
        [(_PREVIEW_SOURCE_ALL, "全部翻譯來源")],
    )
    panel.repair_preview_source_filter.value = _PREVIEW_SOURCE_ALL
    kit.set_dropdown_options(
        panel.repair_preview_issue_filter,
        [(_PREVIEW_ISSUE_ALL, _PREVIEW_ISSUE_LABELS[_PREVIEW_ISSUE_ALL])],
    )
    panel.repair_preview_issue_filter.value = _PREVIEW_ISSUE_ALL
    panel.repair_preview_count.value = "尚未載入候選預覽。"
    panel.repair_preview_btn.disabled = panel._running or panel._repair_preview_running
    update_start_button(panel)


def update_start_button(panel, *, running: bool | None = None) -> None:
    is_running = panel._running if running is None else running
    has_empty_preview = (
        panel._repair_preview is not None and panel._repair_preview.selected_count == 0
    )
    panel.repair_start_btn.disabled = (
        is_running or panel._repair_preview_running or has_empty_preview
    )
    panel.repair_mode_group.disabled = is_running or panel._repair_preview_running


def _query_preview(
    panel,
    database_path: Path,
    database_priority: tuple[int, ...],
    options: TranslateOptions,
    generation: int,
    db_identity: tuple[str, tuple[int, ...]],
    mode: str,
) -> None:
    try:
        result = preview_same_source_ai_retranslation_from_path(
            database_path, database_priority, options, mode=mode
        )
    except Exception as exc:  # worker records failure and reports it to the UI
        _schedule_result(panel, generation, options, db_identity, None, str(exc))
        raise
    _schedule_result(panel, generation, options, db_identity, result, None)


def preview(panel, _e=None) -> None:
    if panel._running:
        show_snack(panel._page, "機翻正在執行中", C.GOLD)
        return
    if panel._repair_preview_running:
        show_snack(panel._page, "譯文修復預覽仍在查詢中，請稍候", C.GOLD)
        return
    panel._reset_stats(mode="repair")
    clear_preview(panel)
    if not panel.version_dd.value:
        panel.repair_preview_text.value = "請先選擇遊戲版本。"
        panel._safe_update()
        return
    db = panel._get_db()
    if db is None:
        panel.repair_preview_text.value = "無法開啟 Mod 資料庫。"
        panel._safe_update()
        return

    path = getattr(db, "path", None)
    if path is None:
        panel.repair_preview_text.value = "無法取得資料庫路徑，未啟動預覽。"
        panel._safe_update()
        return
    try:
        database_path = Path(path).resolve()
        database_priority = tuple(getattr(db, "priority", ()))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        panel.repair_preview_text.value = f"無法準備唯讀資料庫連線：{exc}"
        log_warning(f"準備譯文修復預覽資料庫快照失敗：{exc!r}")
        panel._safe_update()
        return
    options = panel.build_options()
    mode = panel._repair_mode
    db_identity = (str(database_path), database_priority)
    generation = panel._repair_preview_generation
    panel._repair_preview_running = True
    panel.repair_preview_btn.disabled = True
    panel.repair_preview_text.value = (
        "正在背景查詢符合條件的舊 AI 譯文（不會呼叫 AI）；"
        "查詢完成前可繼續操作其他頁面。"
        if mode == "same_source_ai"
        else "正在背景檢查各來源譯文（不會呼叫 AI）；查詢完成前可繼續操作其他頁面。"
    )
    update_start_button(panel)
    panel._safe_update()
    _launch_preview_query(
        panel, database_path, database_priority, options, generation, db_identity, mode
    )


def _launch_preview_query(panel, path, priority, options, generation, identity, mode):
    """Launch the read-only query and restore the card if admission fails."""
    try:
        launched = launch_page_operation(
            panel._page,
            lambda: _query_preview(
                panel, path, priority, options, generation, identity, mode
            ),
            name="Mod 資料庫譯文修復預覽",
            owner="moddb-retranslate-preview",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            commit=CommitPolicy.EPHEMERAL,
            durability=DurabilityPolicy.RECOMPUTABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            fallback_launcher=launch_standalone_worker,
        )
    except Exception as exc:  # noqa: BLE001 - restore controls when the operation cannot launch
        panel._repair_preview_running = False
        panel.repair_preview_btn.disabled = panel._running
        panel.repair_preview_text.value = f"預覽無法啟動：{exc}"
        log_warning(f"Mod 資料庫譯文修復預覽無法啟動：{exc!r}")
        update_start_button(panel)
        panel._safe_update()
        return
    if not launched:
        panel._repair_preview_running = False
        panel.repair_preview_btn.disabled = panel._running
        panel.repair_preview_text.value = "應用程式正在關閉，未啟動預覽查詢。"
        update_start_button(panel)
        panel._safe_update()


def _schedule_result(
    panel,
    generation: int,
    options: TranslateOptions,
    db_identity: tuple[str, tuple[int, ...]] | None,
    result: SameSourceAIRepairPreview | None,
    error: str | None,
) -> None:
    """Marshal worker output; this function never writes Flet controls."""
    try:
        panel._page.run_task(
            apply_preview_result, panel, generation, options, db_identity, result, error
        )
    except Exception as exc:  # noqa: BLE001 - a disposed page may reject UI work
        log_warning(f"舊 AI 重翻預覽結果無法排回 UI：{exc!r}")


async def apply_preview_result(
    panel,
    generation: int,
    options: TranslateOptions,
    db_identity: tuple[str, tuple[int, ...]] | None,
    result: SameSourceAIRepairPreview | None,
    error: str | None,
) -> None:
    """Apply only the latest preview whose scope and database are still current."""
    if generation != panel._repair_preview_generation:
        panel._repair_preview_running = False
        panel.repair_preview_btn.disabled = panel._running
        panel.repair_preview_text.value = (
            "範圍已變更，舊預覽結果已丟棄；請使用目前條件重新預覽。"
        )
        update_start_button(panel)
        panel._safe_update()
        return

    panel._repair_preview_running = False
    panel.repair_preview_btn.disabled = panel._running
    if error is not None:
        panel.repair_preview_text.value = f"預覽失敗：{error}"
        log_warning(f"Mod 資料庫譯文修復預覽失敗：{error}")
        update_start_button(panel)
        panel._safe_update()
        return

    try:
        current_identity = database_identity(panel._get_db())
    except Exception as exc:  # noqa: BLE001 - unavailable database means stale result
        current_identity = None
        log_warning(f"檢查舊 AI 重翻預覽資料庫身分失敗：{exc!r}")
    if current_identity != db_identity:
        panel._repair_preview = None
        panel._repair_preview_db_identity = None
        panel.repair_preview_text.value = (
            "資料庫已變更或關閉，舊預覽結果已丟棄；請重新預覽。"
        )
        update_start_button(panel)
        panel._safe_update()
        return

    assert result is not None
    _display_preview_result(panel, options, db_identity, result)


def _display_preview_result(panel, options, db_identity, result):
    """Render the exact candidate snapshot and initialize grouped preview filters."""
    panel._repair_preview = result
    panel._repair_preview_db_identity = db_identity
    selected = result.selected_count
    cap = f"上限 {format_count(options.limit)} 筆" if options.limit else "不限筆數"
    breakdown = (
        "、".join(
            f"{cache_profile_label(cache_type)}：{format_count(count)} 筆"
            for cache_type, count in result.profile_counts
        )
        or "無符合類型"
    )
    catalog = source_catalog_for(panel._get_db())
    if result.mode == "quality_mismatch":
        sources = "、".join(source_label(source, catalog) for source in result.sources)
        panel.repair_preview_text.value = (
            f"修復候選預覽｜待處理來源項目 {format_count(result.total_candidates)} 筆；"
            f"候選中有 {format_count(result.input_newline_mismatch_candidates)} 筆輸入換行數不同"
            f"（其中另有硬格式問題 {format_count(result.input_newline_mismatch_with_other_hard_issues)} 筆）。\n"
            f"本次將送 AI 修復 {format_count(selected)} 筆來源項目。\n"
            + (
                "已納入只有實際換行數不同的項目（AI 結果換行數須與原文一致）。\n"
                if result.newline_only_included
                else f"略過只有實際換行數不同：{format_count(result.skipped_newline_only_candidates)} 筆（不佔 AI 名額）。\n"
            )
            + f"來源：{sources or '無'}；翻譯類型：{breakdown}；"
            f"規劃批次：{format_count(result.estimated_batches)}（不等於實際 HTTP 請求次數）。"
        )
    else:
        panel.repair_preview_text.value = (
            f"修復候選預覽｜待處理來源項目 {format_count(result.total_candidates)} 筆；{cap}，"
            f"本次將重翻 {format_count(selected)} 筆。\n"
            f"來源：{source_label(result.source, catalog)}；只處理目前生效來源為 AI 且譯文等於原文的項目；"
            "人工、模組自帶及其他來源不會被重新翻譯。\n"
            f"翻譯類型分布：{breakdown}；"
            f"規劃批次：{format_count(result.estimated_batches)}（不等於實際 HTTP 請求次數）。"
        )
    kit.set_dropdown_options(
        panel.repair_preview_source_filter,
        [(_PREVIEW_SOURCE_ALL, "全部翻譯來源")]
        + [
            (str(source), source_label(source, catalog))
            for source in sorted({int(row.source_id or 0) for row in result.entries})
        ],
    )
    panel.repair_preview_source_filter.value = _PREVIEW_SOURCE_ALL
    kit.set_dropdown_options(
        panel.repair_preview_issue_filter,
        [
            (key, label)
            for key, label in _PREVIEW_ISSUE_LABELS.items()
            if key == _PREVIEW_ISSUE_ALL
            or any(key in _entry_issue_categories(row) for row in result.entries)
        ],
    )
    panel.repair_preview_issue_filter.value = _PREVIEW_ISSUE_ALL
    refresh_preview_cards(panel, update=False)
    update_start_button(panel)
    panel._safe_update()


def _entry_issue_categories(row) -> set[str]:
    """Return filter categories from the same token comparison used by repair validation."""
    wanted = format_tokens(row.en_us)
    actual = format_tokens(row.current_ai_translation)
    categories = set()
    for token in set(wanted) | set(actual):
        if wanted[token] != actual[token]:
            categories.add(token_category(token))
    return categories or {"other"}


def _preview_groups(result):
    """Group one immutable preview snapshot by compatible translation key context."""
    groups = defaultdict(list)
    for index, row in enumerate(result.entries):
        group_key = (row.mc_version, row.mod_id, row.kind, row.key, row.en_us)
        groups[group_key].append((result.entry_cache_types[index], row))
    return list(groups.values())


def _preview_group_matches(
    group, query: str, source_filter: str, issue_filter: str
) -> bool:
    query = query.strip().casefold()
    if source_filter != _PREVIEW_SOURCE_ALL and not any(
        str(row.source_id or 0) == source_filter for _cache_type, row in group
    ):
        return False
    if issue_filter != _PREVIEW_ISSUE_ALL and not any(
        issue_filter in _entry_issue_categories(row) for _cache_type, row in group
    ):
        return False
    if not query:
        return True
    searchable = "\n".join(
        [
            str(value)
            for _cache_type, row in group
            for value in (
                row.mc_version,
                row.mod_id,
                row.kind,
                row.key,
                row.en_us,
                row.current_ai_translation,
                source_label(row.source_id),
            )
        ]
    ).casefold()
    return query in searchable


def _copy_preview_text(panel, text: str) -> None:
    async def copy() -> None:
        await ft.Clipboard().set(text)

    panel._page.run_task(copy)


def _build_preview_detail(panel, group) -> list:
    """One card's expanded detail: copy button plus each source's format review."""
    first_row = group[0][1]
    controls: list = [
        ft.Row(
            [
                ft.Text(f"來源譯文（{len(group)} 筆）", size=11.5, color=C.MUTED),
                ft.IconButton(
                    icon=ft.Icons.CONTENT_COPY,
                    tooltip="複製英文原文",
                    on_click=lambda _e, text=first_row.en_us: _copy_preview_text(
                        panel, text
                    ),
                ),
            ],
            spacing=3,
        )
    ]
    for source_index, (cache_type, row) in enumerate(group):
        format_review = _build_preview_format_review(
            cache_type, row, show_source=source_index == 0
        )
        controls.append(
            ft.Container(
                padding=8,
                bgcolor=C.PANEL2,
                border_radius=8,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                kit.chip(source_label(row.source_id), "neutral"),
                                ft.Text(
                                    f"{cache_profile_label(cache_type)}｜待修復",
                                    size=11.5,
                                    color=C.GOLD,
                                ),
                            ],
                            spacing=8,
                        ),
                        format_review,
                    ],
                    spacing=4,
                ),
            )
        )
    return controls


def _build_preview_group_card(panel, group, index: int, total: int):
    first_cache_type, first_row = group[0]
    issue_categories = set().union(
        *(_entry_issue_categories(row) for _cache, row in group)
    )
    issue_text = "、".join(
        _PREVIEW_ISSUE_LABELS.get(category, category)
        for category in sorted(issue_categories)
    )
    detail = ft.Column(spacing=8, visible=False)
    toggle = ft.TextButton("查看詳細資料", icon=ft.Icons.EXPAND_MORE)

    def toggle_detail(_e=None):
        if not detail.visible and not detail.controls:
            # 一頁有幾十張卡片、每張可能有多個來源；展開時才建詳細內容，翻頁才不會卡
            detail.controls = _build_preview_detail(panel, group)
        detail.visible = not detail.visible
        toggle.text = "收合詳細資料" if detail.visible else "查看詳細資料"
        toggle.icon = ft.Icons.EXPAND_LESS if detail.visible else ft.Icons.EXPAND_MORE
        panel._safe_update()

    toggle.on_click = toggle_detail
    return ft.Container(
        padding=12,
        border=ft.Border.all(1, C.LINE),
        border_radius=10,
        content=ft.Column(
            [
                ft.Row(
                    [
                        kit.chip(cache_profile_label(first_cache_type), "dia"),
                        ft.Text(
                            f"{first_row.mod_id} · {first_row.kind}",
                            size=12,
                            color=C.MUTED,
                            expand=True,
                        ),
                        kit.chip(f"{len(group)} 個來源", "neutral"),
                        kit.chip(issue_text, "gold"),
                    ],
                    spacing=7,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(first_row.key, size=12.5, color=C.TEXT, selectable=True),
                ft.Text(f"修復項目 {index} / {total}", size=11.5, color=C.MUTED),
                ft.Text(
                    visible_breaks(first_row.en_us),
                    size=13,
                    color=C.TEXT,
                    max_lines=3,
                ),
                ft.Row(
                    [
                        kit.chip("共用 1 次 AI 修復", "dia"),
                        ft.Text(
                            "通過各來源驗證後，分別更新對應譯文",
                            size=11.5,
                            color=C.MUTED,
                        ),
                        toggle,
                    ],
                    spacing=8,
                    wrap=True,
                ),
                detail,
            ],
            spacing=7,
        ),
    )


def _build_preview_format_review(cache_type, row, *, show_source):
    review = FormatDiffReview(
        context_label=source_label(row.source_id),
        source_label="英文原文・en_us",
        target_label=f"{cache_profile_label(cache_type)}・目前譯文",
        compact=True,
        show_source=show_source,
    )
    review.render(row.en_us, row.current_ai_translation, kind=row.kind)
    return review.control


def refresh_preview_cards(panel, *, update: bool = True, page: int = 1) -> None:
    """Filter and render one page of the immutable preview without changing execution entries.

    Changing a filter renders page 1; the pager passes the requested page.
    """
    result = panel._repair_preview
    if result is None:
        panel.repair_samples.controls = []
        panel.repair_samples.height = 0
        panel.repair_samples.visible = False
        panel.repair_preview_pager.visible = False
        panel.repair_preview_count.value = "尚未載入候選預覽。"
        return
    query = str(panel.repair_preview_search.value or "")
    source_filter = str(panel.repair_preview_source_filter.value or _PREVIEW_SOURCE_ALL)
    issue_filter = str(panel.repair_preview_issue_filter.value or _PREVIEW_ISSUE_ALL)
    groups = [
        group
        for group in _preview_groups(result)
        if _preview_group_matches(group, query, source_filter, issue_filter)
    ]
    total_groups = len(_preview_groups(result))
    pager = panel.repair_preview_pager
    pager.set_state(len(groups), page)
    first = (pager.current_page - 1) * PREVIEW_PAGE_SIZE
    render_groups = groups[first : first + PREVIEW_PAGE_SIZE]
    pager.visible = len(groups) > PREVIEW_PAGE_SIZE
    count_text = (
        f"{'符合' if len(groups) > PREVIEW_PAGE_SIZE else '顯示'} {len(groups):,} / "
        f"{total_groups:,} 個翻譯 Key；涵蓋 {sum(len(group) for group in groups):,} 筆來源項目。"
        + (
            " 可展開查看完整原文與各來源譯文。"
            if groups
            else " 沒有符合目前篩選條件的候選。"
        )
    )
    panel.repair_preview_count.value = count_text
    panel.repair_samples.controls = [
        _build_preview_group_card(panel, group, index, len(groups))
        for index, group in enumerate(render_groups, start=first + 1)
    ]
    has_groups = bool(groups)
    panel.repair_samples.height = None if has_groups else 0
    panel.repair_samples.visible = has_groups
    if update:
        panel._safe_update()


def confirm(panel, _e=None) -> None:
    selected = panel._repair_preview
    if panel._running:
        return
    if selected is None:
        show_snack(
            panel._page,
            "請先按「預覽候選譯文」，檢查條件、來源與筆數後再開始修復。",
            C.GOLD,
        )
        return
    if selected.selected_count == 0:
        show_snack(panel._page, "目前沒有符合條件的譯文可修復。", C.GOLD)
        return
    show_dialog = getattr(panel._page, "show_dialog", None)
    if not callable(show_dialog):
        show_snack(panel._page, "目前畫面無法顯示確認視窗，未開始修復", C.GOLD)
        return
    show_dialog(
        ft.AlertDialog(
            title=ft.Text(
                "確認修復各來源譯文"
                if selected.mode == "quality_mismatch"
                else "確認重新翻譯舊 AI 譯文"
            ),
            content=ft.Text(
                (
                    f"即將送出 {selected.selected_count:,} 筆目前生效的來源譯文給 AI 修復。"
                    "保留各筆原有來源標記；若 AI 輸出缺少受保護格式 token 或翻譯失敗，舊譯文會保留。"
                    "人工來源修復後會改為未審核。匯入 ZIP 檔案不會被修改。"
                )
                if selected.mode == "quality_mismatch"
                else (
                    f"即將重翻 {selected.selected_count:,} 筆目前生效來源為「AI 機翻」且"
                    "譯文與原文相同的項目。只更新 AI 來源；人工及其他來源不會更動，"
                    "也不會同步到其他版本。"
                ),
                selectable=True,
                width=440,
            ),
            actions=[
                ft.TextButton(
                    "取消", on_click=lambda _e=None: panel._page.pop_dialog()
                ),
                ft.TextButton(
                    "開始修復",
                    on_click=lambda _e=None: panel._start_retranslation(selected),
                ),
            ],
        )
    )
