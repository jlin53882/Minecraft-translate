"""app/views/moddb_view.py：Mod 資料庫頁（總覽／條目校對／掃描匯入／批次機翻）。

資料庫是分版本的翻譯記憶庫：掃描 jar 建立、翻譯結果自動寫回、可在這裡手動校對。
頁面本身只負責切換頁籤與持有資料庫連線；各頁籤在 ``app/views/moddb/``。
"""

from __future__ import annotations

import flet as ft

from app.services_impl.moddb_service import (
    TranslationDB,
    current_settings,
    open_database,
    warm_stats_quietly,
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


class ModDbView(ft.Column):
    """Mod 翻譯資料庫頁。"""

    def __init__(self, page: ft.Page, file_picker: ft.FilePicker):
        super().__init__(expand=True, spacing=16)
        self._page = page
        self.file_picker = file_picker
        self._db: TranslationDB | None = None
        self._db_loaded = False
        self._db_sig: tuple | None = None
        self.tab = "overview"

        self.overview = OverviewPanel(
            page,
            self.get_db,
            open_scan=lambda: self.show_tab("scan"),
            open_entries=self.open_entries,
        )
        self.entries = EntriesPanel(page, self.get_db, on_changed=self._on_data_changed)
        self.scan = ScanPanel(
            page, file_picker, self.get_db, on_finished=self._on_scan_finished
        )
        self.translate = TranslatePanel(
            page, self.get_db, on_finished=self._on_scan_finished
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
            self._db_sig = self._settings_signature()
            self._db = open_database(create=False)
            self._db_loaded = True
            self._warm_in_background(self._db)
        return self._db

    @staticmethod
    def _settings_signature() -> tuple:
        """資料庫路徑與來源優先序；兩者變了才需要重新開啟連線。"""
        settings = current_settings()
        return (str(settings.resolved_path()), settings.priority)

    def _warm_in_background(self, db: TranslationDB | None) -> None:
        """開啟資料庫後在背景先算好總覽統計；使用者切到總覽頁時多半已經算好。"""
        run_thread = getattr(self._page, "run_thread", None)
        if db is not None and callable(run_thread):
            run_thread(warm_stats_quietly, db)

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
        if key == "overview":
            self.overview.refresh()
        elif key == "entries":
            self.entries.refresh()
        elif key == "scan":
            self.scan.refresh_versions()
        elif key == "translate":
            self.translate.refresh_scope()
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

    def _on_scan_finished(self) -> None:
        # 連線看得到其他連線已提交的資料；只有「原本沒有資料庫檔案」才需要重新開啟
        if self._db is None:
            self.reload_db()
        self.overview.refresh()

    def _on_data_changed(self) -> None:
        """手動儲存後：總覽的統計等切到總覽頁時才更新（它要重算數十萬筆，不能卡在每次儲存）。

        統計快取已因寫入失效，這裡在背景先算好，之後切到總覽頁就是即時的。
        """
        self._warm_in_background(self._db)

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
        self.scan.resume()
        self.translate.resume()
        self.show_tab(self.tab)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料操作
            log_debug(f"ModDbView update 略過：{exc}")

    @property
    def page(self):
        return self._page
