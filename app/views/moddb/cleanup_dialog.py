"""資料清理視窗：匯出檢查檔 → 自行檢查 → 依檢查檔預覽並刪除 → 需要時整批還原。

清理對象是目前規則判定「不是要翻譯的內容」的舊條目。一般檢查檔只列沒有任何譯文或歷史的；
已經被翻譯或審核過的另外用「匯出已有譯文的項目」匯出，刪除時連同譯文與歷史一起移除。
數量可能上千筆，所以不在畫面上逐筆檢查，而是匯出 JSON 檢查檔；刪除前會再依目前資料重新檢查。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
from app.ui.snack import show_snack
from app.views.moddb.formatting import format_count, kind_label
from translation_tool.utils.log_unit import log_exception, log_info, log_warning


def _source_line(sources: dict[str, int]) -> str:
    """依生效譯文來源分組的筆數，例如「譯文來源：人工 3、釘宮翻譯組 5」。"""
    parts = [f"{name} {format_count(n)}" for name, n in sources.items()]
    return "譯文來源：" + "、".join(parts) if parts else ""


def _category_lines(counts: dict[str, int]) -> str:
    return "\n".join(
        f"　{svc.CLEANUP_CATEGORIES.get(cid, cid)}：{format_count(n)} 筆"
        for cid, n in counts.items()
        if n
    )


class CleanupDialog:
    """匯出、預覽、刪除、還原四個步驟；所有資料庫工作都在背景執行。"""

    def __init__(self, page: ft.Page, get_db, on_complete):
        self.page = page
        self.get_db = get_db
        self.on_complete = on_complete
        self._busy = False
        self._plan = (
            None  # (檢查檔, 預覽結果, 預覽時的資料庫)；任何選項或資料庫變動都要作廢
        )
        self._batches = []
        self._build_controls()
        self._build_dialog()
        self._load_options()

    # ------------------------------------------------------------------ 版面
    def _build_controls(self) -> None:
        self.version_dd = kit.dropdown(
            label="版本", dense=True, width=180, on_select=self._on_version
        )
        self.export_btn = kit.button(
            "匯出檢查檔",
            "secondary",
            icon=ft.Icons.FILE_DOWNLOAD_OUTLINED,
            on_click=self._export,
        )
        self.export_translated_btn = kit.button(
            "匯出已有譯文的項目",
            "secondary",
            icon=ft.Icons.FILE_DOWNLOAD_OUTLINED,
            on_click=lambda _e: self._export(with_translation=True),
        )
        self.open_folder_btn = kit.button(
            "開啟檢查檔資料夾", "ghost", size="sm", on_click=self._open_folder
        )
        self.overview_title = ft.Text(
            "", size=13, weight=ft.FontWeight.BOLD, color=C.TEXT
        )
        self.overview_body = ft.Column(spacing=2, tight=True)
        self._overview_generation = 0
        self.export_summary = ft.Text(
            "", size=12.5, color=C.TEXT, selectable=True, visible=False
        )
        self.report_dd = kit.dropdown(
            label="檢查檔", dense=True, width=460, on_select=self._on_report
        )
        self.refresh_reports_btn = kit.button(
            "重新整理清單", "ghost", size="sm", on_click=self._load_reports
        )
        self.preview_btn = kit.button(
            "預覽刪除", "secondary", on_click=self._preview, disabled=True
        )
        self.plan_summary = ft.Text(
            "", size=12.5, color=C.TEXT, selectable=True, visible=False
        )
        self.apply_btn = kit.button(
            "確認刪除",
            "danger",
            icon=ft.Icons.DELETE_OUTLINE,
            on_click=self._apply,
            disabled=True,
        )
        self.revert_info = ft.Text("", size=12, color=C.MUTED)
        self.batch_dd = kit.dropdown(label="尚未還原的清理", dense=True, width=460)
        self.revert_btn = kit.button(
            "還原選取的清理", "secondary", on_click=self._revert, disabled=True
        )
        self.status = ft.Text("", size=12, color=C.MUTED, visible=False)
        self.progress = ft.ProgressBar(visible=False)
        self.close_btn = ft.TextButton("關閉", on_click=self._close)

    def _build_dialog(self) -> None:
        width, height = dialog_dimensions(self.page)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("資料清理"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "找出目前規則判定「不是要翻譯的內容」的舊條目。",
                            size=13,
                            color=C.TEXT,
                        ),
                        ft.Text(
                            "「匯出檢查檔」只列入沒有任何譯文、生效譯文與歷史紀錄的條目，"
                            "有人動過的不會碰。符合規則但已經被翻譯或審核過的條目，"
                            "用「匯出已有譯文的項目」另外匯出檢查（檔案內附目前的譯文與來源）；"
                            "刪除會連同所有來源的譯文與歷史一起移除，可以還原。",
                            size=12,
                            color=C.MUTED,
                        ),
                        self.version_dd,
                        ft.Container(
                            ft.Column(
                                [self.overview_title, self.overview_body],
                                spacing=6,
                                tight=True,
                            ),
                            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                            bgcolor=C.PANEL2,
                            border=ft.Border.all(1, C.LINE),
                            border_radius=design.RADIUS_CONTROL,
                        ),
                        ft.Text("步驟 1　匯出檢查檔", weight=ft.FontWeight.BOLD),
                        ft.Row(
                            [
                                self.export_btn,
                                self.export_translated_btn,
                                self.open_folder_btn,
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                        self.export_summary,
                        ft.Text(
                            "步驟 2　用編輯器開啟檢查檔，不想刪的項目整個刪掉，"
                            "或把 delete 改成 false",
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text("步驟 3　依檢查檔清理", weight=ft.FontWeight.BOLD),
                        ft.Row(
                            [self.report_dd, self.refresh_reports_btn],
                            spacing=10,
                            wrap=True,
                        ),
                        ft.Row([self.preview_btn], spacing=8),
                        self.plan_summary,
                        ft.Text("還原", weight=ft.FontWeight.BOLD),
                        ft.Row([self.batch_dd, self.revert_btn], spacing=10, wrap=True),
                        self.revert_info,
                        self.status,
                        self.progress,
                    ],
                    tight=True,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=min(width, 760),
                height=height,
                data=RESPONSIVE_DIALOG_MARKER,
            ),
            actions=[self.close_btn, self.apply_btn],
        )

    def open(self) -> None:
        try:
            present_page_dialog(self.page, self.dialog)
        except (AttributeError, RuntimeError):
            show_snack(self.page, "目前畫面無法開啟資料清理視窗。", C.GOLD)

    def _close(self, _e=None) -> None:
        if self._busy:
            show_snack(self.page, "資料庫處理中，請稍候。", C.GOLD)
            return
        close_page_dialog(self.page, self.dialog)

    # ------------------------------------------------------------------ 選項
    def _load_options(self) -> None:
        db = self.get_db()
        versions = db.versions() if db else []
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        if versions and self.version_dd.value not in versions:
            self.version_dd.value = versions[0]
        self._load_reports()
        self._load_batch_info()
        self._refresh_overview()

    def _on_version(self, _e=None) -> None:
        self.export_summary.visible = False
        self._reset_plan()
        self._refresh_overview()
        self._safe_update()

    def _on_report(self, _e=None) -> None:
        self._reset_plan()
        self._safe_update()

    def _refresh_overview(self) -> None:
        """依所選版本重新計算可清理的筆數（背景、只讀）；選項變動時舊結果作廢。"""
        db, version = self.get_db(), self.version_dd.value
        self._overview_generation += 1
        generation = self._overview_generation
        if db is None or not version:
            self.overview_title.value = "請先選擇版本"
            self.overview_body.controls = []
            self._safe_update()
            return
        self.overview_title.value = f"版本 {version}：計算中…"
        self.overview_body.controls = []
        self._safe_update()

        def work() -> None:
            result = error = None
            try:
                result = svc.cleanup_overview(db, version)
            except sqlite3.Error as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(f"資料清理總覽計算失敗：{version}")
            self._post(self._apply_overview, generation, version, result, error)

        self._launch(work, "Mod 資料庫清理總覽", CommitPolicy.PARTIAL_ALLOWED)

    def _apply_overview(self, generation: int, version: str, result, error) -> None:
        if generation != self._overview_generation:
            return  # 計算期間又換了版本
        if error is not None:
            self.overview_title.value = f"版本 {version}：無法計算（{error}）"
            self.overview_body.controls = []
            self._safe_update()
            return
        counts, protected = result
        total = sum(counts.values())
        self.overview_title.value = (
            f"版本 {version}：可清理 {format_count(total)} 筆"
            if total
            else f"版本 {version}：沒有可清理的條目"
        )
        lines = [
            ft.Text(
                f"{kind_label(kind)}・{svc.CLEANUP_CATEGORIES.get(category, category)}"
                f"　{format_count(n)} 筆",
                size=12.5,
                color=C.TEXT,
            )
            for (kind, category), n in sorted(counts.items(), key=lambda item: -item[1])
        ]
        if protected:
            lines.append(
                ft.Text(
                    f"另有 {format_count(protected)} 筆符合條件但已有譯文或歷史，"
                    "不會列入一般檢查檔；可用「匯出已有譯文的項目」另外清理",
                    size=12,
                    color=C.MUTED,
                )
            )
        self.overview_body.controls = lines
        self._safe_update()

    def _load_reports(self, _e=None) -> None:
        db = self.get_db()
        reports = svc.list_cleanup_reports(db) if db else []
        kit.set_dropdown_options(self.report_dd, [(str(p), p.name) for p in reports])
        keys = {str(p) for p in reports}
        if self.report_dd.value not in keys:
            self.report_dd.value = str(reports[0]) if reports else None
        self._reset_plan()
        self.preview_btn.disabled = self.report_dd.value is None
        self._safe_update()

    def _load_batch_info(self) -> None:
        """列出還沒完全還原的清理批次（新的在前）；部分還原的批次仍可再還原。"""
        db = self.get_db()
        batches = svc.cleanup_batches(db) if db else []
        self._batches = [b for b in batches if not b.complete]
        kit.set_dropdown_options(
            self.batch_dd, [(b.batch_id, self._batch_label(b)) for b in self._batches]
        )
        keys = {b.batch_id for b in self._batches}
        if self.batch_dd.value not in keys:
            self.batch_dd.value = self._batches[0].batch_id if self._batches else None
        self.revert_btn.disabled = not self._batches
        if not batches:
            self.revert_info.value = "還沒有清理紀錄"
        elif not self._batches:
            self.revert_info.value = "所有清理都已還原"
        else:
            self.revert_info.value = ""

    @staticmethod
    def _batch_label(info) -> str:
        state = (
            f"，已還原 {format_count(info.restored)} 筆、"
            f"待還原 {format_count(info.pending)} 筆"
            if info.restored
            else ""
        )
        return (
            f"{info.created_at}（UTC）　版本 {info.version}　"
            f"{format_count(info.count)} 筆{state}"
        )

    def _selected_batch(self):
        return next(
            (b for b in self._batches if b.batch_id == self.batch_dd.value), None
        )

    def _reset_plan(self) -> None:
        self._plan = None
        self.plan_summary.visible = False
        self.apply_btn.disabled = True
        self.apply_btn.content = "確認刪除"

    # ------------------------------------------------------------------ 匯出
    def _export(self, _e=None, *, with_translation: bool = False) -> None:
        db, version = self.get_db(), self.version_dd.value
        if db is None or not version or self._busy:
            show_snack(self.page, "請先選擇版本。", C.GOLD)
            return
        self._set_busy(True, "掃描條目並匯出檢查檔…")

        def work() -> None:
            report = error = None
            try:
                report = svc.export_cleanup(
                    db, version, with_translation=with_translation
                )
            except (OSError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(f"資料清理匯出失敗：{version}")
            self._post(self._after_export, report, error)

        self._launch(work, "Mod 資料庫清理檢查檔匯出", CommitPolicy.PARTIAL_ALLOWED)

    def _after_export(self, report, error) -> None:
        self._set_busy(False)
        if error is not None:
            show_snack(self.page, f"匯出失敗：{error}", C.RED)
            return
        total = report.exported_total
        lines = [f"版本 {report.version}：匯出 {format_count(total)} 筆"]
        if total:
            lines.append(_category_lines(report.counts))
        if report.sources:
            lines.append(_source_line(report.sources))
        if report.review_required:
            lines.append(
                f"預設刪除 {format_count(report.default_selected)} 筆；"
                f"{format_count(report.review_required)} 筆要人工確認"
                "（可能是被誤判的文字，delete 預設為 false，確認要刪再改成 true）"
            )
        skipped = sum(report.protected.values())
        if skipped:
            lines.append(
                f"另有 {format_count(skipped)} 筆符合條件但已有譯文或歷史，不會列入"
            )
        lines.append(f"檢查檔：{report.path}")
        self.export_summary.value = "\n".join(lines)
        self.export_summary.visible = True
        log_info(f"Mod 資料庫清理檢查檔：{report.path}（{total} 筆）")
        self._load_reports()
        self._refresh_overview()
        if total:
            self.report_dd.value = str(report.path)
            self.preview_btn.disabled = False
        self._safe_update()

    def _open_folder(self, _e=None) -> None:
        db = self.get_db()
        if db is None:
            return
        if not svc.open_report_folder(db):
            show_snack(
                self.page,
                f"無法開啟資料夾：{svc.cleanup_report_dir(db)}",
                C.GOLD,
            )

    # ------------------------------------------------------------------ 預覽與刪除
    def _selected_report(self) -> Path | None:
        return Path(self.report_dd.value) if self.report_dd.value else None

    def _preview(self, _e=None) -> None:
        db, path = self.get_db(), self._selected_report()
        if db is None or path is None or self._busy:
            return
        self._reset_plan()
        self._set_busy(True, "讀取檢查檔並重新檢查…")

        def work() -> None:
            plan = error = None
            try:
                plan = svc.preview_cleanup(db, path)
            except (ValueError, OSError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(f"資料清理預覽失敗：{path}")
            self._post(self._after_preview, path, plan, error)

        self._launch(work, "Mod 資料庫清理預覽", CommitPolicy.PARTIAL_ALLOWED)

    def _after_preview(self, path: Path, plan, error) -> None:
        self._set_busy(False)
        if error is not None:
            self.plan_summary.value = f"無法預覽：{error}"
            self.plan_summary.visible = True
            self._safe_update()
            return
        if plan.version != self.version_dd.value:
            self._reset_plan()
            self.plan_summary.value = (
                f"這份檢查檔是版本 {plan.version} 的，和目前選的版本不同，"
                "請改選版本或檢查檔"
            )
            self.plan_summary.visible = True
            self._safe_update()
            return
        self._plan = (path, plan, self.get_db())
        lines = [
            (
                f"檢查檔標記要刪除 {format_count(plan.requested)} 筆；"
                f"現在實際可刪 {format_count(plan.deletable)} 筆"
            )
        ]
        if plan.deletable:
            lines.append(_category_lines(dict(plan.by_category)))
        if plan.with_translation:
            lines.append(
                f"其中 {format_count(plan.with_translation)} 筆已有譯文或歷史："
                "刪除會連同所有來源的譯文與歷史一起移除，可按「還原上次清理」復原"
            )
        lines.extend(
            f"略過 {format_count(n)} 筆：{reason}" for reason, n in plan.skipped.items()
        )
        self.plan_summary.value = "\n".join(lines)
        self.plan_summary.visible = True
        self.apply_btn.disabled = plan.deletable == 0
        self.apply_btn.content = f"確認刪除 {format_count(plan.deletable)} 筆"
        self._safe_update()

    def _apply(self, _e=None) -> None:
        db = self.get_db()
        if db is None or self._plan is None or self._busy:
            return
        path, previewed, previewed_db = self._plan
        if (
            db is not previewed_db
            or previewed.version != self.version_dd.value
            or str(path) != self.report_dd.value
        ):
            self._reset_plan()
            self.plan_summary.value = "版本、檢查檔或資料庫已變動，請重新預覽"
            self.plan_summary.visible = True
            self._safe_update()
            return
        self._set_busy(True, "刪除中…")

        def work() -> None:
            plan = error = None
            try:
                plan = svc.apply_cleanup(db, path, previewed.token)
            except (ValueError, OSError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(f"資料清理刪除失敗：{path}")
            self._post(self._after_apply, plan, error)

        self._launch(
            work,
            "Mod 資料庫清理",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _after_apply(self, plan, error) -> None:
        self._set_busy(False)
        if error is not None:
            log_warning(f"Mod 資料庫清理失敗：{error!r}")
            self._reset_plan()  # 檢查檔或資料已不是預覽時的樣子：要重新預覽
            self.plan_summary.value = f"清理未執行：{error}"
            self.plan_summary.visible = True
            show_snack(self.page, f"清理未執行：{error}", C.RED)
            self._safe_update()
            return
        message = (
            f"已刪除 {format_count(plan.deletable)} 筆（可在「還原」選取這次清理復原）"
        )
        skipped = sum(plan.skipped.values())
        if skipped:
            message += f"；略過 {format_count(skipped)} 筆"
        log_info(f"Mod 資料庫清理：{plan.version}；{message}")
        self._reset_plan()
        self.plan_summary.value = message
        self.plan_summary.visible = True
        self._load_batch_info()
        if plan.batch_id:
            self.batch_dd.value = plan.batch_id  # 預設還原剛做的這一次
        self._refresh_overview()
        show_snack(self.page, message, C.EM, text_color=C.ON_EM)
        if self.on_complete:
            self.on_complete()
        self._safe_update()

    # ------------------------------------------------------------------ 還原
    def _revert(self, _e=None) -> None:
        db, info = self.get_db(), self._selected_batch()
        if db is None or info is None or self._busy:
            return
        self._set_busy(True, "還原中…")

        def work() -> None:
            result = error = None
            try:
                result = svc.revert_cleanup(db, info.batch_id)
            except (ValueError, sqlite3.Error) as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - worker boundary reports unexpected failures
                error = exc
                log_exception(f"資料清理還原失敗：{info.batch_id}")
            self._post(self._after_revert, result, error)

        self._launch(
            work,
            "Mod 資料庫清理還原",
            CommitPolicy.ATOMIC,
            durability=DurabilityPolicy.USER_ACTION,
        )

    def _after_revert(self, result, error) -> None:
        self._set_busy(False)
        if error is not None:
            show_snack(self.page, f"還原失敗：{error}", C.RED)
            return
        message = f"已還原 {format_count(result.restored)} 筆"
        if result.remapped:
            message += (
                f"（{format_count(result.remapped)} 筆原編號已被佔用，改用新編號）"
            )
        if not result.complete:
            message += (
                f"；還有 {format_count(result.pending)} 筆因已重新掃描出同一條目而"
                "保留為待還原，批次仍可再還原"
            )
        self._load_batch_info()
        self._refresh_overview()
        show_snack(self.page, message, C.EM, text_color=C.ON_EM)
        if self.on_complete:
            self.on_complete()
        self._safe_update()

    # ------------------------------------------------------------------ 共用
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
                owner="moddb-cleanup",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                commit=commit,
                durability=durability,
                shutdown=ShutdownPolicy.ALLOW_TO_FINISH,
            )
        except Exception as exc:  # noqa: BLE001 - restore the dialog if the worker cannot launch
            self._set_busy(False)
            log_exception(f"{name}無法啟動")
            show_snack(self.page, f"{name}無法啟動：{exc}", C.RED)
            return
        if not launched:
            self._set_busy(False)
            show_snack(self.page, "應用程式正在關閉，未啟動操作。", C.GOLD)

    def _post(self, callback, *args) -> None:
        """把背景結果排回畫面執行緒（沒有 ``run_task`` 的測試頁面直接呼叫）。"""
        run_task = getattr(self.page, "run_task", None)
        try:
            if callable(run_task):

                async def runner() -> None:
                    callback(*args)

                run_task(runner)
            else:
                callback(*args)
        except Exception as exc:  # noqa: BLE001 - a disposed page may reject the callback
            log_warning(f"Mod 資料庫清理結果無法排回畫面：{exc!r}")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        for control in (
            self.version_dd,
            self.export_btn,
            self.export_translated_btn,
            self.report_dd,
            self.batch_dd,
            self.refresh_reports_btn,
            self.close_btn,
        ):
            control.disabled = busy
        self.status.visible = busy
        self.status.value = text
        self.progress.visible = busy
        self.progress.value = None if busy else 0
        if busy:
            self.preview_btn.disabled = True
            self.apply_btn.disabled = True
            self.revert_btn.disabled = True
        else:
            self.preview_btn.disabled = self.report_dd.value is None
            self.apply_btn.disabled = self._plan is None or self._plan[1].deletable == 0
            self.revert_btn.disabled = not self._batches
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except RuntimeError:
            pass  # 視窗已關閉
