"""app/views/moddb_view.py：Mod 資料庫頁（總覽／條目校對／掃描匯入／批次機翻）。

資料庫是分版本的翻譯記憶庫：掃描 jar 建立、翻譯結果自動寫回、可在這裡手動校對。
頁面本身只負責切換頁籤與持有資料庫連線；各頁籤在 ``app/views/moddb/``。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import (
    TranslationDB,
    current_settings,
    database_data_generation,
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
from app.ui.page_post import has_background_scheduler
from app.ui.update_scope import isolate, update_regions
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
        # 更新邊界：頁面層級的 page.update()（狀態列、Key 輪詢…）不會走進這棵樹；
        # 這一頁的變更一律由本頁與各頁籤自己更新（見 _safe_update／_update_panel）。
        isolate(self)
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
            on_database_changed=self._on_database_changed,
        )
        self.entries = EntriesPanel(
            page,
            self.get_db,
            on_changed=self._on_entries_changed,
            on_filter_changed=self._on_entries_filter_changed,
            on_flagged_resolved=lambda entry_id: self.translate.resolve_flagged(
                entry_id
            ),
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
            request_refresh=lambda: self._request_panel_refresh("translate"),
        )
        self._panels = {
            "overview": self.overview,
            "entries": self.entries,
            "scan": self.scan,
            "translate": self.translate,
        }
        for panel in self._panels.values():
            isolate(panel)
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
            # 總覽的重統計仍在背景連線執行；開啟一個 view-owned 連線只做
            # schema/priority 初始化，讓使用者在總覽尚未完成時仍能操作條目頁。
            # _adopt_overview_database() 會在背景快照完成後關閉重複的 worker 連線。
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
        return has_background_scheduler(self._page)

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
            keep_scroll = self.entries._refresh_keep_scroll
            self.entries.refresh(keep_scroll=keep_scroll)
            self.entries._refresh_keep_scroll = False
            self._panel_refresh_needed[key] = bool(self.entries._list_error)
        elif key == "scan":
            self.scan.refresh_versions()
            self._panel_refresh_needed[key] = False
        else:
            self.translate.refresh_scope()
            self._panel_refresh_needed[key] = False
        if update:
            self._safe_update()

    @staticmethod
    def _hide_indicator(panel) -> None:
        """隱藏頁籤的背景載入提示，並讓清單恢復正常亮度。"""
        panel.refresh_indicator.visible = False
        panel.refresh_indicator.value = ""
        clear = getattr(panel, "clear_loading", None)
        if callable(clear):
            clear()

    def _request_panel_refresh(self, key: str, *, message: str | None = None) -> None:
        """Load the selected tab's database data off the UI thread."""
        if key in self._panel_refresh_loading:
            self._panel_refresh_needed[key] = True
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
        if key == "entries":
            self.entries.set_data_loading(True)
        if self.tab == key:
            if message is not None and key == "entries":
                panel.show_loading()
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
            if key == "entries":
                self.entries.set_data_loading(False)
            log_debug(f"Mod DB {key} 頁籤資料載入無法啟動：{exc!r}")
        if launched is False:
            self._panel_refresh_loading.discard(key)
            self._panel_refresh_needed[key] = True
            panel.refresh_indicator.visible = False
            panel.refresh_indicator.value = "應用程式正在關閉，無法載入資料。"
            if key == "entries":
                self.entries.set_data_loading(False)
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
                if key == "entries":
                    self._mark_snapshot_generation(snapshot)
            except Exception as exc:  # noqa: BLE001 - 顯示載入錯誤並恢復頁籤
                log_debug(f"Mod DB {key} 頁籤資料載入失敗：{exc!r}")
                snapshot = {
                    "key": key,
                    "identity": request.get("identity"),
                    "source_catalog": request.get("source_catalog"),
                    "filter_only": bool(request.get("filter_only")),
                    "keep_scroll": bool(request.get("keep_scroll")),
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
                    if key == "entries":
                        self.entries.set_data_loading(False)
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
        if key == "entries":
            self.entries.set_data_loading(False)
        if signature != self._settings_signature():
            self._panel_refresh_needed[key] = True
            self._hide_indicator(panel)
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
                self._hide_indicator(panel)
                if self.tab == key:
                    self._request_panel_refresh(key)
                return
        if key == "entries" and not self._validate_entries_snapshot(
            request, snapshot, panel
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

    def _validate_entries_snapshot(self, request: dict, snapshot: dict, panel) -> bool:
        """拒絕資料庫身分或資料代次過期的 Entries 快照。"""
        expected_identity = request.get("identity")
        snapshot_identity = snapshot.get("identity")
        current_identity = getattr(panel, "_db_identity", None)
        identity_stale = (
            expected_identity is not None and snapshot_identity != expected_identity
        ) or (
            current_identity is not None
            and snapshot_identity is not None
            and snapshot_identity != current_identity
        )
        if identity_stale:
            self._panel_refresh_needed["entries"] = True
            self.entries._filter_only_refresh = False
            self.entries._sync_database_identity(None)
            self.reload_db()
            self._hide_indicator(panel)
            if self.tab == "entries":
                self._request_panel_refresh("entries")
            return False
        if self._entries_snapshot_is_stale(snapshot):
            self._panel_refresh_needed["entries"] = True
            self._hide_indicator(panel)
            if self.tab == "entries":
                self._request_panel_refresh("entries")
            return False
        return True

    def _entries_snapshot_is_stale(self, snapshot: dict) -> bool:
        """快照讀完之後資料庫又被寫入（含其他程序）時視為過期。

        worker 讀完快照後已在背景比對過世代（``generation_checked``）；這裡不再於 UI 執行緒
        查詢共用連線，免得和正在寫入的 worker 搶同一把鎖而凍結畫面。沒有 worker 比對結果
        （同步 fallback、測試）才在這裡查。
        """
        generation = snapshot.get("db_generation")
        if self._db is None or generation is None:
            return False
        if snapshot.get("generation_checked"):
            return bool(snapshot.get("db_stale"))
        return database_data_generation(self._db) != generation

    def _mark_snapshot_generation(self, snapshot: dict) -> None:
        """在 worker 內比對快照世代與共用連線目前的世代（結果交給 UI 執行緒判斷）。"""
        generation = snapshot.get("db_generation")
        db = self._db
        if db is None or generation is None:
            return
        try:
            current = database_data_generation(db)
        except Exception as exc:  # noqa: BLE001 - 連線可能剛被關閉：視為過期，讓 UI 重新載入
            log_debug(f"Mod DB 快照世代比對失敗：{exc!r}")
            current = None
        snapshot["generation_checked"] = True
        snapshot["db_stale"] = current != generation

    def _panel_refresh_request(self, key: str) -> dict:
        if key == "entries":
            return {
                "criteria": self.entries._entry_filter(),
                "version": self.entries.version,
                "mod_id": self.entries.mod_id,
                "kind": self.entries.kind,
                "page": self.entries.pager.current_page,
                "filter_only": self.entries._filter_only_refresh,
                "page_only": self.entries._page_only_refresh,
                "keep_scroll": self.entries._refresh_keep_scroll,
                "fallback_index": self.entries._refresh_fallback_index,
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
                "limit": self.translate.limit(),
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

    def _on_database_changed(self) -> None:
        """資料庫寫入完成後，使所有 Mod DB snapshot 失效。"""
        self._invalidate_database_snapshots()
        if self.tab == "overview":
            self._request_overview_refresh()
        else:
            # 目前頁籤也必須刷新。尤其 EntriesPanel 可能保留篩選選項或 resident state，
            # 只刷新資料列無法涵蓋這些狀態；隱藏頁籤則保留待下次進入時刷新。
            self._refresh_active_panel()

    def _on_entries_changed(self) -> None:
        """EntriesPanel 資料變更的向後相容回呼。"""
        self._on_database_changed()

    def _on_entries_filter_changed(
        self,
        page: int = 1,
        keep_selection: bool = True,
        full_refresh: bool = False,
        *,
        background: bool = True,
        page_only: bool = False,
        keep_scroll: bool = False,
    ) -> None:
        """在 worker 重新載入條目結果頁；沒有背景排程的頁面（測試）同步載入。

        ``page_only``：只換頁，篩選條件與下拉選項都沒變，worker 不必重算選項。
        ``keep_scroll``：儲存／還原後留在原本的捲動位置。
        """
        self._invalidate_panel("entries")
        entries = self.entries
        entries._refresh_keep_selection = keep_selection
        entries._filter_only_refresh = background and not full_refresh
        entries._page_only_refresh = page_only and entries._filter_only_refresh
        entries._refresh_keep_scroll = keep_scroll
        if self.tab != "entries":
            return
        if background and self._has_background_scheduler():
            message = f"正在載入第 {page:,} 頁…" if page_only else "正在篩選條目…"
            self._request_panel_refresh("entries", message=message)
            return
        if not page_only and (full_refresh or page == 1):
            # 同步篩選也要更新模組／來源／類型選單（背景路徑由快照的 apply_filter_options 完成）。
            entries._refresh_filter_options()
        entries._load_list(
            page=page, keep_selection=keep_selection, keep_scroll=keep_scroll
        )
        self._panel_refresh_needed["entries"] = bool(entries._list_error)
        self._hide_indicator(entries)
        self._safe_update()

    def _invalidate_database_snapshots(self) -> None:
        self._data_revision += 1
        self._overview_needs_refresh = True
        self.entries._filter_only_refresh = False
        self.entries._page_only_refresh = False
        for key in self._panel_refresh_needed:
            self._panel_refresh_needed[key] = True
        self._pending_panel_results.clear()

    def _invalidate_panel(self, key: str) -> None:
        self._panel_refresh_needed[key] = True
        self._pending_panel_results.pop(key, None)
        if key == "entries":
            self.entries._filter_only_refresh = False
            self.entries._page_only_refresh = False

    def _refresh_active_panel(self) -> None:
        """資料庫更新後刷新目前頁籤（有背景排程時在 worker 讀取，結果以 ticket 丟棄過期者）。"""
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
        """更新分頁堆疊（頁籤顯示／隱藏）與目前的頁籤內容；其他頁籤與頁面外殼不受影響。"""
        update_regions(self._tab_stack)
        self._flush_panel(self.tab)

    def _flush_panel(self, key: str) -> None:
        """把一個頁籤的變更送到畫面（頁籤各區塊是更新邊界，要明確更新）。"""
        panel = self._panels[key]
        flush = getattr(panel, "flush", None)
        if callable(flush):
            flush()
        else:
            update_regions(panel)

    def _update_panel(self, key: str) -> None:
        """Apply one panel snapshot without diffing every mounted sibling tab."""
        self._flush_panel(key)

    @property
    def page(self):
        return self._page
