"""app/shell/app_shell.py：App 外殼的組裝與導覽。

把側欄、頂列、狀態列、快速跳轉、鍵盤快捷鍵與 ``TaskManager`` 接起來，並負責切頁。
``main.py`` 只剩 runtime 初始化與 ``AppShell(page).mount()``。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections.abc import Callable

import flet as ft

from app import config_store
from app.config_apply import save_notice
from app.shell.config_effects import CacheRootReloader
from app.shell.palette import (
    KIND_ACTION,
    PaletteItem,
    close_palette,
    current_palette,
    page_items,
    show_palette,
)
from app.shell.resume_prompt import ResumePrompt
from app.shell.sidebar import SIDEBAR_WIDTH_COMPACT, Sidebar
from app.shell.statusbar import StatusBar
from app.ui.safe_file_picker import SafeFilePicker
from app.shell.task_manager import TaskInfo, TaskManager
from app.shell.topbar import TopBar
from app.ui import design
from app.ui.design import C
from app.ui.keyboard_shortcuts import create_keyboard_handler
from app.ui.snack import show_snack
from app.view_registry import (
    DEFAULT_VIEW_KEY,
    MIN_WINDOW_SIZE,
    SPECS_BY_KEY,
    build_view_registry,
    built_view,
    get_group,
    get_window_size,
    index_of,
)

logger = logging.getLogger("main_app")

APP_TITLE = "MC 繁化工坊"
COMPACT_BELOW_WIDTH = 1180  # 視窗比這個窄時，側欄收成只剩圖示
REFRESH_INTERVAL_SEC = 0.25  # 任務事件的 UI 更新節流
KEY_REFRESH_SEC = 5.0  # API Key 健康度輪詢間隔
CLOSE_WAIT_TIMEOUT_SEC = 2.0
CLOSE_WAIT_POLL_SEC = 0.05
# 快取資料夾變更後需要重新整理的頁面
_CACHE_DEPENDENT_VIEWS = ("dashboard", "cache")

# 時鐘與 sleep 抽成模組層級名稱，測試可以換成假的（不必真的等）
_monotonic = time.monotonic
_async_sleep = asyncio.sleep

# 這些頁面建立後，需要拿到 registry 才能互相切頁 / 通知
_REGISTRY_SETTERS = ("set_registry", "set_view_registry")
# 需要拿到外殼（導覽 / 任務事件）的頁面
_SHELL_SETTERS = ("set_shell",)
# 續跑時各種任務要回到的頁面（機器翻譯頁；FTB／KubeJS／MD 都在任務翻譯頁的分頁）
_RESUME_VIEW_BY_KIND = {
    "lm_directory": "lm",
    "ftbquests": "translation",
    "kubejs": "translation",
    "md": "translation",
}


def _default_cache_reload():
    """重載翻譯快取並重建搜尋索引（背景執行緒呼叫）。"""
    from app.services_impl.cache.cache_services import cache_reload_service

    return cache_reload_service()


def read_app_version() -> str:
    """從 pyproject.toml 讀版本號；讀不到就回傳空字串（狀態列不顯示）。"""
    try:
        import tomllib

        from translation_tool.utils.app_paths import get_resource_root

        data = tomllib.loads(
            (get_resource_root() / "pyproject.toml").read_text(encoding="utf-8")
        )
        return str(data.get("project", {}).get("version", ""))
    except Exception:  # noqa: BLE001 - 版本只是裝飾，讀不到不影響啟動
        return ""


class AppShell:
    """組裝整個 App 外殼。

    ``key_snapshot`` 與 ``config_loader`` 可注入，方便測試（預設讀真實設定）。

    **執行緒與生命週期契約（Flet 1.0 是單執行緒 async UI 模型）**

    - ``TaskManager`` 與 ``config_store`` 的訂閱者可能在任何執行緒被呼叫。AppShell 的訂閱者
      只負責「排程」：透過 ``page.run_task`` 把 UI 更新排到 page 的 event loop 上，並在那裡
      合併（coalesce）與節流；絕不在 callback 所在的執行緒直接改 Control 或呼叫 ``page.update()``。
    - mount 時註冊的全域資源（TaskSession observer、TaskManager / config 訂閱、Key 輪詢、
      待執行的更新工作）都由 :meth:`dispose` 統一移除；``page.on_close``（session 結束）會呼叫它。
      不綁 ``on_disconnect``：web client 可能只是暫時斷線，之後會重連。
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
        subscribe_config_paths: Callable[
            [Callable[[frozenset[str]], None]], Callable[[], None]
        ]
        | None = None,
        flush_before_close: Callable[[], object] | None = None,
        cache_reloader: CacheRootReloader | None = None,
        find_interrupted_tasks: Callable[[], object] | None = None,
    ) -> None:
        self.page = page
        self.file_picker = file_picker or SafeFilePicker()
        self._key_snapshot = key_snapshot or _default_key_snapshot
        self._config_loader = config_loader or _default_config_loader
        self.tasks = task_manager or TaskManager()
        if initial_mode is None:
            initial_mode = _default_mode()
        self.mode = initial_mode
        self._mode_saver = mode_saver or config_store.set_theme_mode
        self._subscribe_config = subscribe_config or config_store.subscribe
        self._subscribe_config_paths = (
            subscribe_config_paths or config_store.subscribe_paths
        )
        self._flush_before_close = flush_before_close or _default_flush_before_close
        self._unsubscribe_config: Callable[[], None] | None = None
        self._cache_reloader = cache_reloader or CacheRootReloader(
            is_busy=lambda: bool(self.tasks.active()),
            reload=_default_cache_reload,
            on_reloaded=self._on_cache_reloaded,
        )
        self._unsubscribe_config_paths: Callable[[], None] | None = None
        self._unsubscribe_tasks: Callable[[], None] | None = None
        self.current_key: str | None = None
        self._last_refresh = 0.0
        self._unread = False
        self._seen_recent: set[int] = set()

        # UI 排程狀態：訂閱者可能在任何執行緒進來，所以用小鎖保護（鎖內不呼叫 Flet、不 await）
        self._sched_lock = threading.Lock()
        self._disposed = False
        self._refresh_scheduled = False
        self._env_scheduled = False
        self._refresh_future = None
        self._env_future = None
        self._poll_future = None
        self._page_on_close = None
        self._previous_on_close = None
        self._window_on_event = None
        self._previous_window_on_event = None
        self._close_pending = False
        self._resume_prompt = _build_resume_prompt(
            page, self._resume_interrupted_task, find_interrupted_tasks
        )

        design.apply(page, initial_mode)

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
        # 合併而非覆蓋：避免洗掉呼叫端（例如開發截圖環境）已註冊的字型
        page.fonts = {
            **(getattr(page, "fonts", None) or {}),
            design.FONT_MONO: "fonts/JetBrainsMono-Regular.ttf",
        }
        page.on_keyboard_event = self.keyboard.handle_keyboard
        page.on_resize = self._on_resize
        self._bind_page_close()
        self._bind_window_close()

        self.tasks.attach()
        self._unsubscribe_tasks = self.tasks.subscribe(self._schedule_task_refresh)
        # 設定頁（或任何地方）存檔後，API Key 狀態 / 模型 / 資料夾要立刻更新
        self._unsubscribe_config = self._subscribe_config(self._on_config_saved)
        # 有任務在跑時存檔：提醒「進行中的任務不受影響」（#117）
        self._unsubscribe_config_paths = self._subscribe_config_paths(
            self._on_config_paths_saved
        )
        self.refresh_environment()

        page.add(self.build())
        # 套用首次載入時的可用寬度；Web 窄視窗不一定會在註冊 handler 後送出 resize。
        self._on_resize()
        self.navigate(start_view)
        self._schedule_key_poll()
        self._show_resume_prompt()

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

    def _show_resume_prompt(self) -> None:
        """啟動時偵測上次被中斷的機器翻譯並詢問使用者（不會自動開始任何任務）。"""
        try:
            self._resume_prompt.show_if_needed()
        except Exception:
            logger.warning("顯示續跑提示失敗", exc_info=True)

    def _resume_interrupted_task(self, task) -> None:
        """使用者確認續跑：切到對應的頁面（機器翻譯或任務翻譯），帶入上次的輸入與選項後開始。"""
        view_key = _RESUME_VIEW_BY_KIND.get(getattr(task, "kind", "lm_directory"), "lm")
        self.navigate(view_key)
        index = index_of(self.registry, view_key)
        view = built_view(self.registry[index]) if index >= 0 else None
        inner = getattr(view, "content", None) or view  # wrap_view 包了一層容器
        resume = getattr(inner, "resume_interrupted", None)
        if callable(resume):
            resume(task)
        else:
            logger.warning("頁面 %s 不支援續跑，無法帶入上次的任務", view_key)

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
        for name in _SHELL_SETTERS:
            setter = getattr(inner, name, None)
            if callable(setter):
                setter(self)

    # -- 主題 ----------------------------------------------------------------

    def set_mode(self, mode: str, *, persist: bool = True) -> None:
        """切換深 / 淺色，並（預設）記住使用者的選擇。"""
        mode = "dark" if mode == "dark" else "light"
        changed = mode != self.mode
        self.mode = mode
        self.page.theme_mode = design.THEME_MODES[mode]
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
        """設定存檔後（可能在任何執行緒）：只排程，真正的更新在 page event loop 上做。"""
        with self._sched_lock:
            if self._disposed or self._env_scheduled:
                return
            self._env_scheduled = True
        ok, future = self._submit_ui(self._apply_config_refresh)
        with self._sched_lock:
            if not ok:
                self._env_scheduled = False
            else:
                self._env_future = future

    def _on_config_paths_saved(self, changed_paths: frozenset[str]) -> None:
        """存檔後（可能在任何執行緒）：觸發設定的副作用，並依中央套用時機表提醒使用者。"""
        with self._sched_lock:
            if self._disposed:
                return
        # 快取資料夾變更：沒有任務時立即重載，有任務時等任務結束
        self._cache_reloader.on_config_paths(changed_paths)
        try:
            running = bool(self.tasks.active())
        except Exception:
            logger.debug("讀取進行中任務失敗", exc_info=True)
            running = False
        message = save_notice(changed_paths, tasks_running=running)
        if message is None:
            return

        async def notify() -> None:
            with self._sched_lock:
                if self._disposed:
                    return
            show_snack(self.page, message, C.DIA)

        self._submit_ui(notify)

    def _on_cache_reloaded(self, ok: bool) -> None:
        """快取重載結束（背景執行緒）：排程到 UI 執行緒刷新相關頁面並提示結果。"""

        async def refresh() -> None:
            with self._sched_lock:
                if self._disposed:
                    return
            if ok:
                for item in self.registry:
                    if item["key"] not in _CACHE_DEPENDENT_VIEWS:
                        continue
                    view_obj = built_view(item)
                    content = getattr(view_obj, "content", None)
                    # 工作台有 reload()；快取頁沒有公開的重載入口，重新掛載流程會重讀總覽
                    refresh_fn = getattr(content, "reload", None) or getattr(
                        content, "did_mount", None
                    )
                    if callable(refresh_fn):
                        try:
                            refresh_fn()
                        except Exception:
                            logger.debug("快取重載後刷新頁面失敗", exc_info=True)
                show_snack(
                    self.page, "快取資料夾已變更，已重新載入快取與搜尋索引", C.DIA
                )
            else:
                show_snack(
                    self.page,
                    "快取資料夾變更後重新載入失敗，請查看日誌（目前仍使用舊的快取）",
                    C.GOLD,
                )
            self._safe_update()

        self._submit_ui(refresh)

    async def _apply_config_refresh(self) -> None:
        with self._sched_lock:
            self._env_scheduled = False
            if self._disposed:
                return
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
        """任務事件（可能在任何執行緒、而且很密集）：合併成 page event loop 上的一個更新工作。

        已經有排程中的更新工作時直接返回（它執行時會讀到最新狀態），所以大量 progress 事件
        只會排一個工作；工作本身會依 ``REFRESH_INTERVAL_SEC`` 節流。
        """
        self._cache_reloader.poke()  # 任務結束後，等待中的快取重載才會開始
        with self._sched_lock:
            if self._disposed or self._refresh_scheduled:
                return
            self._refresh_scheduled = True
        ok, future = self._submit_ui(self._coalesced_refresh)
        with self._sched_lock:
            if not ok:
                self._refresh_scheduled = False
            else:
                self._refresh_future = future

    async def _coalesced_refresh(self) -> None:
        wait = REFRESH_INTERVAL_SEC - (_monotonic() - self._last_refresh)
        if wait > 0:
            await _async_sleep(wait)
        with self._sched_lock:
            self._refresh_scheduled = False  # 這之後的新事件會排新的工作
            if self._disposed:
                return
        self._last_refresh = _monotonic()
        self.refresh_tasks()
        self._safe_update()

    def _submit_ui(self, handler):
        """把 coroutine function 排到 page 的 event loop，回傳 ``(是否排成功, Future)``。

        失敗（例如 Page 已關閉）回傳 ``(False, None)``；成功時的 Future 留著給 dispose 取消。
        """
        try:
            return True, self.page.run_task(handler)
        except Exception:
            logger.debug("無法排程 UI 更新", exc_info=True)
            return False, None

    def _schedule_key_poll(self) -> None:
        """每隔幾秒更新一次 API Key 健康度（冷卻到期 / 額度用盡都會變）。"""
        _ok, self._poll_future = self._submit_ui(self._poll_keys)

    async def _poll_keys(self) -> None:
        while not self._disposed:
            await _async_sleep(KEY_REFRESH_SEC)
            if self._disposed:
                break
            self.refresh_keys()
            self._safe_update()

    # -- 生命週期 ------------------------------------------------------------

    def _bind_page_close(self) -> None:
        """session 結束（``page.on_close``）時 teardown；保留原本已掛的 handler。

        刻意不用 ``on_disconnect``：web client 暫時斷線後會重連，不能因此做不可逆的 teardown。
        """
        page = self.page
        previous = getattr(page, "on_close", None)
        self._previous_on_close = previous

        async def on_close(event=None) -> None:
            try:
                self.dispose()
            finally:
                await self._invoke_existing_handler_async(previous, event)

        self._page_on_close = on_close
        try:
            page.on_close = on_close
        except Exception:
            logger.debug("無法掛上 page.on_close", exc_info=True)

    def _invoke_existing_handler(self, handler, event=None) -> None:
        """保留既有 handler，且不遺失 async callback。"""
        if handler is None:
            return
        is_async = inspect.iscoroutinefunction(handler) or (
            callable(handler) and inspect.iscoroutinefunction(handler.__call__)
        )
        if is_async:

            async def invoke_async() -> None:
                try:
                    await handler(event)
                except Exception:
                    logger.debug("既有 async page handler 失敗", exc_info=True)

            self._submit_ui(invoke_async)
            return
        try:
            result = handler(event)
        except Exception:
            logger.debug("既有 page handler 失敗", exc_info=True)
            return
        if inspect.isawaitable(result):

            async def await_result() -> None:
                try:
                    await result
                except Exception:
                    logger.debug("既有 awaitable page handler 失敗", exc_info=True)

            self._submit_ui(await_result)

    async def _invoke_existing_handler_async(self, handler, event=None) -> None:
        """在 close event dispatch 內直接完成既有 handler chaining。"""
        if handler is None:
            return
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("既有 async page handler 失敗", exc_info=True)

    def _abort_close(self) -> None:
        """中止 close，但保留外殼時恢復接收新任務。"""
        self._close_pending = False
        if not self._disposed:
            self.tasks.resume_accepting()

    def _bind_window_close(self) -> None:
        """把桌面 native CLOSE 導向同一個 lifecycle teardown。"""
        window = getattr(self.page, "window", None)
        if window is None:
            return
        previous = getattr(window, "on_event", None)
        self._previous_window_on_event = previous
        try:
            window.prevent_close = True
        except Exception:
            logger.debug("無法啟用桌面視窗關閉攔截", exc_info=True)

        async def on_window_event(event=None) -> None:
            close_type = getattr(getattr(ft, "WindowEventType", None), "CLOSE", None)
            if close_type is None or getattr(event, "type", None) != close_type:
                self._invoke_existing_handler(previous, event)
                return
            if self._disposed:
                return
            if self.tasks.active() and not self._close_pending:
                self._show_close_confirmation()
                return
            if self._close_pending:
                return
            await self._complete_window_close()

        self._window_on_event = on_window_event
        try:
            window.on_event = on_window_event
        except Exception:
            logger.debug("無法掛上 desktop window event handler", exc_info=True)

    def _show_close_confirmation(self) -> None:
        """任務執行中先讓使用者選擇繼續或取消關閉。"""
        show_dialog = getattr(self.page, "show_dialog", None)
        if not callable(show_dialog):
            logger.warning("頁面不支援關閉確認對話框，保留視窗開啟")
            return

        def keep_running(_event=None) -> None:
            try:
                self.page.pop_dialog()
            except Exception:
                logger.debug("關閉確認取消失敗", exc_info=True)

        def confirm_close(_event=None) -> None:
            try:
                self.page.pop_dialog()
            except Exception:
                logger.debug("關閉確認 dialog 關閉失敗", exc_info=True)
            if self._close_pending or self._disposed:
                return
            self._close_pending = True
            self.tasks.request_cancel_active()
            ok, _future = self._submit_ui(self._complete_window_close)
            if not ok:
                self._abort_close()
                self._show_close_failure()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("仍有任務正在執行"),
            content=ft.Text("關閉視窗會中斷目前任務，尚未落盤的資料可能遺失。"),
            actions=[
                ft.TextButton("繼續執行", on_click=keep_running),
                ft.TextButton("仍要關閉", on_click=confirm_close),
            ],
        )
        show_dialog(dialog)

    async def _complete_window_close(self) -> None:
        """完成 desktop close：先 teardown，再讓 native window 結束。"""
        if self._disposed:
            return
        self.tasks.stop_accepting()
        close_ready = False
        try:
            self.tasks.request_cancel_active()
            deadline = _monotonic() + CLOSE_WAIT_TIMEOUT_SEC
            while self.tasks.active() and _monotonic() < deadline:
                await _async_sleep(CLOSE_WAIT_POLL_SEC)
            if self.tasks.active():
                self._show_close_failure()
                return
            result = self._flush_before_close()
            if inspect.isawaitable(result):
                result = await result
            if result is False:
                raise RuntimeError("close flush returned false")
            close_ready = True
        except Exception:
            logger.warning(
                "關閉前 drain/flush/checkpoint 失敗，保留視窗供重試", exc_info=True
            )
            self._show_close_failure()
            return
        finally:
            if not close_ready and not self._disposed:
                self._abort_close()
        self.dispose()
        destroy = getattr(getattr(self.page, "window", None), "destroy", None)
        if not callable(destroy):
            return
        try:
            result = destroy()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("無法銷毀 desktop window", exc_info=True)

    def _show_close_failure(self) -> None:
        show_dialog = getattr(self.page, "show_dialog", None)
        if not callable(show_dialog):
            return
        show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("無法安全關閉"),
                content=ft.Text("任務或資料寫入尚未完成，請稍後再試。"),
                actions=[
                    ft.TextButton(
                        "知道了", on_click=lambda _e=None: self.page.pop_dialog()
                    )
                ],
            )
        )

    def dispose(self) -> None:
        """移除 mount 時註冊的全域資源（冪等）。

        - TaskSession 全域 observer（``TaskManager.detach``）與 TaskManager / config 訂閱
        - Key 輪詢與待執行的更新工作（取消 Future；晚到的 callback 看到 ``_disposed`` 就不更新 UI）
        - 還掛著我們自己的 page 事件 handler
        """
        with self._sched_lock:
            if self._disposed:
                return
            self._disposed = True
            self._close_pending = False
            futures = [self._refresh_future, self._env_future, self._poll_future]
            self._refresh_future = self._env_future = self._poll_future = None
            unsubscribers = [
                self._unsubscribe_tasks,
                self._unsubscribe_config,
                self._unsubscribe_config_paths,
            ]
            self._unsubscribe_tasks = self._unsubscribe_config = None
            self._unsubscribe_config_paths = None
        for unsubscribe in unsubscribers:
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception:
                    logger.debug("取消訂閱失敗", exc_info=True)
        self.tasks.detach()
        for future in futures:
            if future is not None:
                try:
                    future.cancel()
                except Exception:
                    logger.debug("取消排程工作失敗", exc_info=True)
        page = self.page
        try:
            if page.on_keyboard_event == self.keyboard.handle_keyboard:
                page.on_keyboard_event = None
            if page.on_resize == self._on_resize:
                page.on_resize = None
            if getattr(page, "on_close", None) is self._page_on_close:
                page.on_close = self._previous_on_close
            window = getattr(page, "window", None)
            if (
                window is not None
                and getattr(window, "on_event", None) is self._window_on_event
            ):
                window.on_event = self._previous_window_on_event
                window.prevent_close = False
        except Exception:
            logger.debug("還原 page handler 失敗", exc_info=True)

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


def _build_resume_prompt(page, on_resume, find_tasks=None) -> ResumePrompt:
    """建立啟動時的續跑提示（引擎相依由 service 層提供；``find_tasks`` 可注入給測試）。"""
    from app.services_impl import resume_service

    return ResumePrompt(
        page,
        find_tasks=find_tasks or resume_service.find_interrupted_tasks,
        check_resume=resume_service.check_resume,
        discard=resume_service.discard_interrupted,
        on_resume=on_resume,
    )


def _default_flush_before_close() -> bool:
    """flush 共用 log buffer；各 writer 必須在自身 completion 前完成落盤。"""
    from app.services_impl.logging_service import GLOBAL_LOG_LIMITER

    GLOBAL_LOG_LIMITER.flush()
    return True


def _default_mode() -> str:
    try:
        return config_store.get_theme_mode()
    except Exception:  # noqa: BLE001 - 設定壞掉時用預設深色
        return config_store.DEFAULT_THEME_MODE


def _default_key_snapshot() -> list:
    from app.services_impl.key_health_service import get_key_health_snapshot

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
