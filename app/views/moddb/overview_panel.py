"""app/views/moddb/overview_panel.py：總覽（資料量、各版本翻譯進度、缺譯最多的模組、最近掃描）。"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_service import VersionStat, database_problem
from app.services_impl.moddb_source_service import source_label, source_tone
from app.ui import design, kit
from app.ui.design import C
from app.ui.kit.inputs import BUTTON_HEIGHTS
from app.views.moddb.formatting import format_count, percent
from translation_tool.utils.log_unit import log_debug

# 總覽四張 KPI 卡的標題列固定高度（有按鈕的卡比較高，固定後四張才等高）
OVERVIEW_CARD_HEAD = BUTTON_HEIGHTS["sm"] + 2  # 容得下 sm 按鈕與同高的說明圖示鈕


def _source_breakdown(db, source_stats):
    """Order observed source buckets by this DB's priority and build legend rows."""
    grouped: dict[str, list] = {}
    observed = set()
    for row in source_stats:
        grouped.setdefault(row.mc_version, []).append(row)
        if row.count > 0:
            observed.add((row.source, row.review_status))

    order = db.source_catalog.ordered_codes(db.priority)
    priority_index = {code: index for index, code in enumerate(order)}
    sources = sorted(
        {source for source, _status in observed if source is not None},
        key=lambda source: (priority_index.get(source, len(priority_index)), source),
    )
    status_order = {"unreviewed": 0, "reviewed": 1, "legacy_unknown": 2}
    source_index = {source: index for index, source in enumerate(sources)}
    for rows in grouped.values():
        rows.sort(
            key=lambda row: (
                len(source_index)
                if row.source is None
                else source_index.get(row.source, len(source_index)),
                status_order.get(row.review_status, 3),
            )
        )

    legend = []
    for source in sources:
        statuses = sorted(
            {status for row_source, status in observed if row_source == source},
            key=lambda status: status_order.get(status, 3),
        )
        for status in statuses:
            legend.append(
                (
                    source_label(source, db.source_catalog, status),
                    source_tone(source),
                )
            )
    if (None, None) in observed:
        legend.append(("未翻譯", "neutral"))
    return grouped, legend


