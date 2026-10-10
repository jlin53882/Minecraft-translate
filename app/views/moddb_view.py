"""app/views/moddb_view.py：Mod 資料庫頁（總覽／條目校對／掃描匯入／批次機翻）。

資料庫是分版本的翻譯記憶庫：掃描 jar 建立、翻譯結果自動寫回、可在這裡手動校對。
頁面本身只負責切換頁籤與持有資料庫連線；各頁籤在 ``app/views/moddb/``。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import (
    TranslationDB,
    current_settings,
    database_problem,
    open_database,
)
from app.tasks.operation_registry import (
    CancellationPolicy,
    OperationPresentation,
    ShutdownPolicy,
    launch_page_operation,
)
from app.ui import kit
from app.views.moddb.entries_panel import EntriesPanel
from app.views.moddb.overview_panel import OverviewPanel
from app.views.moddb.panel_refresh import (
    load_entries_filter_snapshot,
    load_panel_snapshot,
)
from app.views.moddb.scan_panel import ScanPanel
from app.views.moddb.translate_panel import TranslatePanel
from translation_tool.utils.log_unit import log_debug

TABS = (
    ("overview", "總覽"),
    ("entries", "條目校對"),
    ("scan", "掃描匯入"),
    ("translate", "批次機翻"),
)
PANEL_REFRESH_MESSAGES = {
    "entries": "背景更新條目資料中…",
    "scan": "背景更新版本清單中…",
    "translate": "背景更新機翻範圍中…",
}


def _load_overview_snapshot(
    settings,
) -> tuple[TranslationDB | None, dict, Exception | None]:
    """Open the configured database and collect one complete overview snapshot."""
    worker_db: TranslationDB | None = None
    snapshot: dict = {
        "stats": [],
        "overview": {},
        "source_stats": [],
        "missing": [],
        "scans": [],
    }
    try:
        worker_db = open_database(create=False, settings=settings)
        if worker_db is None:
            problem = database_problem(settings)
            if problem:
                raise ValueError(problem)
        if worker_db is not None:
            snapshot["source_stats"] = worker_db.effective_source_stats_by_version()
            stats = worker_db.version_stats_from_effective_sources(
                snapshot["source_stats"]
            )
            snapshot["stats"] = stats
            if stats:
                snapshot["overview"] = worker_db.overview()
                snapshot["missing"] = worker_db.missing_by_mod(
                    stats[0].mc_version, limit=10
                )
                snapshot["scans"] = worker_db.last_scans(5)
    except Exception as exc:  # noqa: BLE001 - 顯示載入失敗並恢復頁面
        log_debug(f"Mod DB 總覽載入失敗：{exc!r}")
        if worker_db is not None:
            worker_db.close()
        return None, snapshot, exc
    return worker_db, snapshot, None


class ModDbView(ft.Column):
    """Mod 翻譯資料庫頁。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=16)
        self._page = page
        self.file_picker = file_picker
        self._db: TranslationDB | None = None
        self._db_loaded = False
        self._db_sig: tuple | None = None
        self._overview_loading = False
        self._overview_load_generation = 0
        self._overview_needs_refresh = True
        self._data_revision = 0
        self._panel_refresh_loading: set[str] = set()
        self._panel_refresh_generation = {key: 0 for key, _label in TABS}
        self._panel_refresh_needed = {
            key: True for key, _label in TABS if key != "overview"
        }
        self._pending_panel_results = {}
        self.tab = "overview"

        self.overview = OverviewPanel(
            page,
            self.get_db,
            request_refresh=self._request_overview_refresh,
            open_scan=lambda: self.show_tab("scan"),
            open_entries=self.open_entries,
        )
        self.entries = EntriesPanel(
            page,
            self.get_db,
            on_changed=self._on_entries_changed,
            on_filter_changed=self._on_entries_filter_changed,
        )
        self.scan = ScanPanel(
            page,
            file_picker,
            self.get_db,
            on_finished=self._on_scan_finished,
            defer_initial_refresh=True,
        )
        self.translate = TranslatePanel(
            page,
            self.get_db,
            on_finished=self._on_scan_finished,
            on_view_flagged=self.open_flagged,
        )
        self._panels = {
            "overview": self.overview,
            "entries": self.entries,
            "scan": self.scan,
            "translate": self.translate,
        }
        # Keep each visited panel attached to the page. Replacing ``body.content``
        # on every click makes Flet detach and resend the full panel tree, including
        # the entries list. Mount tabs on first visit, then switch with visibility
        # updates so returning to a tab does not rebuild its frontend controls.
        self._panel_hosts = {
            key: ft.Container(content=panel, expand=True, visible=key == "overview")
            for key, panel in self._panels.items()
        }
        self._mounted_tabs = {"overview"}
        self._tab_stack = ft.Stack(
            controls=[self._panel_hosts["overview"]],
            expand=True,
            fit=ft.StackFit.EXPAND,
        )
        self.body = ft.Container(expand=True, content=self._tab_stack)
        self.tab_seg = kit.Segmented(list(TABS), "overview", self.show_tab)
        self.controls = [
            kit.page_header(
                "Mod 資料庫",
                "分版本的翻譯記憶庫：掃描 jar 建立、翻譯結果自動寫回、手動校對可同步原文相同的版本",
                icon=ft.Icons.STORAGE_OUTLINED,
                tone="dia",
                actions=[self.tab_seg],
            ),
            self.body,
        ]
        self.show_tab("overview", update=False)

    # ------------------------------------------------------------------ 資料庫
    def get_db(self) -> TranslationDB | None:
        """目前的資料庫（沒有檔案時回傳 None，掃描完成後會重新開啟）。"""
        if not self._db_loaded:
            # 首次總覽載入期間由背景工作擁有資料庫連線，避免切換其他頁籤時
            # 又在 UI 執行緒同步開啟同一個大型資料庫。
            if self._overview_loading:
                return None
            self._db_sig = self._settings_signature()
            self._db = open_database(create=False)
            self._db_loaded = True
        return self._db

    @staticmethod
    def _settings_signature() -> tuple:
        """資料庫路徑與來源優先序；兩者變了才需要重新開啟連線。"""
        settings = current_settings()
        return (str(settings.resolved_path()), settings.priority)

    def _request_overview_refresh(self) -> None:
        """在背景載入總覽需要的全部資料，避免同步統計卡住首次進入。"""
        if self._overview_loading:
            return

        data_revision = self._data_revision
        settings = current_settings()
        signature = (str(settings.resolved_path()), settings.priority)
        self._overview_load_generation += 1
        generation = self._overview_load_generation
        self._overview_loading = True
        self.overview.set_loading(preserve=True)
        if self.tab == "overview":
            self._safe_update()

        # Flet pages provide run_thread (and the full app also has an operation
        # registry). Minimal standalone pages have no scheduler, so load inline.
        if not self._has_background_scheduler():
            worker_db, snapshot, error = _load_overview_snapshot(settings)
            self._apply_overview_result(
                generation, signature, worker_db, snapshot, error, data_revision
            )
            return

        def load() -> None:
            worker_db, snapshot, error = _load_overview_snapshot(settings)

            async def apply_on_ui() -> None:
                self._apply_overview_result(
                    generation, signature, worker_db, snapshot, error, data_revision
                )

            run_task = getattr(self._page, "run_task", None)
            if callable(run_task):
                try:
                    run_task(apply_on_ui)
                except Exception as exc:  # noqa: BLE001 - page may detach during load
                    if worker_db is not None:
                        worker_db.close()
                    self._overview_loading = False
                    self._overview_needs_refresh = True
                    log_debug(f"Mod DB 總覽結果排程失敗：{exc!r}")
            else:
                self._apply_overview_result(
                    generation, signature, worker_db, snapshot, error, data_revision
                )

        try:
            launched = launch_page_operation(
                self._page,
                load,
                name="Mod DB 總覽載入",
                owner="moddb-overview-load",
                cancellation=CancellationPolicy.NON_CANCELLABLE,
                shutdown=ShutdownPolicy.DRAIN_ONLY,
                presentation=OperationPresentation.MAINTENANCE,
            )
        except Exception as exc:  # noqa: BLE001 - restore state on launch failure
            self._overview_loading = False
            self._overview_needs_refresh = True
            self.overview.show_error(f"無法啟動總覽載入：{exc}")
            if self.tab == "overview":
                self._safe_update()
            return
        if not launched:
            self._overview_loading = False
            self.overview.show_error("應用程式正在關閉，無法載入總覽")
            if self.tab == "overview":
                self._safe_update()

    def _has_background_scheduler(self) -> bool:
        can_run_worker = getattr(
            self._page, "operation_registry", None
        ) is not None or callable(getattr(self._page, "run_thread", None))
        return can_run_worker and callable(getattr(self._page, "run_task", None))

    def _apply_overview_result(
        self,
        generation: int,
        signature: tuple,
        worker_db: TranslationDB | None,
        snapshot: dict,
        error: Exception | None,
        data_revision: int,
    ) -> None:
        """Apply a worker snapshot and transfer or close its connection safely."""
        if generation != self._overview_load_generation:
            if worker_db is not None:
                worker_db.close()
            return

        self._overview_loading = False
        if signature != self._settings_signature():
            self._overview_needs_refresh = True
            if worker_db is not None:
                worker_db.close()
            self.reload_db()
            if self.tab == "overview":
                self._request_overview_refresh()
            return
        if data_revision != self._data_revision:
            self._overview_needs_refresh = True
            if worker_db is not None:
                worker_db.close()
            if self.tab == "overview":
                self._request_overview_refresh()
            return

        if error is not None:
            self._overview_needs_refresh = True
            if worker_db is not None:
                worker_db.close()
            if self._db_sig != signature:
                self.reload_db()
            self._db_loaded = True
            self._db_sig = signature
            self.overview.show_error(str(error))
        else:
            self._overview_needs_refresh = False
            self._adopt_overview_database(signature, worker_db)
            self.overview.apply_snapshot(
                self._db,
                stats=snapshot["stats"],
                overview=snapshot["overview"],
                source_stats=snapshot["source_stats"],
                missing=snapshot["missing"],
                scans=snapshot["scans"],
            )

        if self.tab != "overview":
            self._refresh_active_panel()
        self._safe_update()

    def _adopt_overview_database(
        self, signature: tuple, worker_db: TranslationDB | None
    ) -> None:
        """Keep one view-owned connection and close any duplicate worker connection."""
        if self._db is not None and self._db_sig != signature:
            self.reload_db()
        if self._db is None:
            self._db = worker_db
        elif worker_db is not None:
            worker_db.close()
        self._db_loaded = True
        self._db_sig = signature

    def reload_db(self) -> None:
        """關閉並重新開啟（資料庫路徑或優先序設定變更、掃描建立新檔後）。"""
        if self._db is not None:
            self._db.close()
        self._db, self._db_loaded = None, False

    # ------------------------------------------------------------------ 頁籤
    def show_tab(self, key: str, *, update: bool = True) -> None:
        previous = self.tab
        if previous != key:
            previous_panel = self._panels[previous]
            pause = getattr(previous_panel, "will_unmount", None)
            if callable(pause):
                pause()

        self.tab = key
        if self.tab_seg.value != key:
            self.tab_seg.select(key)
        if key not in self._mounted_tabs:
            self._tab_stack.controls.append(self._panel_hosts[key])
            self._mounted_tabs.add(key)
        for tab_key in self._mounted_tabs:
            self._panel_hosts[tab_key].visible = tab_key == key

        background = self._has_background_scheduler()
        # 面板會留在 stack 中；離開時已明確停止輪詢，切回來要接續追蹤，
        # 否則畫面會停在離開當下的進度，直到任務結束後也不會更新。
        if key == "overview":
            if self._overview_loading and update:
                self._safe_update()
            elif self._overview_needs_refresh and (update or not background):
                self._request_overview_refresh()
            elif update:
                self._safe_update()
        elif key in self._panel_refresh_needed:
            self._activate_panel(key, background=background, update=update)
            if key == "scan":
                self.scan.resume()
            elif key == "translate":
                self.translate.resume()

    def _activate_panel(self, key: str, *, background: bool, update: bool) -> None:
        pending = self._pending_panel_results.pop(key, None)
        if pending is not None:
            generation, signature, request, data_revision, snapshot = pending
            self._apply_panel_refresh(
                key,
                generation,
                signature,
                request,
                data_revision,
                self._panels[key],
                snapshot,
            )
            return
        if not self._panel_refresh_needed[key]:
            if update:
                self._safe_update()
            return
        if background:
            if update:
                self._request_panel_refresh(key)
            return
        if key == "entries":
            self.entries.refresh()
            self._panel_refresh_needed[key] = bool(self.entries._list_error)
        elif key == "scan":
            self.scan.refresh_versions()
            self._panel_refresh_needed[key] = False
        else:
            self.translate.refresh_scope()
            self._panel_refresh_needed[key] = False
        if update:
            self._safe_update()

    def _request_panel_refresh(self, key: str, *, message: str | None = None) -> None:
        """Load the selected tab's database data off the UI thread."""
        if key in self._panel_refresh_loading:
            if self.tab == key and message is None:
                self._safe_update()
            return
        self._pending_panel_results.pop(key, None)
        panel = self._panels[key]
        try:
            request = self._panel_refresh_request(key)
        except ValueError as exc:
            panel.refresh_indicator.value = str(exc)
            panel.refresh_indicator.visible = True
            if self.tab == key:
                self._safe_update()
            return
        settings = current_settings()
        signature = (str(settings.resolved_path()), settings.priority)
        data_revision = self._data_revision
        self._panel_refresh_generation[key] += 1
        generation = self._panel_refresh_generation[key]
        self._panel_refresh_loading.add(key)
        panel.refresh_indicator.value = message or PANEL_REFRESH_MESSAGES[key]
        panel.refresh_indicator.visible = True
        if self.tab == key:
            if message is not None and key == "entries":
                try:
                    panel.refresh_indicator.update()
                except RuntimeError:
                    self._safe_update()
            else:
                self._safe_update()

        try:
            launched = self._launch_panel_refresh(
                key, panel, request, settings, signature, data_revision, generation
            )
        except Exception as exc:  # noqa: BLE001 - restore state if admission fails
            launched = False
            panel.refresh_indicator.visible = True
            panel.refresh_indicator.value = f"無法啟動資料載入：{exc}"
            log_debug(f"Mod DB {key} 頁籤資料載入無法啟動：{exc!r}")
        if launched is False:
            self._panel_refresh_loading.discard(key)
            self._panel_refresh_needed[key] = True
            panel.refresh_indicator.visible = False
            panel.refresh_indicator.value = "應用程式正在關閉，無法載入資料。"
            if self.tab == key:
                self._safe_update()

    def _launch_panel_refresh(
        self, key, panel, request, settings, signature, data_revision, generation
    ):
        """Run database reads in a worker, then apply their snapshot on the UI loop."""

        def load() -> None:
            try:
                if key == "entries" and request.get("filter_only"):
                    snapshot = load_entries_filter_snapshot(settings, request)
                else:
                    snapshot = load_panel_snapshot(key, request, settings)
            except Exception as exc:  # noqa: BLE001 - 顯示載入錯誤並恢復頁籤
                log_debug(f"Mod DB {key} 頁籤資料載入失敗：{exc!r}")
                snapshot = {
                    "key": key,
                    "identity": request.get("identity"),
                    "source_catalog": request.get("source_catalog"),
                    "filter_only": bool(request.get("filter_only")),
                    "error": str(exc),
                }

            async def apply_on_ui() -> None:
                self._apply_panel_refresh(
                    key,
                    generation,
                    signature,
                    request,
                    data_revision,
                    panel,
                    snapshot,
                )

            run_task = getattr(self._page, "run_task", None)
            if callable(run_task):
                try:
                    run_task(apply_on_ui)
                except Exception as exc:  # noqa: BLE001 - page may detach during load
                    self._panel_refresh_loading.discard(key)
                    self._panel_refresh_needed[key] = True
                    log_debug(f"Mod DB {key} 頁籤結果排程失敗：{exc!r}")
            else:
                self._apply_panel_refresh(
                    key,
                    generation,
                    signature,
                    request,
                    data_revision,
                    panel,
                    snapshot,
                )

        return launch_page_operation(
            self._page,
            load,
            name=f"Mod DB {dict(TABS)[key]}資料載入",
            owner=f"moddb-{key}-panel-refresh",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            presentation=OperationPresentation.MAINTENANCE,
        )

    def _apply_panel_refresh(
        self,
        key: str,
        generation: int,
        signature: tuple,
        request: dict,
        data_revision: int,
        panel,
        snapshot: dict,
    ) -> None:
        """Fallback for lightweight pages without a UI task scheduler."""
        if generation != self._panel_refresh_generation[key]:
            return
        self._panel_refresh_loading.discard(key)
        if signature != self._settings_signature():
            self._panel_refresh_needed[key] = True
            panel.refresh_indicator.visible = False
            panel.refresh_indicator.value = ""
            if self.tab == key:
                self._request_panel_refresh(key)
            return
        if data_revision != self._data_revision:
            self._panel_refresh_needed[key] = True
            if self.tab == key:
                self._request_panel_refresh(key)
            return
        if key != "scan":
            try:
                current_request = self._panel_refresh_request(key)
            except ValueError as exc:
                self._panel_refresh_needed[key] = True
                panel.refresh_indicator.value = str(exc)
                panel.refresh_indicator.visible = True
                if self.tab == key:
                    self._safe_update()
                return
            if current_request != request:
                if not self._panel_refresh_needed[key]:
                    return
                self._panel_refresh_needed[key] = True
                panel.refresh_indicator.visible = False
                panel.refresh_indicator.value = ""
                if self.tab == key:
                    self._request_panel_refresh(key)
                return
        if (
            key == "entries"
            and snapshot.get("filter_only")
            and not (self._validate_filter_snapshot(request, snapshot, panel))
        ):
            return
        if self.tab != key:
            self._pending_panel_results[key] = (
                generation,
                signature,
                request,
                data_revision,
                snapshot,
            )
            self._panel_refresh_needed[key] = False
            return
        panel.apply_refresh_snapshot(snapshot)
        self._panel_refresh_needed[key] = bool(snapshot.get("error"))
        if self.tab == key:
            self._update_panel(key)

    def _validate_filter_snapshot(self, request: dict, snapshot: dict, panel) -> bool:
        """Reject filter results whose database identity or generation is stale."""
        expected_identity = request.get("identity")
        if (
            expected_identity is not None
            and snapshot.get("identity") != expected_identity
        ):
            self._panel_refresh_needed["entries"] = True
            self.entries._filter_only_refresh = False
            self.entries._db_identity = None
            self.reload_db()
            panel.refresh_indicator.visible = False
            panel.refresh_indicator.value = ""
            if self.tab == "entries":
                self._request_panel_refresh("entries")
            return False
        if (
            self._db is not None
            and snapshot.get("db_generation") is not None
            and self._db._data_gen() != snapshot["db_generation"]
        ):
            self._panel_refresh_needed["entries"] = True
            panel.refresh_indicator.visible = False
            panel.refresh_indicator.value = ""
            if self.tab == "entries":
                self._request_panel_refresh("entries")
            return False
        return True

    def _panel_refresh_request(self, key: str) -> dict:
        if key == "entries":
            return {
                "criteria": self.entries._entry_filter(),
                "version": self.entries.version,
                "mod_id": self.entries.mod_id,
                "kind": self.entries.kind,
                "page": self.entries.pager.current_page,
                "filter_only": self.entries._filter_only_refresh,
                "identity": self.entries._db_identity,
                "source_catalog": self.entries._render_source_catalog,
                "selected_id": self.entries.selected.id
                if self.entries.selected is not None
                and self.entries._refresh_keep_selection
                else None,
            }
        if key == "scan":
            return {
                "version": self.scan.version(),
                "query": (self.scan.version_search.value or "").strip().lower(),
            }
        if key == "translate":
            return {
                "version": self.translate.version_dd.value,
                "mod": self.translate.mod_dd.value,
                "reuse": bool(self.translate.reuse_row.value),
            }
        raise ValueError(f"未知 Mod DB 頁籤：{key}")

    def open_entries(
        self, state: str, version: str | None = None, mod_id: str | None = None
    ) -> None:
        """從總覽跳到條目校對並套用篩選。"""
        self.entries.show_filter(state)
        if version:
            self.entries.version = version
        self.entries.mod_id = mod_id
        self._invalidate_panel("entries")
        self.show_tab("entries")

    def open_flagged(
        self, entry_ids: list[int], drafts: dict[int, str], version: str
    ) -> None:
        """從批次機翻跳到條目校對，只看「特殊字元不一致、沒寫入」的條目（AI 譯文預填）。"""
        self.entries.show_flagged(entry_ids, drafts, version)
        self._invalidate_panel("entries")
        self.show_tab("entries")

    def _on_scan_finished(self) -> None:
        # 連線看得到其他連線已提交的資料；只有「原本沒有資料庫檔案」才需要重新開啟
        if self._db is None:
            self.reload_db()
        self._invalidate_database_snapshots()
        self._refresh_active_panel()

    def _on_entries_changed(self) -> None:
        self._invalidate_database_snapshots()
        # EntriesPanel has already refreshed the row and editor that were just saved.
        self._panel_refresh_needed["entries"] = False
        if self.tab == "overview":
            self._request_overview_refresh()
        elif self.tab != "entries":
            self._refresh_active_panel()

    def _on_entries_filter_changed(
        self,
        page: int = 1,
        keep_selection: bool = True,
        full_refresh: bool = False,
        *,
        background: bool = True,
    ) -> None:
        """Refresh changed filters in a worker and page navigation inline."""
        self._invalidate_panel("entries")
        self.entries._refresh_keep_selection = keep_selection
        self.entries._filter_only_refresh = background and not full_refresh
        if self.tab != "entries":
            return
        if background and self._has_background_scheduler():
            self._request_panel_refresh("entries", message="正在篩選條目…")
            return
        self.entries._load_list(page=page, keep_selection=keep_selection)
        self._panel_refresh_needed["entries"] = bool(self.entries._list_error)
        self.entries.refresh_indicator.visible = False
        self.entries.refresh_indicator.value = ""
        self._safe_update()

    def _invalidate_database_snapshots(self) -> None:
        self._data_revision += 1
        self._overview_needs_refresh = True
        self.entries._filter_only_refresh = False
        for key in self._panel_refresh_needed:
            self._panel_refresh_needed[key] = True
        self._pending_panel_results.clear()

    def _invalidate_panel(self, key: str) -> None:
        self._panel_refresh_needed[key] = True
        self._pending_panel_results.pop(key, None)
        if key == "entries":
            self.entries._filter_only_refresh = False

    def _refresh_active_panel(self) -> None:
        """資料庫背景載入期間若切換了頁籤，連線就緒後刷新目前頁面。"""
        if self.tab == "overview":
            self._request_overview_refresh()
        else:
            self._activate_panel(
                self.tab,
                background=self._has_background_scheduler(),
                update=True,
            )

    # ------------------------------------------------------------------ 生命週期
    def will_unmount(self) -> None:
        self._overview_load_generation += 1
        self._overview_loading = False
        for key in self._panel_refresh_generation:
            self._panel_refresh_generation[key] += 1
        self._panel_refresh_loading.clear()
        self._pending_panel_results.clear()
        self.scan.will_unmount()
        self.translate.will_unmount()

    def did_mount(self) -> None:
        # 從別的頁（例如機器翻譯寫回新資料）切回來時：連線本來就看得到新資料，
        # 不必重開（重開會丟掉 SQLite 的頁面快取，數十萬筆時每次都是冷啟動）；
        # 只有資料庫路徑／優先序設定變了、或原本沒有資料庫檔案才重新開啟
        if self._db is None or self._db_sig != self._settings_signature():
            self.reload_db()
        self._invalidate_database_snapshots()
        # 多個已開啟的面板會留在 stack，但只接續目前頁籤的輪詢；隱藏頁面不更新畫面。
        self.show_tab(self.tab)

    def _safe_update(self) -> None:
        try:
            # Updating this subtree keeps unrelated app controls out of each tab
            # switch diff. Before mount (and in small test pages), fall back to the
            # page update path.
            self._tab_stack.update()
        except RuntimeError:
            try:
                self._page.update()
            except Exception as exc:  # noqa: BLE001 - page may already be detached
                log_debug(f"ModDbView update 略過：{exc}")
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料操作
            log_debug(f"ModDbView update 略過：{exc}")

    def _update_panel(self, key: str) -> None:
        """Apply one panel snapshot without diffing every mounted sibling tab."""
        try:
            self._panels[key].update()
        except RuntimeError:
            self._safe_update()

    @property
    def page(self):
        return self._page
