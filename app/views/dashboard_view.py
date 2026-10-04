"""app/views/dashboard_view.py：工作台（首頁）。

一眼看到專案狀態：快取筆數、替換規則數、API Key 健康度、本次任務，以及翻譯流程五步目前走到哪。
資料都來自真實來源（見 ``dashboard/dashboard_data.py``）；快取概覽與規則數在背景執行緒讀取，
讀完再套到畫面，所以第一次開啟不會卡住。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import flet as ft

from app.services_impl.key_health_service import (
    STATUS_COOLING,
    STATUS_PROBING,
    KeyHealth,
)
from app.shell.task_manager import STATUS_ERROR, TaskManager
from app.ui import design, kit
from app.ui.design import C
from app.ui.design import tone as get_tone
from app.views.dashboard.dashboard_data import (
    STEP_DONE,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    CacheBar,
    DashboardData,
    StepStatus,
    build_dashboard_data,
    format_ago,
    format_count,
    greeting,
)

logger = logging.getLogger(__name__)

STEP_ICONS = {
    "extractor": ft.Icons.INVENTORY_2_OUTLINED,
    "merge": ft.Icons.CALL_MERGE,
    "lm": ft.Icons.AUTO_AWESOME_OUTLINED,
    "qc": ft.Icons.VERIFIED_USER_OUTLINED,
    "bundler": ft.Icons.FOLDER_ZIP_OUTLINED,
}
STEP_TONES = {
    "extractor": "dia",
    "merge": "ench",
    "lm": "gold",
    "qc": "red",
    "bundler": "em",
}
STEP_STATUS_STYLE = {
    STEP_PENDING: ("待執行", "neutral"),
    STEP_RUNNING: ("進行中", "gold"),
    STEP_DONE: ("完成", "em"),
    STEP_FAILED: ("失敗", "red"),
}
KEY_STATUS_STYLE = {
    "ok": ("正常", "em"),
    STATUS_COOLING: ("冷卻中", "gold"),
    STATUS_PROBING: ("待重試", "dia"),
}
BAR_TONES = ("em", "dia", "ench", "gold", "red")


def _default_cache_overview() -> dict:
    from app.services_impl.cache.cache_services import cache_get_overview_service

    return cache_get_overview_service()


def _default_rules_count() -> int:
    from app.services_impl.config_service import load_replace_rules

    return len(load_replace_rules())


def _default_key_snapshot() -> list[KeyHealth]:
    from app.services_impl.key_health_service import get_key_health_snapshot

    return get_key_health_snapshot()


class DashboardView(ft.Column):
    """工作台。``set_shell`` 由外殼在頁面建立後呼叫，提供導覽與任務事件。"""

    def __init__(
        self,
        page: ft.Page,
        *,
        cache_overview_loader: Callable[[], dict] = _default_cache_overview,
        rules_count_loader: Callable[[], int] = _default_rules_count,
        key_snapshot_loader: Callable[[], list] = _default_key_snapshot,
    ) -> None:
        super().__init__(expand=True, spacing=18, scroll=ft.ScrollMode.AUTO)
        activity_card, cache_card, flow_card = self._init_dashboard_state_and_cards(
            cache_overview_loader, key_snapshot_loader, page, rules_count_loader
        )
        keys_card = self._build_dashboard_keys_card()
        self.controls = [
            ft.Row(
                [
                    ft.Row(
                        [
                            kit.tone_icon(
                                ft.Icons.GRID_VIEW, "em", size=22, box=46, radius=13
                            ),
                            ft.Column(
                                [
                                    self.title_text,
                                    ft.Text(
                                        "專案狀態一覽：快取、規則、API Key 與翻譯流程進度",
                                        size=13,
                                        color=C.MUTED,
                                    ),
                                ],
                                spacing=2,
                                tight=True,
                            ),
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row([self.refresh_button, self.continue_button], spacing=10),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.END,
            ),
            ft.Row(
                [self.stat_cache, self.stat_rules, self.stat_keys, self.stat_tasks],
                spacing=14,
            ),
            ft.Row(
                [
                    ft.Column([flow_card], expand=7),
                    ft.Column([cache_card], expand=4),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            ft.Row(
                [
                    ft.Column([activity_card], expand=6),
                    ft.Column([keys_card], expand=5),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]
        self.refresh_view(self._collect())

    def _init_dashboard_state_and_cards(
        self, cache_overview_loader, key_snapshot_loader, page, rules_count_loader
    ):
        """工作台的狀態與流程／快取／活動卡片。"""
        self._page = page
        self._cache_overview_loader = cache_overview_loader
        self._rules_count_loader = rules_count_loader
        self._key_snapshot_loader = key_snapshot_loader
        self._navigate: Callable[[str], None] | None = None
        self._tasks: TaskManager | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._cache_overview: dict | None = None
        self._rules_count: int | None = None
        self._loading = False
        self._active_task_ids: set[int] = set()
        self._task_state_lock = threading.Lock()
        self.data = DashboardData()

        self.title_text = ft.Text("", size=22, weight=ft.FontWeight.BOLD, color=C.TEXT)
        self.stat_cache = kit.stat_card(
            "快取條目", "—", icon=ft.Icons.STORAGE_OUTLINED, tone="ench", expand=1
        )
        self.stat_rules = kit.stat_card(
            "替換規則", "—", icon=ft.Icons.FIND_REPLACE, tone="dia", expand=1
        )
        self.stat_keys = kit.stat_card(
            "可用 API Key", "—", icon=ft.Icons.KEY, tone="gold", expand=1
        )
        self.stat_tasks = kit.stat_card(
            "本次任務", "0", icon=ft.Icons.TASK_ALT, tone="em", expand=1
        )
        self.steps_row = ft.Row(spacing=12)
        self.step_badge = ft.Container()
        self.cache_column = ft.Column(spacing=12)
        self.activity_column = ft.Column(spacing=2)
        self.keys_column = ft.Column(spacing=6)
        self.refresh_button = kit.button(
            "重新整理",
            "secondary",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self.reload(),
        )
        self.continue_button = kit.button(
            "前往一鍵流水線",
            "primary",
            icon=ft.Icons.PLAY_ARROW,
            on_click=lambda _e: self._go("pipeline"),
        )

        flow_card = kit.section_card(
            "翻譯流程",
            self.steps_row,
            icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
            tone="em",
            badge=self.step_badge,
        )
        cache_card = kit.section_card(
            "快取分佈", self.cache_column, icon=ft.Icons.STORAGE_OUTLINED, tone="ench"
        )
        activity_card = kit.section_card(
            "最近活動", self.activity_column, icon=ft.Icons.HISTORY, tone="dia"
        )
        return activity_card, cache_card, flow_card

    def _build_dashboard_keys_card(self):
        """API 金鑰卡片。"""
        keys_card = kit.section_card(
            "API Key 狀態",
            self.keys_column,
            icon=ft.Icons.KEY,
            tone="gold",
            actions=[
                kit.button(
                    "前往設定",
                    "ghost",
                    size="sm",
                    on_click=lambda _e: self._go("config"),
                )
            ],
        )
        return keys_card

    # -- 外殼介面 --------------------------------------------------------------

    def set_shell(self, shell) -> None:
        """外殼建立頁面後呼叫：提供導覽，並訂閱任務事件（任務一變就更新流程進度與活動）。"""
        self._navigate = shell.navigate
        self.set_task_manager(shell.tasks)

    def set_task_manager(self, tasks: TaskManager) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
        self._tasks = tasks
        with self._task_state_lock:
            self._active_task_ids = {task.id for task in tasks.active()}
        self._unsubscribe = tasks.subscribe(self._on_tasks_changed)
        self.refresh_view(self._collect())

    def _go(self, view_key: str) -> None:
        if self._navigate is not None:
            self._navigate(view_key)

    def did_mount(self) -> None:
        self.reload()

    # -- 資料 ----------------------------------------------------------------

    def _collect(self) -> DashboardData:
        """用目前已知的資料組出畫面資料（不做 I/O；快取 / 規則由 reload 另外載入）。"""
        try:
            keys = self._key_snapshot_loader()
        except Exception:
            logger.debug("工作台讀取 Key 狀態失敗", exc_info=True)
            keys = []
        tasks = self._tasks
        return build_dashboard_data(
            cache_overview=self._cache_overview,
            rules_count=self._rules_count,
            key_snapshot=keys,
            active=tasks.active() if tasks else [],
            recent=tasks.recent(20) if tasks else [],
        )

    def reload(self, *, sync: bool = False) -> None:
        """重新讀取快取概覽與規則數（背景執行緒），讀完更新畫面。"""
        if self._loading:
            return
        self._loading = True

        def work() -> None:
            try:
                overview = self._cache_overview_loader()
            except Exception:
                logger.warning("工作台讀取快取概覽失敗", exc_info=True)
                overview = None
            try:
                rules = self._rules_count_loader()
            except Exception:
                logger.warning("工作台讀取規則數失敗", exc_info=True)
                rules = None
            self._cache_overview, self._rules_count = overview, rules
            self._loading = False
            self._apply_on_ui(direct=sync)

        if sync:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    def _apply_on_ui(self, *, direct: bool = False) -> None:
        if direct:
            self.refresh_view(self._collect())
            return

        async def apply() -> None:
            self.refresh_view(self._collect())
            self._safe_update()

        try:
            self._page.run_task(apply)
        except Exception:  # noqa: BLE001 - 沒有 event loop（測試）就直接套用
            self.refresh_view(self._collect())

    def _on_tasks_changed(self) -> None:
        tasks = self._tasks
        active_ids = {task.id for task in tasks.active()} if tasks else set()
        with self._task_state_lock:
            crossed_task_boundary = active_ids != self._active_task_ids
            self._active_task_ids = active_ids
        if crossed_task_boundary:
            # 只在 active task membership 發生開始 / 結束邊界時重讀昂貴統計；
            # progress 事件只更新 UI 狀態。
            self.reload()
        self._apply_on_ui()

    def _safe_update(self) -> None:
        try:
            self.update()
        except (AssertionError, RuntimeError):
            pass  # 尚未加入頁面

    # -- 畫面 ----------------------------------------------------------------

    def refresh_view(self, data: DashboardData) -> None:
        self.data = data
        self.title_text.value = f"{greeting()}，歡迎回來"
        self.stat_cache.set_value(
            format_count(data.total_entries if self._cache_overview else None)
        )
        self.stat_cache.set_value(
            delta="翻譯快取總筆數" if self._cache_overview else "讀取中…"
        )
        self.stat_rules.set_value(
            format_count(data.rules_count),
            delta="replace_rules.json" if data.rules_count is not None else "讀取中…",
            delta_tone="neutral",
        )
        keys = data.keys
        if keys is not None:
            self.stat_keys.set_value(
                f"{keys.usable}/{keys.total}" if keys.total else "—",
                delta=keys.tooltip
                if keys.total == 0
                else (f"{keys.cooling} 把冷卻中" if keys.cooling else "全部正常"),
                delta_tone="neutral" if not keys.cooling else "gold",
            )
        self.stat_tasks.set_value(
            str(data.tasks_done + data.tasks_failed + data.tasks_running),
            delta=f"完成 {data.tasks_done}・進行中 {data.tasks_running}・失敗 {data.tasks_failed}",
            delta_tone="red" if data.tasks_failed else "neutral",
        )
        self.steps_row.controls = [
            self._step_tile(i, s) for i, s in enumerate(data.steps, 1)
        ]
        done = sum(1 for s in data.steps if s.status == STEP_DONE)
        self.step_badge.content = kit.chip(
            f"本次完成 {done} / {len(data.steps)}", "em" if done else "neutral"
        )
        self.cache_column.controls = [
            self._cache_bar(b, BAR_TONES[i % len(BAR_TONES)])
            for i, b in enumerate(data.cache_bars)
        ] or [
            kit.empty_state(
                "尚無快取資料",
                "翻譯後會在這裡顯示各類型的筆數",
                icon=ft.Icons.STORAGE_OUTLINED,
            )
        ]
        self.activity_column.controls = self._activity_rows(data) or [
            kit.empty_state(
                "本次還沒有任務",
                "到左側選一個流程開始，進度會顯示在這裡",
                icon=ft.Icons.HISTORY,
            )
        ]
        self.keys_column.controls = [self._key_row(k) for k in data.key_rows] or [
            kit.empty_state(
                "尚未設定 API Key",
                "到設定頁新增 Gemini API Key",
                icon=ft.Icons.KEY_OFF_OUTLINED,
            )
        ]

    def _step_tile(self, number: int, step: StepStatus) -> ft.Control:
        text, tone_name = STEP_STATUS_STYLE[step.status]
        active = step.status in (STEP_RUNNING, STEP_DONE)
        chip_text = (
            f"{text}　{step.detail}"
            if step.detail and step.status == STEP_RUNNING
            else text
        )
        return ft.Container(
            expand=1,
            height=158,
            padding=14,
            border_radius=design.RADIUS_CARD - 2,
            bgcolor=get_tone(tone_name).bg if active else C.PANEL2,
            border=ft.Border.all(1, get_tone(tone_name).line if active else C.LINE),
            ink=True,
            on_click=lambda _e, key=step.key: self._go(key),
            content=ft.Column(
                [
                    kit.tone_icon(
                        STEP_ICONS[step.key],
                        STEP_TONES[step.key],
                        size=18,
                        box=36,
                        radius=10,
                    ),
                    ft.Text(
                        f"STEP {number}",
                        size=10.5,
                        color=C.DIM,
                        font_family=design.FONT_MONO,
                    ),
                    ft.Text(
                        step.title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT
                    ),
                    ft.Text(step.desc, size=11.5, color=C.DIM),
                    ft.Row(
                        [
                            kit.chip(
                                chip_text, tone_name, dot=step.status == STEP_RUNNING
                            )
                        ]
                    ),
                ],
                spacing=4,
                tight=True,
            ),
            tooltip=f"前往 {step.title}",
        )

    @staticmethod
    def _cache_bar(bar: CacheBar, tone_name: str) -> ft.Control:
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(bar.label, size=12.5, color=C.TEXT, expand=True),
                        ft.Text(
                            format_count(bar.entries),
                            size=12,
                            color=C.MUTED,
                            font_family=design.FONT_MONO,
                        ),
                    ]
                ),
                kit.progress_bar(bar.share, tone_name, height=6),
            ],
            spacing=6,
        )

    def _activity_rows(self, data: DashboardData) -> list[ft.Control]:
        now = time.time()
        rows = []
        for task in data.activity:
            if task.running:
                tone_name, status = "gold", f"進行中 {task.percent}%"
            elif task.status == STATUS_ERROR:
                tone_name, status = "red", "失敗"
            else:
                tone_name, status = "em", "完成"
            ago = format_ago(now - (task.finished_at or task.started_at))
            rows.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=8, vertical=9),
                    border_radius=design.RADIUS_CONTROL,
                    ink=bool(task.view_key),
                    on_click=(lambda _e, key=task.view_key: self._go(key))
                    if task.view_key
                    else None,
                    content=ft.Row(
                        [
                            ft.Container(
                                width=9,
                                height=9,
                                border_radius=5,
                                border=ft.Border.all(2, get_tone(tone_name).fg),
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        task.name,
                                        size=13.5,
                                        weight=ft.FontWeight.W_600,
                                        color=C.TEXT,
                                    ),
                                    ft.Text(f"{status}・{ago}", size=11.5, color=C.DIM),
                                ],
                                spacing=1,
                                tight=True,
                                expand=True,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
        return rows

    @staticmethod
    def _key_row(key: KeyHealth) -> ft.Control:
        text, tone_name = KEY_STATUS_STYLE.get(key.status, ("正常", "em"))
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            bgcolor=C.PANEL2,
            border=ft.Border.all(1, C.LINE),
            border_radius=design.RADIUS_CONTROL,
            content=ft.Row(
                [
                    ft.Text(
                        f"Key #{key.index + 1}",
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT,
                    ),
                    ft.Text(
                        key.masked,
                        size=12,
                        color=C.MUTED,
                        font_family=design.FONT_MONO,
                        expand=True,
                    ),
                    kit.chip(text, tone_name),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    @property
    def page(self):
        return self._page
