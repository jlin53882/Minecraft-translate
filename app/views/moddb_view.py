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
from app.views.moddb.scan_panel import ScanPanel
from app.views.moddb.translate_panel import TranslatePanel
from translation_tool.utils.log_unit import log_debug

TABS = (
    ("overview", "總覽"),
    ("entries", "條目校對"),
    ("scan", "掃描匯入"),
    ("translate", "批次機翻"),
)


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
            stats = worker_db.version_stats()
            snapshot["stats"] = stats
            if stats:
                snapshot["overview"] = worker_db.overview()
                snapshot["source_stats"] = worker_db.effective_source_stats_by_version()
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
        self.tab = "overview"

        self.overview = OverviewPanel(
            page,
            self.get_db,
            request_refresh=self._request_overview_refresh,
            open_scan=lambda: self.show_tab("scan"),
            open_entries=self.open_entries,
        )
        self.entries = EntriesPanel(page, self.get_db)
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
        self.body = ft.Container(expand=True)
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
                generation, signature, worker_db, snapshot, error
            )
            return

        def load() -> None:
            worker_db, snapshot, error = _load_overview_snapshot(settings)

            async def apply_on_ui() -> None:
                self._apply_overview_result(
                    generation, signature, worker_db, snapshot, error
                )

            run_task = getattr(self._page, "run_task", None)
            if callable(run_task):
                run_task(apply_on_ui)
            else:
                self._apply_overview_result(
                    generation, signature, worker_db, snapshot, error
                )

        launched = launch_page_operation(
            self._page,
            load,
            name="Mod DB 總覽載入",
            owner="moddb-overview-load",
            cancellation=CancellationPolicy.NON_CANCELLABLE,
            shutdown=ShutdownPolicy.DRAIN_ONLY,
            presentation=OperationPresentation.MAINTENANCE,
        )
        if not launched:
            self._overview_loading = False
            self.overview.show_error("應用程式正在關閉，無法載入總覽")
            if self.tab == "overview":
                self._safe_update()

    def _has_background_scheduler(self) -> bool:
        return getattr(self._page, "operation_registry", None) is not None or callable(
            getattr(self._page, "run_thread", None)
        )

    def _apply_overview_result(
        self,
        generation: int,
        signature: tuple,
        worker_db: TranslationDB | None,
        snapshot: dict,
        error: Exception | None,
    ) -> None:
        """Apply a worker snapshot and transfer or close its connection safely."""
        if generation != self._overview_load_generation:
            if worker_db is not None:
                worker_db.close()
            return

        self._overview_loading = False
        if signature != self._settings_signature():
            if worker_db is not None:
                worker_db.close()
            self.reload_db()
            if self.tab == "overview":
                self._request_overview_refresh()
            return

        if error is not None:
            if worker_db is not None:
                worker_db.close()
            if self._db_sig != signature:
                self.reload_db()
            self._db_loaded = True
            self._db_sig = signature
            self.overview.show_error(str(error))
        else:
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
        self.tab = key
        self.tab_seg.select(key)
        panel = self._panels[key]
        # 離開頁籤時面板被卸載（will_unmount 會停掉輪詢）；任務還在跑，切回來要接續輪詢，
        # 否則畫面停在離開當下的進度，直到任務結束後也不會更新
        if key == "overview":
            if update or not self._has_background_scheduler():
                self._request_overview_refresh()
        elif key == "entries":
            self.entries.refresh()
        elif key == "scan":
            self.scan.refresh_versions()
            self.scan.resume()
        elif key == "translate":
            self.translate.refresh_scope()
            self.translate.resume()
        self.body.content = panel
        if update:
            self._safe_update()

    def open_entries(
        self, state: str, version: str | None = None, mod_id: str | None = None
    ) -> None:
        """從總覽跳到條目校對並套用篩選。"""
        self.entries.show_filter(state)
        if version:
            self.entries.version = version
        self.entries.mod_id = mod_id
        self.show_tab("entries")

    def open_flagged(
        self, entry_ids: list[int], drafts: dict[int, str], version: str
    ) -> None:
        """從批次機翻跳到條目校對，只看「特殊字元不一致、沒寫入」的條目（AI 譯文預填）。"""
        self.entries.show_flagged(entry_ids, drafts, version)
        self.show_tab("entries")

    def _on_scan_finished(self) -> None:
        # 連線看得到其他連線已提交的資料；只有「原本沒有資料庫檔案」才需要重新開啟
        if self._db is None:
            self.reload_db()

    def _refresh_active_panel(self) -> None:
        """資料庫背景載入期間若切換了頁籤，連線就緒後刷新目前頁面。"""
        if self.tab == "entries":
            self.entries.refresh()
        elif self.tab == "scan":
            self.scan.refresh_versions()
        elif self.tab == "translate":
            self.translate.refresh_scope()

    # ------------------------------------------------------------------ 生命週期
    def will_unmount(self) -> None:
        self.scan.will_unmount()
        self.translate.will_unmount()

    def did_mount(self) -> None:
        # 從別的頁（例如機器翻譯寫回新資料）切回來時：連線本來就看得到新資料，
        # 不必重開（重開會丟掉 SQLite 的頁面快取，數十萬筆時每次都是冷啟動）；
        # 只有資料庫路徑／優先序設定變了、或原本沒有資料庫檔案才重新開啟
        if self._db is None or self._db_sig != self._settings_signature():
            self.reload_db()
        # 目前頁籤的面板才掛在畫面上：由 show_tab 決定是否接續輪詢（只 resume 目前頁籤），
        # 沒掛上畫面的面板不該重新開始更新畫面
        self.show_tab(self.tab)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料操作
            log_debug(f"ModDbView update 略過：{exc}")

    @property
    def page(self):
        return self._page