class OverviewPanel(ft.Column):
    """總覽頁籤。"""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        *,
        request_refresh: Callable[[], None] | None = None,
        open_scan: Callable[[], None] | None = None,
        open_entries: Callable[[str, str | None, str | None], None] | None = None,
    ):
        """``open_entries(state, version, mod_id)`` 讓總覽的連結跳到條目校對。"""
        super().__init__(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self._get_db = get_db
        self._request_refresh = request_refresh
        self._open_scan = open_scan
        self._open_entries = open_entries
        self._has_snapshot = False

        self._build_stat_cards()
        self.versions_col = ft.Column(spacing=14)
        self.legend = ft.Row(spacing=8, wrap=True)
        self.legend_note = ft.Text(
            "圖例只列出目前生效且有筆數的來源；未翻譯另列。來源優先序只影響排列。",
            size=11.5,
            color=C.DIM,
        )
        self.missing_col = ft.Column(spacing=0)
        self.scans_col = ft.Column(spacing=8)
        self.refresh_btn = kit.button(
            "重新整理",
            "secondary",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self.refresh(),
        )
        self.problem = ft.Text(
            "", size=12.5, color=C.RED, selectable=True, visible=False
        )
        self.loading = ft.Row(
            [
                ft.ProgressRing(width=18, height=18, stroke_width=2),
                ft.Text("正在載入 Mod 資料庫總覽…", size=12.5, color=C.DIM),
            ],
            spacing=10,
            visible=True,
        )
        self.empty = kit.empty_state(
            "資料庫還沒有資料",
            "到「掃描匯入」選擇遊戲版本與 mods 資料夾，讀取 jar 內的語言檔即可建立",
            icon=ft.Icons.STORAGE_OUTLINED,
            action_text="前往掃描匯入",
            on_action=lambda _e: self._goto_scan(),
        )
        self.content_col = ft.Column(
            [
                ft.Row(
                    [
                        self.stat_mods,
                        self.stat_content,
                        self.stat_diff,
                        self.stat_changed,
                    ],
                    spacing=12,
                ),
                kit.section_card(
                    "各版本翻譯進度",
                    ft.Column(
                        [self.legend, self.legend_note, self.versions_col], spacing=8
                    ),
                    icon=ft.Icons.BAR_CHART,
                    tone="em",
                ),
                kit.section_card(
                    "缺譯最多的模組",
                    self.missing_col,
                    icon=ft.Icons.REPORT_GMAILERRORRED,
                    tone="gold",
                    flush=True,
                ),
                kit.section_card(
                    "最近掃描", self.scans_col, icon=ft.Icons.HISTORY, tone="dia"
                ),
            ],
            spacing=12,
        )
        self.controls = [
            ft.Row([self.refresh_btn], alignment=ft.MainAxisAlignment.END),
            self.loading,
            self.problem,
            self.empty,
            self.content_col,
        ]
        self.set_loading(preserve=False)

    def _build_stat_cards(self) -> None:
        self.diff_btn = kit.button(
            "檢視",
            "secondary",
            size="sm",
            icon=ft.Icons.COMPARE_ARROWS,
            tooltip="跳到條目校對，只看「版本不同」的條目",
            on_click=lambda _e: self._goto_entries("diff"),
        )
        self.diff_help_btn = ft.IconButton(
            icon=ft.Icons.HELP_OUTLINE,
            icon_size=18,
            icon_color=C.MUTED,
            padding=0,  # 預設 padding 8 會讓按鈕高過標題列；固定成與 sm 按鈕同高
            width=BUTTON_HEIGHTS["sm"],
            height=BUTTON_HEIGHTS["sm"],
            tooltip="說明",
            on_click=lambda _e: self._show_diff_help(),
        )
        self.changed_btn = kit.button(
            "檢視",
            "secondary",
            size="sm",
            icon=ft.Icons.EDIT_NOTE,
            tooltip="跳到條目校對，只看「原文已變動」的條目",
            on_click=lambda _e: self._goto_entries("changed"),
        )
        self.stat_mods = kit.stat_card(
            "模組",
            "—",
            icon=ft.Icons.EXTENSION_OUTLINED,
            tone="em",
            delta="資料庫內出現過的模組",
            delta_tone="neutral",
            expand=1,
            head_height=OVERVIEW_CARD_HEAD,
            reserve_delta_space=True,
        )
        self.stat_content = kit.stat_card(
            "不重複條目",
            "—",
            icon=ft.Icons.TEXT_SNIPPET_OUTLINED,
            tone="dia",
            expand=1,
            head_height=OVERVIEW_CARD_HEAD,
            reserve_delta_space=True,
        )
        self.stat_diff = kit.stat_card(
            "跨版本譯文不同",
            "—",
            icon=ft.Icons.COMPARE_ARROWS,
            tone="gold",
            delta="相同內容、各版本譯文不一致",
            delta_tone="neutral",
            expand=1,
            action=ft.Row([self.diff_help_btn, self.diff_btn], spacing=4, tight=True),
            head_height=OVERVIEW_CARD_HEAD,
            reserve_delta_space=True,
        )
        self.stat_changed = kit.stat_card(
            "原文已變動",
            "—",
            icon=ft.Icons.EDIT_NOTE,
            tone="neutral",
            delta="鍵值相同但原文改了（掃描時略過）",
            delta_tone="neutral",
            expand=1,
            action=self.changed_btn,
            head_height=OVERVIEW_CARD_HEAD,
            reserve_delta_space=True,
        )

    # ------------------------------------------------------------------ 載入
    def refresh(self, *, update: bool = False) -> None:
        """請求非同步重新載入；資料查詢由 ModDbView 的背景工作負責。"""
        if self._request_refresh is None:
            db = self._get_db()
            stats = db.version_stats() if db else []
            if not stats:
                problem = database_problem()
                if problem:
                    self.show_error(problem)
                    if update:
                        self._safe_update()
                    return
            overview = db.overview() if db and stats else {}
            source_stats = (
                db.effective_source_stats_by_version() if db and stats else []
            )
            missing = db.missing_by_mod(stats[0].mc_version, limit=10) if stats else []
            scans = db.last_scans(5) if db and stats else []
            self.apply_snapshot(
                db,
                stats=stats,
                overview=overview,
                source_stats=source_stats,
                missing=missing,
                scans=scans,
            )
            if update:
                self._safe_update()
            return
        self._request_refresh()

    def set_loading(self, *, preserve: bool = True) -> None:
        self.loading.visible = True
        self.refresh_btn.disabled = True
        self.problem.visible = False
        if not preserve or not self._has_snapshot:
            self.empty.visible = False
            self.content_col.visible = False

    def show_error(self, message: str) -> None:
        self.loading.visible = False
        self.refresh_btn.disabled = False
        self.problem.value = f"⚠ 資料庫無法使用：{message}"
        self.problem.visible = True
        if not self._has_snapshot:
            self.empty.visible = False
            self.content_col.visible = False

    def apply_snapshot(
        self,
        db,
        *,
        stats: list[VersionStat],
        overview: dict,
        source_stats: list,
        missing: list[dict],
        scans: list[dict],
    ) -> None:
        self._has_snapshot = True
        self.loading.visible = False
        self.refresh_btn.disabled = False
        self.problem.value = ""
        self.problem.visible = False
        has_data = bool(stats)
        self.empty.visible = not has_data
        self.content_col.visible = has_data
        if has_data and db is not None:
            source_stats_by_version, legend_rows = _source_breakdown(db, source_stats)
            self.legend.controls = [
                kit.chip(label, tone, dot=True) for label, tone in legend_rows
            ]
            self.stat_mods.set_value(format_count(overview["mods"]))
            self.stat_content.set_value(
                format_count(overview["content"]),
                delta=f"共 {format_count(sum(s.total for s in stats))} 筆（含各版本）"
                + (
                    f"・原文未知 {format_count(overview['no_source'])}"
                    if overview["no_source"]
                    else ""
                ),
                delta_tone="neutral",
            )
            self.stat_diff.set_value(format_count(overview["diff"]))
            self.stat_changed.set_value(format_count(overview["src_changed"]))
            self.versions_col.controls = [
                self._version_bar(
                    s,
                    source_stats_by_version.get(s.mc_version, []),
                    db.source_catalog,
                )
                for s in stats
            ]
            self._render_missing(missing, stats[0].mc_version)
            self._render_scans(scans)

    def _safe_update(self) -> None:
        try:
            self._page.update()
        except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料
            log_debug(f"OverviewPanel update 略過：{exc}")

    def _version_bar(self, s: VersionStat, source_stats: list, catalog) -> ft.Control:
        segs = [
            ft.Container(
                expand=max(row.count, 0),
                bgcolor=(
                    design.tone(source_tone(row.source)).fg
                    if row.source is not None
                    else C.TRACK
                ),
                height=10,
                tooltip=(
                    f"{source_label(row.source, catalog, row.review_status) if row.source is not None else '未翻譯'}：{row.count:,}"
                ),
            )
            for row in source_stats
            if row.count > 0
        ]
        translated = s.total - s.untranslated
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            s.mc_version,
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT,
                        ),
                        ft.Text(
                            f"{s.total:,} 條・已翻譯 {percent(translated, s.total)}%",
                            size=12,
                            color=C.MUTED,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    ft.Row(segs, spacing=0),
                    border_radius=5,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    border=ft.Border.all(1, C.LINE),
                    height=12,
                ),
            ],
            spacing=5,
        )

    def _render_missing(self, rows: list[dict], version: str) -> None:
        header = ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            content=ft.Row(
                [
                    self._cell("模組", 4, True),
                    self._cell("條目", 1, True),
                    self._cell("未翻譯", 1, True),
                    self._cell("進度", 1, True),
                    self._cell("", 1, True),
                ]
            ),
        )
        body = [
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=16, vertical=6),
                border=ft.Border.only(top=ft.BorderSide(1, C.LINE)),
                content=ft.Row(
                    [
                        self._cell(r["mod_id"], 4),
                        self._cell(f"{r['total']:,}", 1),
                        self._cell(f"{r['missing']:,}", 1),
                        self._cell(f"{r['progress']}%", 1),
                        ft.Container(
                            kit.button(
                                "校對",
                                "ghost",
                                size="sm",
                                on_click=lambda _e, m=r["mod_id"], v=version: (
                                    self._goto_entries("none", v, m)
                                ),
                            ),
                            expand=1,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            for r in rows
        ]
        self.missing_col.controls = (
            [
                ft.Container(
                    ft.Text(f"版本 {version}", size=11.5, color=C.DIM),
                    padding=ft.Padding.only(left=16, top=10),
                ),
                header,
                *body,
            ]
            if rows
            else [ft.Container(kit.hint_text(f"{version} 沒有缺譯的條目"), padding=16)]
        )

    @staticmethod
    def _cell(text: str, flex: int, head: bool = False) -> ft.Control:
        return ft.Container(
            ft.Text(
                text,
                size=11.5 if head else 12.5,
                color=C.DIM if head else C.TEXT,
                weight=ft.FontWeight.W_500 if head else None,
                no_wrap=True,
            ),
            expand=flex,
        )

    def _render_scans(self, scans: list[dict]) -> None:
        self.scans_col.controls = [
            ft.Row(
                [
                    kit.chip(s["mc_version"], "em"),
                    ft.Text(
                        f"{s['finished_at'][:16]}　新增 {s['stats'].get('new_entries', 0):,}　補入 {s['stats'].get('added_translations', 0):,}　略過 {s['stats'].get('existing', 0):,}",
                        size=12,
                        color=C.TEXT,
                    ),
                    ft.Text(
                        s["folder"],
                        size=11,
                        color=C.DIM,
                        expand=True,
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=10,
            )
            for s in scans
        ] or [kit.hint_text("尚未有掃描記錄")]

    # ------------------------------------------------------------------ 導覽
    def _goto_scan(self) -> None:
        if self._open_scan:
            self._open_scan()

    def _show_diff_help(self) -> None:
        """說明「跨版本譯文不同」查的是什麼，以及為何可能是空白。"""
        show_dialog = getattr(self._page, "show_dialog", None)
        if not callable(show_dialog):
            return
        show_dialog(
            ft.AlertDialog(
                title=ft.Text("關於「跨版本譯文不同」"),
                content=ft.Text(
                    "會跳到「條目校對」並套用「版本不同」篩選，列出對應上方"
                    "「跨版本譯文不同」的條目：同一個模組、同一個鍵值、原文相同，"
                    "但在另一個遊戲版本中的譯文不一樣。\n\n"
                    "資料庫只有一個遊戲版本時沒有其他版本可以比較，結果一定是空白；"
                    "匯入第二個版本後才會出現。\n\n"
                    "注意：這不是「原文已變動」。原文已變動是鍵值相同但英文原文改了"
                    "（掃描時略過），請用右邊「原文已變動」卡片的「檢視」查看。",
                    selectable=True,
                    width=420,
                ),
                actions=[
                    ft.TextButton(
                        "知道了", on_click=lambda _e=None: self._page.pop_dialog()
                    )
                ],
            )
        )

    def _goto_entries(
        self, state: str, version: str | None = None, mod_id: str | None = None
    ) -> None:
        if self._open_entries:
            self._open_entries(state, version, mod_id)
