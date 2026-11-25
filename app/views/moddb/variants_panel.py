"""app/views/moddb/variants_panel.py：「其他原文」頁籤（鍵值相同、原文不同的內容）。

瀏覽、搜尋每份其他原文與它的譯文；長文字在右側並排檢視。第一版只讀，另有「沿用目前譯文」
一個寫入動作。資料讀取一律在 worker（``run_read``），畫面執行緒不碰資料庫；
資料庫被別處寫入時由上層呼叫 ``mark_stale``，下次顯示（或目前正在顯示）時重新載入。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_source_service import source_label, source_tone
from app.services_impl.moddb_variants_service import (
    STATUS_ALL,
    STATUS_TRANSLATED,
    STATUS_UNTRANSLATED,
)
from app.ui import design, kit
from app.ui.design import C
from app.ui.update_scope import update_regions
from app.views.moddb import entry_variant_actions
from app.views.moddb.background_read import run_read
from app.views.moddb.formatting import shorten
from app.views.moddb.variant_detail import build_variant_detail
from translation_tool.utils.log_unit import log_debug

PAGE_SIZE = 30
ALL_MODS = "__all__"
STATUS_OPTIONS = (
    (STATUS_ALL, "全部"),
    (STATUS_UNTRANSLATED, "沒有譯文"),
    (STATUS_TRANSLATED, "已有譯文"),
)
LIST_WIDTH = 440
PREVIEW_CHARS = 90


class VariantsPanel(ft.Column):
    """其他原文清單（左）與並排檢視（右）。"""

    def __init__(
        self,
        page: ft.Page,
        get_db: Callable[[], object | None],
        *,
        on_database_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(expand=True, spacing=12)
        self._page = page
        self._get_db = get_db
        self._on_database_changed = on_database_changed
        self._stale = True
        self._loading = False
        self._generation = 0
        self._catalog = None
        self.version = ""
        self.status = STATUS_ALL
        self.mod_id = ""
        self.search = ""
        self.rows: list = []
        self.selected_id: int | None = None
        self.refresh_indicator = ft.Text("", size=12, color=C.DIM, visible=False)

        filter_bar = self._build_filter_bar()
        self.summary = ft.Text("", size=12.5, color=C.DIM)
        self.controls = [
            self.refresh_indicator,
            filter_bar,
            self.summary,
            self._build_body(),
        ]
        self._show_placeholder("尚未載入")

    def _build_filter_bar(self) -> ft.Row:
        self.version_dd = kit.dropdown(
            label="遊戲版本", dense=True, width=200, on_select=self._on_filter
        )
        self.status_dd = kit.dropdown(
            label="譯文", dense=True, width=150, on_select=self._on_filter
        )
        self.status_dd.options = [
            ft.dropdown.Option(key=key, text=text) for key, text in STATUS_OPTIONS
        ]
        self.status_dd.value = STATUS_ALL
        self.mod_dd = kit.searchable_dropdown(
            label="模組", dense=True, width=240, on_select=self._on_filter
        )
        self.search_field = kit.text_field(
            "搜尋",
            hint="鍵值、模組或原文，按 Enter",
            width=300,
            on_submit=self._on_filter,
        )
        self.reload_btn = kit.button(
            "重新整理",
            "secondary",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self.reload(),
        )
        return ft.Row(
            [
                self.version_dd,
                self.mod_dd,
                self.status_dd,
                self.search_field,
                self.reload_btn,
            ],
            spacing=10,
            wrap=True,
        )

    def _build_body(self) -> ft.Row:
        self.list_col = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
        self.pager = kit.Pager(
            0, page_size=PAGE_SIZE, unit="份", on_change=self._on_page
        )
        self.detail_host = ft.Container(expand=True, padding=ft.Padding.only(left=4))
        return ft.Row(
            [
                ft.Container(
                    width=LIST_WIDTH,
                    content=ft.Column(
                        [self.list_col, self.pager], spacing=8, expand=True
                    ),
                ),
                self.detail_host,
            ],
            spacing=16,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ------------------------------------------------------------------ 載入
    def mark_stale(self) -> None:
        """資料庫被寫入：下次顯示（或現在就在顯示）時重新載入。"""
        self._stale = True

    def activate(self) -> None:
        """頁籤被切到前景。"""
        if self._stale:
            self.reload()
        else:
            self.flush()

    def reload(self, *, page: int | None = None) -> None:
        """在背景載入目前條件的一頁；條件沒變且仍有資料時保留頁碼與選取。"""
        db = self._get_db()
        self._generation += 1
        generation = self._generation
        request = {
            "version": self.version,
            "status": self.status,
            "mod_id": self.mod_id,
            "search": self.search,
            "page": page or self.pager.current_page,
        }
        self._set_loading(True)

        def work() -> dict:
            return _load_snapshot(db, request)

        def apply(snapshot, error) -> None:
            if generation != self._generation:
                return  # 較新的請求已經送出
            self._set_loading(False)
            if error is not None:
                if not isinstance(error, Exception):
                    raise error
                log_debug(f"Mod DB 其他原文載入失敗：{error!r}")
                self._show_placeholder(f"載入失敗：{error}")
                self.flush()
                return
            self._apply_snapshot(snapshot)

        if db is None:
            self._set_loading(False)
            self._show_placeholder("尚未建立 Mod 資料庫。請先到「掃描匯入」掃描 jar。")
            self.flush()
            return
        if not run_read(
            self._page,
            work,
            apply,
            name="Mod DB 其他原文載入",
            owner="moddb-variants",
        ):
            self._set_loading(False)

    def _set_loading(self, loading: bool) -> None:
        self._loading = loading
        self.refresh_indicator.value = "背景更新其他原文中…" if loading else ""
        self.refresh_indicator.visible = loading
        update_regions(self.refresh_indicator)

    def _apply_snapshot(self, snapshot: dict) -> None:
        self._stale = False
        self._catalog = snapshot["catalog"]
        versions = snapshot["versions"]
        self.version = snapshot["version"]
        kit.set_dropdown_options(self.version_dd, [(v, v) for v in versions])
        self.version_dd.value = self.version or None
        kit.set_dropdown_options(
            self.mod_dd,
            [(ALL_MODS, "全部模組"), *((m, m) for m in snapshot["mods"])],
        )
        self.mod_dd.value = self.mod_id or ALL_MODS
        self.rows = snapshot["rows"]
        total = snapshot["total"]
        self.pager.set_state(total, snapshot["page"])
        if not versions:
            self.summary.value = ""
            self.list_col.controls = []
            self._show_placeholder(
                "目前沒有其他原文。掃描匯入時，鍵值相同但原文不同的內容會登記在這裡。"
            )
        else:
            self.summary.value = f"{self.version}：共 {total:,} 份符合條件的其他原文"
            if self.selected_id not in {r.variant_id for r in self.rows}:
                self.selected_id = self.rows[0].variant_id if self.rows else None
            self._render_rows()
            self._render_detail()
        self.flush()

    # ------------------------------------------------------------------ 篩選
    def _on_filter(self, _e=None) -> None:
        self.version = self.version_dd.value or ""
        self.status = self.status_dd.value or STATUS_ALL
        mod = self.mod_dd.value or ""
        self.mod_id = "" if mod == ALL_MODS else mod
        self.search = (self.search_field.value or "").strip()
        self.selected_id = None
        self.reload(page=1)

    def _on_page(self, page: int) -> None:
        self.reload(page=page)

    # ------------------------------------------------------------------ 清單與檢視
    def _render_rows(self) -> None:
        self.list_col.controls = [self._row_tile(row) for row in self.rows]
        if not self.rows:
            self.list_col.controls = [
                kit.empty_state("沒有符合條件的其他原文", "請調整篩選條件。")
            ]

    def _row_tile(self, row) -> ft.Control:
        selected = row.variant_id == self.selected_id
        if row.zh_tw:
            label = source_label(row.source, self._catalog, row.review_status)
            status = kit.chip(label, source_tone(row.source))
        else:
            status = kit.chip("尚無譯文", "neutral")
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=9),
            bgcolor=C.PANEL2 if selected else None,
            border_radius=design.RADIUS_CONTROL,
            border=ft.Border.all(1, C.EM if selected else C.LINE),
            ink=True,
            on_click=lambda _e, vid=row.variant_id: self._select(vid),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            kit.mono_text(row.key, size=12, no_wrap=True, expand=True),
                            status,
                        ],
                        spacing=8,
                    ),
                    kit.hint_text(row.mod_id),
                    ft.Text(
                        shorten(row.en_us, PREVIEW_CHARS),
                        size=12.5,
                        color=C.TEXT,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=3,
                tight=True,
            ),
        )

    def _select(self, variant_id: int) -> None:
        if variant_id == self.selected_id:
            return
        self.selected_id = variant_id
        self._render_rows()
        self._render_detail()
        self.flush()

    def _selected_row(self):
        return next((r for r in self.rows if r.variant_id == self.selected_id), None)

    def _render_detail(self) -> None:
        row = self._selected_row()
        if row is None:
            self._show_placeholder("從左側選一份其他原文，這裡會並排顯示完整內容。")
            return
        adoptable = not row.zh_tw and bool(row.main_zh)
        self.detail_host.content = build_variant_detail(
            row,
            self._catalog,
            version=self.version,
            on_adopt=(lambda: self._adopt(row)) if adoptable else None,
        )

    def _show_placeholder(self, text: str) -> None:
        self.detail_host.content = kit.empty_state(text, icon=ft.Icons.COMPARE_ARROWS)

    # ------------------------------------------------------------------ 寫入
    def _adopt(self, row) -> None:
        """把條目目前的譯文沿用為這份其他原文的譯文（背景寫入，完成後通知上層刷新）。"""
        db = self._get_db()
        if db is None or not row.main_zh:
            return
        entry_variant_actions.adopt_for_variant(
            self._page,
            db,
            entry_id=row.entry_id,
            current_zh_tw=row.main_zh,
            variant_en=row.en_us,
            where=f"{self.version} {row.mod_id} {row.key}",
            on_success=self._adopted,
        )

    def _adopted(self) -> None:
        self.mark_stale()
        if self._on_database_changed is not None:
            self._on_database_changed()
        else:
            self.reload()

    # ------------------------------------------------------------------ 畫面
    def flush(self) -> None:
        update_regions(self)

    def will_unmount(self) -> None:
        """離開頁籤：丟棄還在途中的載入結果（下次顯示時重新載入）。"""
        if self._loading:
            self._stale = True
            self._loading = False
        self._generation += 1
        self.refresh_indicator.visible = False


def _load_snapshot(db, request: dict) -> dict:
    """worker：版本與模組清單加上目前條件的一頁（只讀）。"""
    versions = db.variant_versions()
    version = request["version"] if request["version"] in versions else ""
    if not version and versions:
        version = versions[0]
    mods = db.variant_mods(version) if version else []
    mod_id = request["mod_id"] if request["mod_id"] in mods else ""
    page = max(1, int(request["page"]))
    rows, total = (
        db.list_variants(
            version,
            status=request["status"],
            search=request["search"],
            mod_id=mod_id or None,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )
        if version
        else ([], 0)
    )
    last_page = max(1, -(-total // PAGE_SIZE))
    if page > last_page:  # 資料變少後頁碼超出：回到最後一頁
        page = last_page
        rows, total = db.list_variants(
            version,
            status=request["status"],
            search=request["search"],
            mod_id=mod_id or None,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )
    return {
        "versions": versions,
        "version": version,
        "mods": mods,
        "rows": rows,
        "total": total,
        "page": page,
        "catalog": db.source_catalog,
    }
