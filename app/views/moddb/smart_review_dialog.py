"""UI for previewing, running, and manually applying smart-review candidates."""

from __future__ import annotations

from pathlib import Path

import flet as ft

from app.services_impl.moddb_smart_review_service import (
    SmartReviewCandidate,
    apply_moddb_smart_review_candidate_from_path,
    list_moddb_smart_review_candidates_from_path,
    preview_moddb_smart_review_from_path,
    reject_moddb_smart_review_candidate_from_path,
    run_moddb_smart_review_from_path,
    smart_review_model_options,
)
from app.tasks.operation_registry import (
    CommitPolicy,
    DurabilityPolicy,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import kit
from app.ui.design import C
from app.ui.dialogs import (
    RESPONSIVE_DIALOG_MARKER,
    close_page_dialog,
    dialog_dimensions,
    present_page_dialog,
)
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count, shorten


class SmartReviewDialog:
    """A bounded local AI run whose output stays in a human-review queue."""

    def __init__(self, page: ft.Page, get_db, version: str | None, on_complete):
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self._busy = False
        self._generation = 0
        self._run_id: str | None = None
        self._build_controls(version)
        self._build_dialog()

    def _build_controls(self, version: str | None) -> None:
        db = self.get_db()
        versions = db.versions() if db else []
        self.version_dd = kit.dropdown(
            label="版本", dense=True, width=180, on_select=self._invalidate
        )
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = version if version in versions else None

        self.model_dd = kit.dropdown(
            label="校對模型", dense=True, width=240, on_select=self._invalidate
        )
        options = smart_review_model_options()
        kit.set_dropdown_options(self.model_dd, options)
        self.model_dd.value = (
            "gemini-3.1-flash-lite"
            if any(key == "gemini-3.1-flash-lite" for key, _ in options)
            else (options[0][0] if options else None)
        )
        self.limit_field = kit.text_field(
            "上限（筆）", value="1000", width=150, on_change=self._invalidate
        )
        self.batch_field = kit.text_field(
            "每批（筆）", value="40", width=150, on_change=self._invalidate
        )
        self.preview_btn = kit.button("預覽候選", "secondary", on_click=self._preview)
        self.run_btn = kit.button(
            "開始智慧校對", "primary", icon=ft.Icons.AUTO_AWESOME, on_click=self._run
        )
        self.close_btn = ft.TextButton("關閉", on_click=self._close)
        self.status = ft.Text("尚未預覽", size=12, color=C.MUTED, selectable=True)
        self.summary = ft.Text("尚未載入候選。", size=14, weight=ft.FontWeight.BOLD)
        self.candidates = ft.Column(spacing=8)

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("AI 智慧校對（候選模式）"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "只讀取目前生效且未審核的內容與本地 reviewed context；"
                            "AI 結果先存為候選，不會自動覆蓋譯文或標記已審核。",
                            size=12,
                            color=C.MUTED,
                            selectable=True,
                        ),
                        ft.Row(
                            [
                                self.version_dd,
                                self.model_dd,
                                self.limit_field,
                                self.batch_field,
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                        ft.Row([self.preview_btn, self.run_btn], spacing=8),
                        self.status,
                        self.summary,
                        self.candidates,
                    ],
                    tight=True,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=width,
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.close_btn],
            on_dismiss=self._on_dismiss,
        )

    def open(self) -> None:
        try:
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟智慧校對視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "智慧校對仍在背景執行，請稍候。", C.GOLD)
            return
        self._generation += 1
        close_page_dialog(self.page, self.dialog)

    def _on_dismiss(self, _e=None) -> None:
        self._generation += 1

    def _invalidate(self, _e=None) -> None:
        if not self._busy:
            self._run_id = None
            self.candidates.controls = []
            self.summary.value = "條件已變更，請重新預覽。"
            self._safe_update()

    def _parse_int(self, control, label: str, *, minimum: int = 1) -> int:
        try:
            value = int(str(control.value or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} 必須是整數") from exc
        if value < minimum:
            raise ValueError(f"{label} 必須至少為 {minimum}")
        return value

    def _scope(self) -> tuple[Path, tuple[int, ...], str, int, int, str]:
        db = self.get_db()
        if db is None or not getattr(db, "path", None):
            raise ValueError("目前沒有可用的 Mod 資料庫")
        version = self.version_dd.value
        model = self.model_dd.value
        if not version:
            raise ValueError("請先選擇遊戲版本")
        if not model:
            raise ValueError("請先選擇校對模型")
        return (
            Path(db.path).resolve(),
            tuple(getattr(db, "priority", ())),
            str(version),
            self._parse_int(self.limit_field, "上限"),
            self._parse_int(self.batch_field, "每批"),
            str(model),
        )

    def _preview(self, _e=None) -> None:
        if self._busy:
            return
        try:
            path, priority, version, limit, _batch_size, _model = self._scope()
        except ValueError as exc:
            self.status.value = str(exc)
            self._safe_update()
            return
        self._set_busy(True, "正在唯讀查詢候選…")
        generation = self._generation

        def work() -> None:
            try:
                rows = preview_moddb_smart_review_from_path(
                    path, priority, version=version, limit=limit
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(self._apply_preview, generation, None, str(exc))
                return
            self._post(self._apply_preview, generation, rows, None)

        self._launch(work, "Mod 資料庫智慧校對預覽", CommitPolicy.EPHEMERAL)

    def _apply_preview(self, generation: int, rows, error: str | None) -> None:
        if generation != self._generation:
            self._set_busy(False, "預覽已過期，請重新查詢。")
            return
        self._set_busy(False, "")
        if error:
            self.summary.value = f"預覽失敗：{error}"
            self.candidates.controls = []
        else:
            self.summary.value = (
                f"目前符合候選：{format_count(len(rows))} 筆（預覽不寫入）"
            )
            self.candidates.controls = (
                [
                    ft.Text(
                        f"範例：{shorten(rows[0].en_us, 100)} → {shorten(rows[0].current_zh_tw, 100)}",
                        size=12,
                        color=C.MUTED,
                    )
                ]
                if rows
                else [kit.hint_text("目前條件沒有未審核且有譯文的條目。")]
            )
        self._safe_update()

    def _run(self, _e=None) -> None:
        if self._busy:
            return
        try:
            path, priority, version, limit, batch_size, model = self._scope()
        except ValueError as exc:
            self.status.value = str(exc)
            self._safe_update()
            return
        self._set_busy(True, "AI 校對執行中；候選會分批保存…")
        generation = self._generation

        def progress(snapshot: dict) -> None:
            self._post(
                self._apply_progress,
                generation,
                snapshot.get("processed", 0),
                snapshot.get("selected", 0),
            )

        def work() -> None:
            try:
                result = run_moddb_smart_review_from_path(
                    path,
                    priority,
                    model_name=model,
                    version=version,
                    limit=limit,
                    batch_size=batch_size,
                    progress=progress,
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(self._apply_run, generation, path, priority, None, str(exc))
                return
            self._post(self._apply_run, generation, path, priority, result, None)

        self._launch(
            work,
            "Mod 資料庫智慧校對",
            CommitPolicy.PARTIAL_ALLOWED,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_progress(self, generation: int, processed: int, selected: int) -> None:
        if generation == self._generation:
            self.status.value = f"AI 校對中：{processed:,} / {selected:,} 筆"
            self._safe_update()

    def _apply_run(self, generation, path, priority, result, error) -> None:
        if generation != self._generation:
            self._set_busy(False, "執行結果已過期。")
            return
        self._set_busy(False, "")
        if error:
            self.summary.value = f"執行失敗：{error}"
            self.candidates.controls = []
        else:
            self._run_id = result.run_id
            self.summary.value = (
                f"完成 {result.processed_count:,} 筆：有效 {result.valid_count:,}、"
                f"建議 {result.suggestion_count:,}、不變 {result.no_change_count:,}、"
                f"需補 context {result.needs_context_count:,}、無效 {result.invalid_count:,}"
            )
            self._load_candidates(path, priority, result.run_id)
            if self.on_complete:
                self.on_complete(result)
        self._safe_update()

    def _load_candidates(
        self, path: Path, priority: tuple[int, ...], run_id: str
    ) -> None:
        try:
            rows = list_moddb_smart_review_candidates_from_path(path, priority, run_id)
        except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
            self.candidates.controls = [ft.Text(f"候選載入失敗：{exc}", color=C.RED)]
            return
        self.candidates.controls = [
            self._candidate_card(path, priority, row) for row in rows
        ]
        if not rows:
            self.candidates.controls = [kit.hint_text("此 run 沒有候選結果。")]

    def _candidate_card(
        self, path: Path, priority: tuple[int, ...], row: SmartReviewCandidate
    ):
        suggestion = row.suggested_zh_tw or "（無修改建議）"
        apply_btn = kit.button(
            "套用這筆",
            "primary",
            size="sm",
            on_click=lambda _e, ordinal=row.ordinal: self._apply_candidate(
                path, priority, row.run_id, ordinal
            ),
            disabled=row.status != "ready",
        )
        reject_btn = kit.button(
            "保留原譯文",
            "secondary",
            size="sm",
            on_click=lambda _e, ordinal=row.ordinal: self._reject_candidate(
                path, priority, row.run_id, ordinal
            ),
            disabled=row.status != "ready",
        )
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                f"#{row.ordinal} {row.mod_id}:{row.key}",
                                weight=ft.FontWeight.W_600,
                                expand=True,
                            ),
                            ft.Text(row.status, size=12, color=C.MUTED),
                            reject_btn,
                            apply_btn,
                        ],
                        spacing=8,
                    ),
                    ft.Text(f"原文：{row.en_us}", size=12, selectable=True),
                    ft.Text(f"目前：{row.current_zh_tw}", size=12, selectable=True),
                    ft.Text(f"建議：{suggestion}", size=12, selectable=True),
                ],
                spacing=5,
            ),
            padding=10,
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=8,
        )

    def _apply_candidate(
        self, path: Path, priority: tuple[int, ...], run_id: str, ordinal: int
    ) -> None:
        if self._busy:
            return
        self._set_busy(True, f"套用候選 #{ordinal}…")

        def work() -> None:
            try:
                status = apply_moddb_smart_review_candidate_from_path(
                    path, priority, run_id, ordinal
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(
                    self._apply_candidate_result, path, priority, run_id, None, str(exc)
                )
                return
            self._post(
                self._apply_candidate_result, path, priority, run_id, status, None
            )

        self._launch(
            work,
            "套用智慧校對候選",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_candidate_result(self, path, priority, run_id, status, error) -> None:
        self._set_busy(False, "")
        if error:
            show_snack(self.page, f"套用失敗：{error}", C.RED)
        elif status == "applied":
            show_snack(self.page, "已套用 1 筆人工未審核譯文。", C.EM)
            self._load_candidates(path, priority, run_id)
        elif status == "stale":
            show_snack(
                self.page, "候選已過期，資料庫內容已變更；未覆蓋目前譯文。", C.GOLD
            )
            self._load_candidates(path, priority, run_id)
        else:
            show_snack(self.page, f"此候選目前無法套用：{status}", C.GOLD)
        self._safe_update()

    def _reject_candidate(
        self, path: Path, priority: tuple[int, ...], run_id: str, ordinal: int
    ) -> None:
        if self._busy:
            return
        self._set_busy(True, f"保留候選 #{ordinal} 的原譯文…")

        def work() -> None:
            try:
                status = reject_moddb_smart_review_candidate_from_path(
                    path, priority, run_id, ordinal
                )
            except Exception as exc:  # noqa: BLE001 - UI reports bounded failure
                self._post(
                    self._apply_reject_result, path, priority, run_id, None, str(exc)
                )
                return
            self._post(self._apply_reject_result, path, priority, run_id, status, None)

        self._launch(
            work,
            "保留智慧校對原譯文",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _apply_reject_result(self, path, priority, run_id, status, error) -> None:
        self._set_busy(False, "")
        if error:
            show_snack(self.page, f"保留失敗：{error}", C.RED)
        elif status == "rejected":
            show_snack(self.page, "已保留原譯文，候選不會套用。", C.EM)
            self._load_candidates(path, priority, run_id)
        else:
            show_snack(self.page, f"此候選目前無法保留：{status}", C.GOLD)
        self._safe_update()

    def _launch(
        self,
        work,
        name: str,
        commit: CommitPolicy,
        *,
        durability: DurabilityPolicy = DurabilityPolicy.RECOMPUTABLE,
    ) -> None:
        try:
            launched = launch_page_operation(
                self.page,
                work,
                name=name,
                owner="moddb-smart-review",
                commit=commit,
                durability=durability,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except (AttributeError, RuntimeError) as exc:
            self._set_busy(False, f"操作未啟動：{exc}")
            return
        if not launched:
            self._set_busy(False, "應用程式正在關閉，未啟動操作。")

    def _post(self, callback, *args) -> None:
        run_task = getattr(self.page, "run_task", None)
        if callable(run_task):

            async def runner() -> None:
                callback(*args)

            run_task(runner)
        else:
            callback(*args)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        for control in (
            self.version_dd,
            self.model_dd,
            self.limit_field,
            self.batch_field,
            self.preview_btn,
            self.run_btn,
        ):
            control.disabled = busy
        self.status.value = status
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except RuntimeError:
            pass


__all__ = ["SmartReviewDialog"]
