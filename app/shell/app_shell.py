"""app/shell/app_shell.py：App 外殼的組裝與導覽。

把側欄、頂列、狀態列、快速跳轉、鍵盤快捷鍵與 ``TaskManager`` 接起來，並負責切頁。
``main.py`` 只剩 runtime 初始化與 ``AppShell(page).mount()``。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import flet as ft

from app import config_store
from app.shell.palette import (
    KIND_ACTION,
    PaletteItem,
    close_palette,
    current_palette,
    page_items,
    show_palette,
)
from app.shell.sidebar import SIDEBAR_WIDTH_COMPACT, Sidebar
from app.shell.statusbar import StatusBar
from app.shell.task_manager import TaskInfo, TaskManager
from app.shell.topbar import TopBar
from app.ui import design, theme
from app.ui.keyboard_shortcuts import create_keyboard_handler
from app.view_registry import (
    DEFAULT_VIEW_KEY,
    MIN_WINDOW_SIZE,
    SPECS_BY_KEY,
    build_view_registry,
    get_group,
    get_window_size,
    index_of,
)

logger = logging.getLogger("main_app")

APP_TITLE = "MC 繁化工坊"
COMPACT_BELOW_WIDTH = 1180  # 視窗比這個窄時，側欄收成只剩圖示
REFRESH_INTERVAL_SEC = 0.25  # 任務事件的 UI 更新節流
KEY_REFRESH_SEC = 5.0  # API Key 健康度輪詢間隔

# 這些頁面建立後，需要拿到 registry 才能互相切頁 / 通知
_REGISTRY_SETTERS = ("set_registry", "set_view_registry")


def read_app_version() -> str:
    """從 pyproject.toml 讀版本號；讀不到就回傳空字串（狀態列不顯示）。"""
    try:
        import tomllib
        from pathlib import Path

        data = tomllib.loads(
            (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(
                encoding="utf-8"
            )
        )
        return str(data.get("project", {}).get("version", ""))
    except Exception:  # noqa: BLE001 - 版本只是裝飾，讀不到不影響啟動
        return ""


class AppShell:
    """組裝整個 App 外殼。

    ``key_snapshot`` 與 ``config_loader`` 可注入，方便測試（預設讀真實設定）。
    """

    def __init__(
        self,
        page: ft.Page,
        *,
        file_picker: ft.FilePicker | None = None,
        key_snapshot: Callable[[], list] | None = None,
        config_loader: Callable[[], dict] | None = None,
        task_manager: TaskManager | None = None,
        initial_mode: str | None = None,
        mode_saver: Callable[[str], object] | None = None,
        subscribe_config: Callable[[Callable[[], None]], Callable[[], None]]
        | None = None,
    ) -> None:
        self.page = page
        self.file_picker = file_picker or ft.FilePicker()
        self._key_snapshot = key_snapshot or _default_key_snapshot
        self._config_loader = config_loader or _default_config_loader
        self.tasks = task_manager or TaskManager()
        if initial_mode is None:
            initial_mode = _default_mode()
        self.mode = initial_mode
        self._mode_saver = mode_saver or config_store.set_theme_mode
        self._subscribe_config = subscribe_config or config_store.subscribe
        self._unsubscribe_config: Callable[[], None] | None = None
        self.current_key: str | None = None
        self._last_refresh = 0.0
        self._refresh_timer: threading.Timer | None = None
        self._unread = False
        self._seen_recent: set[int] = set()

        design.apply(page, initial_mode)
        theme.manager.set_mode(initial_mode)

        self.registry = build_view_registry(page, self.file_picker)
        for item in self.registry:
            item.on_build(self._wire_registry)

        self.sidebar = Sidebar(
            on_select=self.navigate,
            on_search=self.open_palette,
            on_theme=self.set_mode,
            mode=initial_mode,
        )
        self.topbar = TopBar(
            on_task_click=self._open_task_view,
            on_api_click=lambda: self.navigate("config"),
            on_recent_click=self._open_task_view,
        )
        self.statusbar = StatusBar(version=read_app_version())
        self.content_area = ft.Container(expand=True)

        self.keyboard = create_keyboard_handler(
            page, self.registry, self.navigate_index
        )
        self.keyboard.set_search_callback(lambda _e=None: self.open_palette())
        self.keyboard.set_current_view_getter(lambda: self.content_area.content)
        self.keyboard.set_palette_getter(lambda: current_palette(page))

    # -- 組裝 ----------------------------------------------------------------

    def build(self) -> ft.Control:
        """外殼的整體版面（側欄 | 頂列 / 內容 / 狀態列）。"""
        return ft.Row(
            [
                self.sidebar,
                ft.Column(
                    [self.topbar, self.content_area, self.statusbar],
                    spacing=0,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )

    def mount(self, start_view: str = DEFAULT_VIEW_KEY) -> None:
        """設定視窗、掛上版面、開始接收任務事件並顯示首頁。"""
        page = self.page
        page.title = APP_TITLE
        width, height = get_window_size()
        try:
            page.window.width = width
            page.window.height = height
            page.window.min_width, page.window.min_height = MIN_WINDOW_SIZE
        except Exception:
            logger.debug("無法設定視窗大小", exc_info=True)
        page.padding = 0
        page.fonts = {design.FONT_MONO: "fonts/JetBrainsMono-Regular.ttf"}
        page.on_keyboard_event = self.keyboard.handle_keyboard
        page.on_resize = self._on_resize

        self.tasks.attach()
        self.tasks.subscribe(self._schedule_task_refresh)
        # 設定頁（或任何地方）存檔後，API Key 狀態 / 模型 / 資料夾要立刻更新
        self._unsubscribe_config = self._subscribe_config(self._on_config_saved)
        self.refresh_environment()

        page.add(self.build())
        self.navigate(start_view)
        self._schedule_key_poll()

    # -- 導覽 ----------------------------------------------------------------

    def navigate(self, view_key: str) -> None:
        """切到指定頁面（key 不存在時記錄警告並忽略）。"""
        index = index_of(self.registry, view_key)
        if index < 0:
            logger.warning("未知的頁面：%s", view_key)
            return
        self.navigate_index(index)

    def navigate_index(self, index: int) -> None:
        if not 0 <= index < len(self.registry):
            return
        item = self.registry[index]
        key = item["key"]
        spec = SPECS_BY_KEY[key]
        self.content_area.content = item["view"]
        self.current_key = key
        self.sidebar.set_selected(key)
        group = get_group(spec.group)
        self.topbar.set_breadcrumb(
            None if spec.group == "system" else group.label, spec.label
        )
        self.page.title = f"{APP_TITLE} — {spec.label}"
        self._safe_update()

    def _open_task_view(self, view_key: str | None) -> None:
        if view_key:
            self.navigate(view_key)

    def _wire_registry(self, view) -> None:
        """頁面第一次建立後，把 registry 交給需要它的頁面。"""
        inner = getattr(view, "content", view)
        for name in _REGISTRY_SETTERS:
            setter = getattr(inner, name, None)
            if callable(setter):
                setter(self.registry)

    # -- 主題 ----------------------------------------------------------------

    def set_mode(self, mode: str, *, persist: bool = True) -> None:
        """切換深 / 淺色，並（預設）記住使用者的選擇。"""
        mode = "dark" if mode == "dark" else "light"
        changed = mode != self.mode
        self.mode = mode
        self.page.theme_mode = design.THEME_MODES[mode]
        theme.manager.set_mode(mode)
        self.sidebar.set_mode(mode)
        self._safe_update()
        if persist and changed:
            try:
                self._mode_saver(mode)
            except Exception:
                logger.warning("無法記住主題偏好", exc_info=True)

    def toggle_mode(self) -> None:
        self.set_mode(design.toggled_mode(self.page))

    # -- 快速跳轉 -------------------------------------------------------------

    def palette_items(self) -> list[PaletteItem]:
        actions = [
            PaletteItem(
                id="action:theme",
                label="切換深色 / 淺色主題",
                kind=KIND_ACTION,
                sub="外觀",
                icon=ft.Icons.CONTRAST,
                keywords=("theme", "dark", "light", "主題", "外觀"),
                run=self.toggle_mode,
            ),
            PaletteItem(
                id="action:api",
                label="檢查 API Key 狀態",
                kind=KIND_ACTION,
                sub="前往設定",
                icon=ft.Icons.KEY,
                keywords=("key", "api", "gemini", "額度"),
                run=lambda: self.navigate("config"),
            ),
        ]
        return [*page_items(self.navigate), *actions]

    def open_palette(self) -> None:
        show_palette(self.page, self.palette_items())

    def close_palette(self) -> None:
        close_palette(self.page)

    # -- 全域狀態（任務 / API / 狀態列）------------------------------------------

    def refresh_environment(self) -> None:
        """讀設定與 Key 狀態，更新頂列 / 狀態列。"""
        self.refresh_keys()
        try:
            config = self._config_loader() or {}
        except Exception:
            logger.warning("外殼讀取設定失敗", exc_info=True)
            config = {}
        self.statusbar.set_model(_enabled_model_name(config))
        self.statusbar.set_workdir(_cache_dir_of(config))

    def _on_config_saved(self) -> None:
        """設定存檔後（可能在背景執行緒）：重讀環境資訊。"""
        self.refresh_environment()
        self._safe_update()

    def refresh_keys(self) -> None:
        try:
            snapshot = self._key_snapshot()
        except Exception:
            logger.debug("讀取 Key 狀態失敗", exc_info=True)
            snapshot = []
        self.topbar.set_keys(snapshot)

    def refresh_tasks(self) -> None:
        """依 TaskManager 更新任務膠囊、側欄徽章、狀態列與通知。"""
        current = self.tasks.current()
        self.topbar.set_task(current)
        running_views = {t.view_key for t in self.tasks.active() if t.view_key}
        for key in self.sidebar.items:
            self.sidebar.set_badge(key, "•" if key in running_views else None, "gold")
        if current is not None:
            self.statusbar.set_status(f"執行中：{current.name}", "gold")
        else:
            recent = self.tasks.recent(1)
            failed = bool(recent) and recent[0].status == "error"
            self.statusbar.set_status(
                "上次任務失敗" if failed else "就緒", "red" if failed else "em"
            )
        recent_tasks = self.tasks.recent()
        fresh = {t.id for t in recent_tasks} - self._seen_recent
        if fresh and self.current_key is not None:
            self._unread = True
        self._seen_recent |= fresh
        self.topbar.set_recent(recent_tasks, unread=self._unread)

    def mark_notifications_read(self) -> None:
        self._unread = False
        self.topbar.set_recent(self.tasks.recent(), unread=False)

    def _schedule_task_refresh(self) -> None:
        """任務事件可能很密集（每個項目一次）：合併成最多每 0.25 秒更新一次 UI。"""
        now = time.monotonic()
        wait = REFRESH_INTERVAL_SEC - (now - self._last_refresh)
        if wait <= 0:
            self._last_refresh = now
            self._refresh_now()
            return
        if self._refresh_timer is None or not self._refresh_timer.is_alive():
            self._refresh_timer = threading.Timer(wait, self._flush_refresh)
            self._refresh_timer.daemon = True
            self._refresh_timer.start()

    def _flush_refresh(self) -> None:
        self._last_refresh = time.monotonic()
        self._refresh_now()

    def _refresh_now(self) -> None:
        self.refresh_tasks()
        self._safe_update()

    def _schedule_key_poll(self) -> None:
        """每隔幾秒更新一次 API Key 健康度（冷卻到期 / 額度用盡都會變）。"""

        async def poll() -> None:
            import asyncio

            while True:
                await asyncio.sleep(KEY_REFRESH_SEC)
                self.refresh_keys()
                self._safe_update()

        try:
            self.page.run_task(poll)
        except Exception:
            logger.debug("未啟動 Key 輪詢", exc_info=True)

    # -- 視窗 ----------------------------------------------------------------

    def _on_resize(self, _e=None) -> None:
        try:
            width = self.page.width or self.page.window.width or 0
        except Exception:  # noqa: BLE001
            return
        compact = 0 < width < COMPACT_BELOW_WIDTH
        if compact != self.sidebar.compact:
            self.sidebar.set_compact(compact)
            self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except Exception:
            logger.debug("page.update 失敗", exc_info=True)


# -- 設定 / Key 的預設來源 -------------------------------------------------------


def _default_mode() -> str:
    try:
        return config_store.get_theme_mode()
    except Exception:  # noqa: BLE001 - 設定壞掉時用預設深色
        return config_store.DEFAULT_THEME_MODE


def _default_key_snapshot() -> list:
    from translation_tool.core.lm_config_rules import get_key_health_snapshot

    return get_key_health_snapshot()


def _default_config_loader() -> dict:
    from translation_tool.utils.config_manager import load_config

    return load_config()


def _enabled_model_name(config: dict) -> str | None:
    """設定中第一個啟用的模型名稱。"""
    models = (config.get("lm_translator") or {}).get("models") or {}
    for name, cfg in models.items():
        if isinstance(cfg, dict) and cfg.get("enabled"):
            return str(name)
    return None


def _cache_dir_of(config: dict) -> str | None:
    """狀態列顯示用的快取資料夾（設定沒有就不顯示）。"""
    value = (config.get("translator") or {}).get("cache_directory")
    return str(value) if value else None


__all__ = ["SIDEBAR_WIDTH_COMPACT", "AppShell", "TaskInfo", "read_app_version"]
