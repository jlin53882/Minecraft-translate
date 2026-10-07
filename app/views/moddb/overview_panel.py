"""app/views/moddb/overview_panel.py：總覽（資料量、各版本翻譯進度、缺譯最多的模組、最近掃描）。"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.services_impl.moddb_service import VersionStat, database_problem
from app.ui import design, kit
from app.ui.design import C
from app.views.moddb.formatting import format_count, percent
from translation_tool.utils.log_unit import log_debug

# 進度條各段：(欄位, 標籤, 色調)
SEGMENTS = (
    ("manual", "人工", "ench"),
    ("jar", "模組自帶／人工來源", "dia"),
    ("converted", "簡中轉繁", "gold"),
    ("ai", "AI 機翻", "em"),
    ("untranslated", "未翻譯", "neutral"),
)


class OverviewPanel(ft.Column):
    """總覽頁籤。"""

    def __init__(
        self,
        page: ft.Page,
        get_db,
        *,
        open_scan: Callable[[], None] | None = None,
        open_entries: Callable[[str, str | None, str | None], None] | None = None,
    ):
        """``open_entries(state, version, mod_id)`` 讓總覽的連結跳到條目校對。"""
        super().__init__(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self._page = page
        self._get_db = get_db
        self._open_scan = open_scan
        self._open_entries = open_entries

        self._build_stat_cards()
        self.versions_col = ft.Column(spacing=14)
        self.legend = ft.Row(
            [kit.chip(label, tone, dot=True) for _f, label, tone in SEGMENTS],
            spacing=8,
            wrap=True,
        )
        self.missing_col = ft.Column(spacing=0)
        self.scans_col = ft.Column(spacing=8)
        self.refresh_btn = kit.button(
            "重新整理",
            "secondary",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self.refresh(update=True),
        )
        self.problem = ft.Text(
            "", size=12.5, color=C.RED, selectable=True, visible=False
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
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,  # 四張卡等高
                ),
                kit.section_card(
                    "各版本翻譯進度",
                    ft.Column([self.legend, self.versions_col], spacing=12),
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
            self.problem,
            self.empty,
            self.content_col,
        ]

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
            icon_size=20,
            icon_color=C.MUTED,
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
        )
        self.stat_content = kit.stat_card(
            "不重複條目", "—", icon=ft.Icons.TEXT_SNIPPET_OUTLINED, tone="dia", expand=1
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
        )

    # ------------------------------------------------------------------ 載入
    def refresh(self, *, update: bool = False) -> None:
        db = self._get_db()
        stats = db.version_stats() if db else []
        has_data = bool(stats)
        self.empty.visible = not has_data
        self.content_col.visible = has_data
        # 檔案存在卻開不起來（其他用途的 SQLite、版本太新）：說明原因，避免誤導成「尚未建立」
        problem = "" if has_data else database_problem()
        self.problem.value = f"⚠ 資料庫無法使用：{problem}" if problem else ""
        self.problem.visible = bool(problem)
        if problem:
            self.empty.visible = False
        if has_data and db is not None:
            ov = db.overview()
            self.stat_mods.set_value(format_count(ov["mods"]))
            self.stat_content.set_value(
                format_count(ov["content"]),
                delta=f"共 {format_count(sum(s.total for s in stats))} 筆（含各版本）"
                + (
                    f"・原文未知 {format_count(ov['no_source'])}"
                    if ov["no_source"]
                    else ""
                ),
                delta_tone="neutral",
            )
            self.stat_diff.set_value(format_count(ov["diff"]))
            self.stat_changed.set_value(format_count(ov["src_changed"]))
            self.versions_col.controls = [self._version_bar(s) for s in stats]
            self._render_missing(db, stats[0].mc_version)
            self._render_scans(db)
        if update:
            try:
                self._page.update()
            except Exception as exc:  # noqa: BLE001 - 頁面已卸載時不影響資料
                log_debug(f"OverviewPanel update 略過：{exc}")

    def _version_bar(self, s: VersionStat) -> ft.Control:
        values = {f: getattr(s, f) for f, _l, _t in SEGMENTS}
        segs = [
            ft.Container(
                expand=max(v, 0),
                bgcolor=design.tone(tone).fg if f != "untranslated" else C.TRACK,
                height=10,
                tooltip=f"{label}：{v:,}",
            )
            for (f, label, tone), v in zip(SEGMENTS, values.values(), strict=True)
            if v > 0
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

    def _render_missing(self, db, version: str) -> None:
        rows = db.missing_by_mod(version, limit=10)
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

    def _render_scans(self, db) -> None:
        scans = db.last_scans(5)
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
