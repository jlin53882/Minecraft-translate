"""Event and operation lifecycle for Mod DB old-AI retranslation."""

from __future__ import annotations

import threading
from pathlib import Path

import flet as ft

from app.services_impl.moddb_retranslate_service import (
    SameSourceAIRepairPreview,
    cache_profile_label,
    preview_same_source_ai_retranslation_from_path,
)
from app.services_impl.moddb_source_service import source_catalog_for, source_label
from app.services_impl.moddb_translate_service import TranslateOptions
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui.design import C
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count
from translation_tool.utils.log_unit import log_warning


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
    """Render candidate scope, source breakdown and a few reviewable samples."""
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
    ai_plan = (
        f"AI 代表 {format_count(result.ai_representatives)} 筆；"
        f"預計共用 {format_count(result.dedup_reused_candidates)} 筆候選；"
    )
    representative_breakdown = (
        "、".join(
            f"{cache_profile_label(cache_type)}：{format_count(count)} 筆代表"
            for cache_type, count in result.representative_profile_counts
        )
        or "無代表項目"
    )
    if result.mode == "quality_mismatch":
        sources = "、".join(source_label(source, catalog) for source in result.sources)
        panel.repair_preview_text.value = (
            f"全範圍修復合格來源列 {format_count(result.total_candidates)} 筆；"
            f"候選中有 {format_count(result.input_newline_mismatch_candidates)} 筆輸入換行數不同"
            f"（其中另有硬格式問題 {format_count(result.input_newline_mismatch_with_other_hard_issues)} 筆）。\n"
            f"本次 AI 代表上限：{cap}；選中 {format_count(result.ai_representatives)} 個代表，"
            f"對應 {format_count(selected)} 個來源列。來源：{sources or '無'}。\n"
            "只更新預覽中的來源列；AI 輸出實體換行數可不同，其他格式 token 仍須一致。\n"
            f"等價映射 {format_count(result.dedup_reused_candidates)} 筆；"
            f"翻譯 profile：{breakdown}；{representative_breakdown}；"
            f"目前預估：約 {format_count(result.estimated_batches)} 個外層批次。"
        )
    else:
        panel.repair_preview_text.value = (
            f"符合條件：{format_count(result.total_candidates)} 筆；{cap}，"
            f"本次將重翻 {format_count(selected)} 筆。\n"
            f"來源：{source_label(result.source, catalog)}；只處理目前生效來源為 AI 且譯文等於原文的項目；"
            "人工、模組自帶及其他來源不會被重新翻譯。\n"
            f"{ai_plan}翻譯 profile：{breakdown}；{representative_breakdown}；"
            f"目前預估：約 {format_count(result.estimated_batches)} 個外層批次。"
        )
    panel.repair_samples.controls = [
        ft.Text(
            f"[{cache_profile_label(result.entry_cache_types[index])}] "
            f"[{source_label(row.source_id, catalog)}] {row.mod_id} / {row.key}\n"
            f"原文：{row.en_us}\n目前譯文：{row.current_ai_translation}",
            size=12,
            color=C.MUTED,
            selectable=True,
        )
        for index, row in enumerate(result.entries[:5])
    ]
    update_start_button(panel)
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
                    f"即將修復 {selected.ai_representatives:,} 個 AI 代表，最多對應 "
                    f"{selected.selected_count:,} 筆來源譯文；去重共用可能更新超過代表上限的來源列。"
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
