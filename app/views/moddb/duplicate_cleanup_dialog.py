"""資料庫重複鍵清理：預覽高信心路徑別名、合併並可復原。"""

from __future__ import annotations

import sqlite3

import flet as ft

from app.services_impl import moddb_cleanup_service as svc
from app.tasks.operation_registry import (
    CancellationPolicy,
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.page_post import post_to_ui
from app.ui.snack import show_snack
from app.ui.update_scope import isolate, update_regions
from app.views.moddb.formatting import format_count, kind_label, shorten
from translation_tool.utils.log_unit import log_exception

ALL_MODULES = "__all__"
ALL_KINDS = "__all__"
KIND_OPTIONS = (
    (ALL_KINDS, "全部類型"),
    ("guide", "Guide 指南文字"),
    ("patchouli", "Patchouli 手冊"),
)
SKIPPED_LABELS = {
    "translation_conflict": "翻譯來源衝突",
    "different_original": "原文不同",
}


class DuplicateCleanupDialog:
    """重複鍵清理的預覽／套用／復原流程。"""

    def __init__(self, page: ft.Page, get_db, version: str | None, on_complete):
        self.page = page
        self.get_db = get_db
        self.version = version
        self.on_complete = on_complete
        self._busy = False
        self._closed = False
        self._generation = 0
        self._scope_seq = 0
        self._plan = None
        self._batches = []
        self._build_controls()
        self._build_dialog()

    def _build_controls(self) -> None:
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=180, on_select=self._on_version
        )
        kit.set_dropdown_options(
            self.version_dd,
            [(self.version, self.version)] if self.version else [],
        )
        self.version_dd.value = self.version or None
        self.mod_dd = kit.dropdown(
            label="模組範圍", dense=True, width=260, on_select=self._on_scope_changed
        )
        kit.set_dropdown_options(self.mod_dd, [(ALL_MODULES, "全部模組")])
        self.mod_dd.value = ALL_MODULES
        self.kind_dd = kit.dropdown(
            label="內容類型", dense=True, width=210, on_select=self._on_scope_changed
        )
        kit.set_dropdown_options(self.kind_dd, list(KIND_OPTIONS))
        self.kind_dd.value = ALL_KINDS
        self.scope_hint = ft.Text("", size=12, color=C.MUTED, selectable=True)
        self._update_scope_hint()
        self.summary = ft.Text(
            "尚未掃描；確認範圍後按「預覽／重新掃描」。", size=13, color=C.GOLD
        )
        self.details = ft.Column(spacing=3, tight=True, scroll=ft.ScrollMode.AUTO)
        self.preview_btn = kit.button(
            "預覽／重新掃描", "secondary", on_click=self._preview
        )
        self.apply_btn = kit.button(
            "確認合併",
            "primary",
            on_click=self._apply,
            disabled=True,
            tooltip="先完成唯讀預覽，確認候選後才能合併",
        )
        self.batch_dd = kit.dropdown(label="尚未還原的合併", dense=True, width=430)
        self.revert_btn = kit.button(
            "還原選取的合併", "secondary", on_click=self._revert, disabled=True
        )
        self.revert_info = ft.Text("", size=12, color=C.MUTED)
        self.status = ft.Text("", size=12, color=C.MUTED, visible=False)
        self.progress = ft.ProgressBar(visible=False)
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("掃描與清理重複鍵"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "預覽只讀，不會修改資料庫。只有同版本、同模組、同類型、"
                            "同原文且沒有翻譯來源衝突的路徑別名，才會列入可安全合併。",
                            size=12.5,
                            color=C.MUTED,
                        ),
                        ft.Text("掃描範圍", size=13, weight=ft.FontWeight.BOLD),
                        ft.Row(
                            [self.version_dd, self.mod_dd, self.kind_dd],
                            spacing=10,
                            wrap=True,
                        ),
                        ft.Container(
                            self.scope_hint,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
                            bgcolor=C.PANEL2,
                            border=ft.Border.all(1, C.LINE),
                            border_radius=design.RADIUS_CONTROL,
                        ),
                        self.summary,
                        ft.Container(
                            content=self.details,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                            bgcolor=C.PANEL2,
                            border=ft.Border.all(1, C.LINE),
                            border_radius=design.RADIUS_CONTROL,
                            height=min(330, max(180, height // 2)),
                        ),
                        ft.Row([self.preview_btn], spacing=8),
                        ft.Text("復原已完成的合併", weight=ft.FontWeight.BOLD),
                        ft.Row([self.batch_dd, self.revert_btn], wrap=True, spacing=8),
                        self.revert_info,
                        self.status,
                        self.progress,
                    ],
                    tight=True,
                    spacing=9,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=min(width, 760),
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.close_btn, self.apply_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        self._closed = False
        self._generation += 1
        try:
            isolate(self.dialog)
            present_page_dialog(self.page, self.dialog)
            self._load_batches()
            self._start_scope_options_load()
            # 開啟即做唯讀預覽，避免使用者看到空白清單而誤以為掃描沒有反應。
            # 範圍變更後仍須按「預覽／重新掃描」，讓套用範圍清楚可控。
            self._preview()
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟重複鍵清理視窗。", C.GOLD)

    def _start_scope_options_load(self) -> None:
        """背景載入版本／模組選項，避免大型資料庫阻塞對話框。"""
        db = self.get_db()
        self._scope_seq += 1
        seq = self._scope_seq
        version = self.version_dd.value
        self.status.visible = True
        self.status.value = "正在載入可用版本與模組…"
        self._safe_update()

        def work() -> None:
            result = error = None
            try:
                versions = list(db.versions()) if db else []
                query_version = (
                    version
                    if version in versions
                    else (versions[0] if versions else None)
                )
                modules = list(db.mods(query_version)) if db and query_version else []
                result = (versions, modules)
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception("重複鍵清理範圍選單載入失敗")
            self._post(
                self._apply_scope_options,
                seq,
                version,
                result,
                error,
                db,
            )

        self._launch(work, "載入重複鍵清理範圍", CommitPolicy.PARTIAL_ALLOWED)

    def _apply_scope_options(self, seq, version, result, error, queried_db) -> None:
        if self._closed or seq != self._scope_seq:
            return
        if self.get_db() is not queried_db:
            # 舊資料庫的版本／模組結果不能套到新資料庫；立即丟棄並重新載入
            # 目前 instance，避免選單看似成功但實際範圍屬於另一份資料。
            self.status.visible = True
            self.status.value = "資料庫已變動，正在重新載入範圍選項…"
            self._safe_update()
            self._start_scope_options_load()
            return
        if error is not None or result is None:
            self.status.visible = True
            self.status.value = f"範圍選單載入失敗：{error}"
            self._safe_update()
            return
        versions, modules = result
        current = self.version_dd.value
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        if current not in versions:
            current = (
                version if version in versions else (versions[0] if versions else None)
            )
        self.version_dd.value = current
        self.version = current or ""
        self._refresh_modules(modules)
        self._update_scope_hint()
        if not self._busy:
            self.status.visible = False
        self._safe_update()

    def _refresh_modules(self, modules) -> None:
        options = [(ALL_MODULES, "全部模組"), *((mod, mod) for mod in modules)]
        selected = self.mod_dd.value
        kit.set_dropdown_options(self.mod_dd, options)
        keys = {key for key, _label in options}
        self.mod_dd.value = selected if selected in keys else ALL_MODULES

    def _selected_scope(self) -> tuple[str, str | None, str | None]:
        version = str(self.version_dd.value or "").strip()
        mod_value = self.mod_dd.value
        kind_value = self.kind_dd.value
        mod_id = None if mod_value in (None, "", ALL_MODULES) else str(mod_value)
        kind = None if kind_value in (None, "", ALL_KINDS) else str(kind_value)
        return version, mod_id, kind

    def _update_scope_hint(self) -> None:
        version, mod_id, kind = self._selected_scope()
        self.scope_hint.value = (
            f"目前範圍：版本 {version or '未選擇'}　·　"
            f"模組 {mod_id or '全部'}　·　類型 {kind_label(kind) if kind else '全部'}"
        )

    def _on_version(self, _e=None) -> None:
        self.version = self.version_dd.value or ""
        self.mod_dd.value = ALL_MODULES
        self._invalidate_scope()
        self._start_scope_options_load()

    def _on_scope_changed(self, _e=None) -> None:
        self._invalidate_scope()

    def _invalidate_scope(self) -> None:
        self._generation += 1
        self._plan = None
        self._update_scope_hint()
        self.summary.value = "掃描範圍已變更，請按「預覽／重新掃描」。"
        self.summary.color = C.GOLD
        self.details.controls = [
            ft.Text("尚未以目前範圍建立預覽。", size=12, color=C.MUTED)
        ]
        self.apply_btn.disabled = True
        self.apply_btn.content = "確認合併"
        self._safe_update()

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "資料庫處理中，請稍候。", C.GOLD)
            return
        self._closed = True
        self._scope_seq += 1
        self._generation += 1
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._closed = True
        self._scope_seq += 1
        self._generation += 1

    def _preview(self, _e=None) -> None:
        db = self.get_db()
        if db is None or self._busy:
            return
        version, mod_id, kind = self._selected_scope()
        if not version:
            self.summary.value = "尚未選擇遊戲版本，無法建立預覽。"
            self.summary.color = C.GOLD
            show_snack(self.page, "請先選擇遊戲版本。", C.GOLD)
            self._safe_update()
            return
        scope = (version, mod_id, kind)
        self.version = version
        previewed_db = db
        generation = self._generation
        self._plan = None
        self.summary.value = "正在依目前範圍掃描重複鍵…"
        self.summary.color = C.MUTED
        self.details.controls = [
            ft.Text("正在建立唯讀預覽，資料庫尚未修改。", size=12, color=C.MUTED)
        ]
        self._set_busy(True, "掃描重複鍵並建立預覽…")
        self._safe_update()

        def work() -> None:
            plan = error = None
            try:
                plan = svc.preview_duplicate_cleanup(
                    db,
                    version,
                    mod_id=mod_id,
                    kind=kind,
                )
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception(f"重複鍵清理預覽失敗：{version}／{mod_id or '全部'}")
            self._post(
                self._after_preview,
                plan,
                error,
                generation,
                previewed_db,
                scope,
            )

        self._launch(work, "Mod 資料庫重複鍵預覽", CommitPolicy.PARTIAL_ALLOWED)

    def _after_preview(self, plan, error, generation, previewed_db, scope) -> None:
        if not self._current(generation):
            return
        self._set_busy(False)
        db_changed = self.get_db() is not previewed_db
        scope_changed = self._selected_scope() != scope
        if db_changed or scope_changed:
            self._plan = None
            message = (
                "資料庫已變動，請重新預覽。"
                if db_changed
                else "掃描範圍已變動，請重新預覽。"
            )
            self.summary.value = message
            self.summary.color = C.GOLD
            self.details.controls = [
                ft.Text("這份結果已過期，避免套用到錯誤範圍。", size=12, color=C.MUTED)
            ]
            show_snack(self.page, message, C.GOLD)
            self._safe_update()
            return
        if error is not None:
            self.summary.value = f"預覽失敗：{error}"
            self.summary.color = C.RED
            self.details.controls = [
                ft.Text("請確認資料庫仍可讀取後重新掃描。", size=12, color=C.MUTED)
            ]
            self._safe_update()
            return
        self._plan = (plan, previewed_db)
        self.summary.value = self._summary(plan)
        self.summary.color = C.TEXT if plan.group_count else C.GOLD
        self.details.controls = self._group_lines(plan)
        self.apply_btn.disabled = plan.group_count == 0
        self.apply_btn.content = f"確認合併 {format_count(plan.duplicate_count)} 筆"
        self._safe_update()

    def _apply(self, _e=None) -> None:
        if self._busy:
            return
        if self._plan is None:
            show_snack(
                self.page,
                "請先按「預覽／重新掃描」，確認目前範圍的候選後才能合併。",
                C.GOLD,
            )
            return
        plan, previewed_db = self._plan
        db = self.get_db()
        db_changed = db is not previewed_db
        scope_changed = self._selected_scope() != (
            plan.version,
            plan.mod_id,
            plan.kind,
        )
        if db_changed or scope_changed:
            self._plan = None
            self._set_busy(False)
            self.apply_btn.content = "確認合併"
            message = (
                "資料庫已變動，請重新預覽。"
                if db_changed
                else "掃描範圍已變動，請重新預覽。"
            )
            self.summary.value = message
            self.summary.color = C.GOLD
            show_snack(self.page, message, C.GOLD)
            self._safe_update()
            return
        generation = self._generation
        self._set_busy(True, "合併重複鍵中…")

        def work() -> None:
            result = error = None
            try:
                result = svc.apply_duplicate_cleanup(
                    db,
                    plan.version,
                    plan.token,
                    mod_id=plan.mod_id,
                    kind=plan.kind,
                )
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception(
                    f"重複鍵清理套用失敗：{plan.version}／{plan.mod_id or '全部'}"
                )
            self._post(self._after_apply, result, error, generation)

        self._launch(
            work,
            "Mod 資料庫重複鍵合併",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _after_apply(self, plan, error, generation) -> None:
        if not self._current(generation):
            return
        self._set_busy(False)
        if error is not None:
            self._plan = None
            self.summary.value = f"合併未執行：{error}"
            self.summary.color = C.RED
            show_snack(self.page, f"合併未執行：{error}", C.RED)
            self._safe_update()
            return
        self._plan = None
        self.summary.value = (
            f"已合併 {format_count(plan.duplicate_count)} 筆，可從下方批次復原。"
        )
        self.summary.color = C.EM
        self.apply_btn.disabled = True
        self._load_batches()
        if plan.batch_id:
            self.batch_dd.value = plan.batch_id
        if self.on_complete:
            self.on_complete()
        self._safe_update()

    def _load_batches(self) -> None:
        db = self.get_db()
        self._batches = (
            [batch for batch in svc.duplicate_cleanup_batches(db) if not batch.complete]
            if db
            else []
        )
        kit.set_dropdown_options(
            self.batch_dd,
            [(batch.batch_id, self._batch_label(batch)) for batch in self._batches],
        )
        self.revert_btn.disabled = not self._batches
        self.revert_info.value = "還沒有重複鍵合併紀錄" if not self._batches else ""

    @staticmethod
    def _batch_label(batch) -> str:
        return (
            f"{batch.created_at}（UTC）　{format_count(batch.group_count)} 組／"
            f"{format_count(batch.entry_count)} 筆"
        )

    def _revert(self, _e=None) -> None:
        db = self.get_db()
        if db is None or not self.batch_dd.value or self._busy:
            return
        generation = self._generation
        self._set_busy(True, "復原重複鍵合併中…")

        def work() -> None:
            result = error = None
            try:
                result = svc.revert_duplicate_cleanup(db, self.batch_dd.value)
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports UI error
                error = exc
                log_exception(f"重複鍵清理復原失敗：{self.batch_dd.value}")
            self._post(self._after_revert, result, error, generation)

        self._launch(
            work,
            "Mod 資料庫重複鍵復原",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _after_revert(self, result, error, generation) -> None:
        if not self._current(generation):
            return
        self._set_busy(False)
        if error is not None:
            show_snack(self.page, f"復原失敗：{error}", C.RED)
            return
        message = f"已復原 {format_count(result.restored_groups)} 組"
        if result.conflicts:
            message += (
                f"；{format_count(len(result.conflicts))} 組因復原後資料已變動而保留"
            )
        self._load_batches()
        if self.on_complete:
            self.on_complete()
        show_snack(self.page, message, C.EM, text_color=C.ON_EM)
        self._safe_update()

    @staticmethod
    def _summary(plan) -> str:
        message = (
            f"共找到 {format_count(len(plan.candidates))} 組候選；"
            f"可安全合併 {format_count(plan.group_count)} 組／"
            f"{format_count(plan.duplicate_count)} 筆"
        )
        if plan.skipped:
            message += "；" + "、".join(
                f"{format_count(count)} 組{SKIPPED_LABELS.get(label, label)}"
                for label, count in plan.skipped.items()
            )
        return message

    @staticmethod
    def _group_lines(plan) -> list[ft.Control]:
        lines = [
            DuplicateCleanupDialog._group_card(group) for group in plan.candidates[:30]
        ]
        if len(plan.candidates) > 30:
            lines.append(
                ft.Text(
                    f"另有 {format_count(len(plan.candidates) - 30)} 組未展開。",
                    size=11.5,
                    color=C.MUTED,
                )
            )
        return lines or [ft.Text("目前沒有發現路徑別名重複。", size=12, color=C.MUTED)]

    @staticmethod
    def _group_card(group) -> ft.Control:
        state = "可安全合併" if group.safe else "需人工確認"
        state_color = C.EM if group.safe else C.GOLD
        keys = "；".join(shorten(entry.key, 100) for entry in group.entries)
        original = shorten(group.entries[0].en_us, 100) if group.entries else ""
        card = [
            ft.Row(
                [
                    ft.Text(
                        state,
                        size=11.5,
                        weight=ft.FontWeight.BOLD,
                        color=state_color,
                    ),
                    ft.Text(
                        f"{kind_label(group.kind)} · {group.mod_id}",
                        size=11.5,
                        color=C.MUTED,
                        expand=True,
                    ),
                    ft.Text(
                        f"{len(group.entries)} 個候選鍵",
                        size=11,
                        color=C.DIM,
                    ),
                ],
                spacing=8,
            ),
            ft.Text(
                f"規範化鍵：{shorten(group.canonical_key, 120)}",
                size=11.5,
                color=C.TEXT,
                selectable=True,
            ),
            ft.Text(f"原文：{original}", size=11.5, color=C.TEXT, selectable=True),
            ft.Text(
                f"來源路徑：{keys}",
                size=11,
                color=C.MUTED,
                selectable=True,
            ),
        ]
        if not group.safe:
            card.append(
                ft.Text(
                    f"原因：{SKIPPED_LABELS.get(group.reason, group.reason)}",
                    size=11,
                    color=C.GOLD,
                )
            )
        return ft.Container(
            content=ft.Column(card, spacing=3, tight=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            bgcolor=C.PANEL,
            border=ft.Border.all(1, state_color),
            border_radius=design.RADIUS_CONTROL,
        )

    def _launch(self, work, name, commit, *, durability=DurabilityPolicy.RECOMPUTABLE):
        try:
            launched = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-duplicate-cleanup",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=commit,
                durability=durability,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except Exception as exc:  # noqa: BLE001 - restore dialog when worker cannot launch
            self._set_busy(False)
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)
            return
        if not launched:
            self._set_busy(False)
            show_snack(self.page, "應用程式正在關閉，未啟動操作。", C.GOLD)

    def _post(self, callback, *args) -> None:
        post_to_ui(
            self.page,
            callback,
            *args,
            label="Mod 資料庫重複鍵清理結果",
            on_failure=self._mark_post_failure,
        )

    def _current(self, generation) -> bool:
        return not self._closed and generation == self._generation

    def _mark_post_failure(self) -> None:
        self._busy = False

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.preview_btn.disabled = busy
        self.version_dd.disabled = busy
        self.mod_dd.disabled = busy
        self.kind_dd.disabled = busy
        self.apply_btn.disabled = (
            busy or self._plan is None or self._plan[0].group_count == 0
        )
        self.batch_dd.disabled = busy
        self.revert_btn.disabled = busy or not self._batches
        self.close_btn.disabled = busy
        self.status.visible = busy
        self.status.value = text
        self.progress.visible = busy
        self.progress.value = None if busy else 0

    def _safe_update(self) -> None:
        update_regions(self.dialog)  # 只重畫這個視窗；視窗已關閉時略過
