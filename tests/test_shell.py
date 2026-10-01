"""app/shell：側欄、頂列、快速跳轉、外殼導覽（不需要真的 Flet Page）。"""

from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

from app import view_registry as vr
from app.shell import palette as pal
from app.shell.app_shell import AppShell, _cache_dir_of, _enabled_model_name
from app.shell.sidebar import SIDEBAR_WIDTH, SIDEBAR_WIDTH_COMPACT, Sidebar
from app.shell.task_manager import TaskManager
from app.shell.topbar import ApiStatusPill, TaskPill, TopBar, summarize_keys
from app.views._log.task_session import TaskSession
from translation_tool.core.lm_key_health import (
    STATUS_COOLING,
    STATUS_OK,
    STATUS_PROBING,
    KeyHealth,
)


def _key(index: int, status: str = STATUS_OK) -> KeyHealth:
    return KeyHealth(
        index=index,
        masked="AIza••••abcd",
        status=status,
        reason="rpd" if status != STATUS_OK else None,
        seconds_remaining=60.0 if status == STATUS_COOLING else 0.0,
        failures=0 if status == STATUS_OK else 1,
    )


class FakePage:
    """外殼需要的 Page 介面（只實作用到的部分）。"""

    def __init__(self) -> None:
        self.overlay: list = []
        self.controls: list = []
        self.updated = 0
        self.title = ""
        self.theme_mode = ft.ThemeMode.LIGHT
        self.bgcolor = None
        self.theme = None
        self.dark_theme = None
        self.padding = None
        self.width = 1440
        self.window = SimpleNamespace(width=0, height=0, min_width=0, min_height=0)
        self.tasks: list = []
        self.on_keyboard_event = None
        self.on_resize = None

    def update(self, *_a, **_k) -> None:
        self.updated += 1

    def add(self, *controls) -> None:
        self.controls.extend(controls)

    def run_task(self, handler, *args) -> None:
        self.tasks.append(handler)


@pytest.fixture
def placeholder_views(monkeypatch):
    """頁面一律用佔位控制項，避免測試去建立真正的大型頁面。"""
    built: dict[str, ft.Control] = {}

    def fake_import(key, page, file_picker):
        view = ft.Text(f"view:{key}")
        built[key] = view
        return view

    monkeypatch.setattr(vr, "_lazy_import_view", fake_import)
    return built


class _Env:
    """外殼的外部相依（設定存檔、設定異動訂閱）替身，確保測試不會碰真的 config.json。"""

    def __init__(self) -> None:
        self.saved_modes: list[str] = []
        self.listeners: list = []
        self.snapshot = [_key(0), _key(1, STATUS_COOLING)]
        self.config = {
            "lm_translator": {
                "models": {"gemini-x": {"enabled": True}, "old": {"enabled": False}}
            },
            "translator": {"cache_directory": "快取資料"},
        }

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def fire_config_saved(self) -> None:
        for callback in list(self.listeners):
            callback()


def _make_shell(env: _Env, **overrides) -> AppShell:
    kwargs = {
        "file_picker": SimpleNamespace(),
        "key_snapshot": lambda: env.snapshot,
        "config_loader": lambda: env.config,
        "task_manager": TaskManager(),
        "initial_mode": "dark",
        "mode_saver": env.saved_modes.append,
        "subscribe_config": env.subscribe,
    }
    kwargs.update(overrides)
    return AppShell(FakePage(), **kwargs)


@pytest.fixture
def env():
    return _Env()


@pytest.fixture
def shell(placeholder_views, env):
    s = _make_shell(env)
    s.mount()
    yield s
    s.tasks.detach()


# -- 快速跳轉：搜尋與排序 ------------------------------------------------------


def _items() -> list[pal.PaletteItem]:
    return pal.page_items(lambda key: None)


def test_empty_query_returns_everything_in_order():
    items = _items()
    assert pal.filter_items(items, "") == items
    assert pal.filter_items(items, "   ") == items


def test_label_prefix_beats_contains_beats_keyword():
    ranked = pal.filter_items(_items(), "翻譯")
    labels = [i.label for i in ranked]
    assert labels[0] in {"翻譯校對"}  # 開頭符合
    assert "機器翻譯" in labels and "任務翻譯" in labels  # 包含
    assert labels.index("翻譯校對") < labels.index("機器翻譯")


def test_keywords_and_key_names_are_searchable():
    assert [i.label for i in pal.filter_items(_items(), "gemini")] == ["機器翻譯"]
    assert pal.filter_items(_items(), "pipeline")[0].label == "一鍵流水線"
    assert [i.label for i in pal.filter_items(_items(), "CACHE")] == ["快取管理"]


def test_search_matches_group_name_and_multi_word():
    labels = {i.label for i in pal.filter_items(_items(), "資料庫")}
    assert labels == {"快取管理", "替換規則", "學名查詢"}
    assert pal.filter_items(_items(), "zzzz-not-found") == []


def test_every_page_item_runs_its_callback_with_the_view_key():
    opened: list[str] = []
    for item in pal.page_items(opened.append):
        item.run()
    assert opened == [spec.key for spec in vr.VIEW_SPECS]


# -- 快速跳轉：面板本體 --------------------------------------------------------


def test_palette_selection_wraps_and_runs_item():
    ran: list[str] = []
    closed: list[bool] = []
    items = [
        pal.PaletteItem("a", "甲", run=lambda: ran.append("a")),
        pal.PaletteItem("b", "乙", run=lambda: ran.append("b")),
    ]
    p = pal.CommandPalette(items, on_close=lambda: closed.append(True))
    assert p.selected.id == "a"
    p.move(1)
    assert p.selected.id == "b"
    p.move(1)  # 繞回第一個
    assert p.selected.id == "a"
    p.move(-1)  # 往上繞到最後
    assert p.selected.id == "b"
    p.run_selected()
    assert ran == ["b"] and closed == [True]


def test_palette_query_resets_selection_and_handles_no_results():
    items = [pal.PaletteItem("a", "甲"), pal.PaletteItem("b", "乙")]
    p = pal.CommandPalette(items, on_close=lambda: None)
    p.move(1)
    p.set_query("乙")
    assert [i.id for i in p.results] == ["b"] and p.index == 0
    p.set_query("找不到")
    assert p.results == [] and p.selected is None
    p.run_selected()  # 沒有結果時 Enter 不會出錯


def test_palette_keyboard_handling():
    closed: list[bool] = []
    p = pal.CommandPalette(
        [pal.PaletteItem("a", "甲"), pal.PaletteItem("b", "乙")],
        on_close=lambda: closed.append(True),
    )
    assert p.handle_key("Arrow Down") is True and p.selected.id == "b"
    assert p.handle_key("Arrow Up") is True and p.selected.id == "a"
    assert p.handle_key("Escape") is True and closed == [True]
    assert p.handle_key("A") is False  # 一般字元交給輸入框


def test_palette_height_follows_result_count():
    items = [pal.PaletteItem(str(i), f"項目{i}") for i in range(30)]
    p = pal.CommandPalette(items, on_close=lambda: None)
    full = p.list_box.height
    p.set_query("項目1")  # 只剩 11 筆，仍受 MAX_VISIBLE 限制
    p.set_query("項目29")
    assert p.list_box.height < full


def test_show_and_close_palette_manage_overlay():
    page = FakePage()
    first = pal.show_palette(page, _items())
    assert pal.current_palette(page) is first
    assert pal.show_palette(page, _items()) is first  # 不會疊兩層
    assert len(page.overlay) == 1
    pal.close_palette(page)
    assert page.overlay == [] and pal.current_palette(page) is None
    pal.close_palette(page)  # 沒開著時關閉不出錯


# -- 頂列 ----------------------------------------------------------------------


def test_summarize_keys_cases():
    assert summarize_keys([]).tone == "neutral"
    assert summarize_keys([]).text == "未設定 Key"

    ok = summarize_keys([_key(0), _key(1)])
    assert (ok.tone, ok.text, ok.usable, ok.cooling) == ("em", "2/2 Key", 2, 0)

    partial = summarize_keys([_key(0), _key(1, STATUS_COOLING), _key(2)])
    assert (partial.tone, partial.text, partial.cooling) == ("gold", "2/3 Key", 1)

    dead = summarize_keys([_key(0, STATUS_COOLING)])
    assert (dead.tone, dead.text, dead.usable) == ("red", "0/1 Key", 0)


def test_probing_key_counts_as_usable():
    summary = summarize_keys([_key(0, STATUS_PROBING)])
    assert summary.usable == 1 and summary.tone == "em"


def test_api_pill_clicks_through():
    clicked: list[bool] = []
    pill = ApiStatusPill(on_click=lambda: clicked.append(True))
    pill.set_keys([_key(0), _key(1, STATUS_COOLING)])
    assert pill.count_text.value == "1/2 Key"
    assert pill.tooltip
    pill.on_click(None)
    assert clicked == [True]


def test_task_pill_shows_only_when_a_task_is_running():
    manager = TaskManager()
    manager.attach()
    try:
        opened: list = []
        pill = TaskPill(on_click=opened.append)
        assert pill.visible is False

        session = TaskSession(name="機器翻譯", view_key="lm")
        session.start()
        session.set_progress(0.42)
        pill.set_task(manager.current())
        assert (
            pill.visible
            and pill.name.value == "機器翻譯"
            and pill.percent.value == "42%"
        )
        pill.on_click(None)
        assert opened == ["lm"]

        session.finish()
        pill.set_task(manager.current())
        assert pill.visible is False
    finally:
        manager.detach()


def test_topbar_breadcrumb_hides_group_for_system_pages():
    bar = TopBar()
    bar.set_breadcrumb("工作流程", "機器翻譯")
    assert bar.group_text.value == "工作流程" and bar.separator.visible
    bar.set_breadcrumb(None, "設定")
    assert not bar.separator.visible and bar.page_text.value == "設定"


def test_topbar_recent_list_and_unread_dot():
    bar = TopBar()
    bar.set_recent([])
    assert bar.bell_dot.visible is False
    manager = TaskManager()
    manager.attach()
    try:
        ok, bad = TaskSession(name="成功任務"), TaskSession(name="失敗任務")
        ok.start()
        ok.finish()
        bad.start()
        bad.set_error()
        bad.finish()
        bar.set_recent(manager.recent(), unread=True)
    finally:
        manager.detach()
    assert bar.bell_dot.visible is True
    assert len(bar.bell_menu.items) == 2


# -- 側欄 ----------------------------------------------------------------------


def _sidebar(**kwargs) -> tuple[Sidebar, list]:
    events: list = []
    sb = Sidebar(
        on_select=lambda k: events.append(("select", k)),
        on_search=lambda: events.append(("search",)),
        on_theme=lambda m: events.append(("theme", m)),
        **kwargs,
    )
    return sb, events


def test_sidebar_lists_every_page_once():
    sb, _ = _sidebar()
    assert set(sb.items) == {spec.key for spec in vr.VIEW_SPECS}
    assert sb.width == SIDEBAR_WIDTH


def test_sidebar_selection_is_exclusive_and_validates_key():
    sb, _ = _sidebar()
    sb.set_selected("lm")
    sb.set_selected("qc")
    assert [k for k, item in sb.items.items() if item.selected] == ["qc"]
    assert sb.selected == "qc"
    with pytest.raises(KeyError):
        sb.set_selected("nope")


def test_sidebar_click_reports_the_page_key():
    sb, events = _sidebar()
    sb.items["cache"].on_click(None)
    sb.search_button.on_click(None)
    assert events == [("select", "cache"), ("search",)]


def test_sidebar_badges_and_clearing():
    sb, _ = _sidebar()
    sb.set_badge("lm", 3)
    assert sb.items["lm"]._badge_slot.content is not None
    sb.set_badge("lm", None)
    assert sb.items["lm"]._badge_slot.content is None
    sb.set_badge("qc", "•", "gold")
    assert sb.items["qc"]._badge_slot.content is not None
    sb.set_badge("qc", 0)
    assert sb.items["qc"]._badge_slot.content is None
    sb.set_badge("not-a-page", 5)  # 未知頁面不出錯


def test_sidebar_compact_keeps_selection_and_badges():
    sb, _ = _sidebar()
    sb.set_selected("rules")
    sb.set_badge("qc", 28, "red")
    sb.set_compact(True)
    assert sb.width == SIDEBAR_WIDTH_COMPACT
    assert sb.items["rules"].selected
    assert sb.items["qc"].compact and sb.items["qc"].tooltip == "QC 檢驗"
    sb.set_compact(False)
    assert sb.width == SIDEBAR_WIDTH and sb.items["qc"].tooltip is None


def test_sidebar_theme_toggle_reports_mode():
    sb, events = _sidebar(mode="dark")
    assert sb.theme_toggle.value == "dark"
    sb.theme_toggle.select("light", notify=True)
    assert events == [("theme", "light")]
    sb.set_mode("dark")  # 同步外觀，不觸發 callback
    assert events == [("theme", "light")]


# -- 外殼 ----------------------------------------------------------------------


def test_mount_shows_default_page_and_environment(shell, placeholder_views):
    assert shell.current_key == vr.DEFAULT_VIEW_KEY
    assert shell.page.title.endswith("一鍵流水線")
    assert shell.page.controls  # 版面已加入
    assert shell.page.window.width == vr.DEFAULT_WINDOW_SIZE[0]
    assert shell.sidebar.selected == "pipeline"
    assert shell.topbar.group_text.value == "工作流程"
    # 只建立了首頁（延遲載入）
    assert set(placeholder_views) == {"pipeline"}
    # 環境資訊
    assert shell.topbar.api_pill.count_text.value == "1/2 Key"
    assert shell.statusbar.model.value == "gemini-x"
    assert shell.statusbar.workdir.value == "快取資料"


def test_navigate_switches_content_sidebar_and_breadcrumb(shell, placeholder_views):
    shell.navigate("config")
    assert shell.current_key == "config"
    assert shell.content_area.content.content is placeholder_views["config"]
    assert shell.sidebar.selected == "config"
    assert shell.topbar.group_text.visible is False  # 設定不屬於任何分組
    assert shell.topbar.page_text.value == "設定"
    assert "設定" in shell.page.title


def test_navigate_unknown_key_is_ignored(shell):
    before = shell.current_key
    shell.navigate("nope")
    shell.navigate_index(999)
    shell.navigate_index(-1)
    assert shell.current_key == before


def test_ctrl_digit_shortcuts_follow_view_specs(shell):
    for spec in vr.VIEW_SPECS:
        if not spec.shortcut:
            continue
        shell.keyboard.handle_keyboard(
            SimpleNamespace(
                key=spec.shortcut, ctrl=True, meta=False, shift=False, alt=False
            )
        )
        assert shell.current_key == spec.key, spec.key


def test_ctrl_p_opens_palette_and_escape_closes_it(shell):
    def press(key, ctrl=False):
        shell.keyboard.handle_keyboard(
            SimpleNamespace(key=key, ctrl=ctrl, meta=False, shift=False, alt=False)
        )

    press("P", ctrl=True)
    palette = pal.current_palette(shell.page)
    assert palette is not None
    press("Arrow Down")
    assert palette.index == 1
    press("Escape")
    assert pal.current_palette(shell.page) is None


def test_palette_can_navigate_and_toggle_theme(shell):
    labels = {i.label for i in shell.palette_items()}
    assert {spec.label for spec in vr.VIEW_SPECS} <= labels
    assert "切換深色 / 淺色主題" in labels

    target = next(i for i in shell.palette_items() if i.id == "page:merge")
    target.run()
    assert shell.current_key == "merge"

    mode_before = shell.mode
    next(i for i in shell.palette_items() if i.id == "action:theme").run()
    assert shell.mode != mode_before


def test_set_mode_updates_page_and_sidebar(shell):
    shell.set_mode("light")
    assert shell.page.theme_mode == ft.ThemeMode.LIGHT
    assert shell.sidebar.theme_toggle.value == "light"
    shell.set_mode("dark")
    assert shell.page.theme_mode == ft.ThemeMode.DARK
    shell.toggle_mode()
    assert shell.page.theme_mode == ft.ThemeMode.LIGHT


def test_running_task_shows_in_pill_badge_and_statusbar(shell):
    session = TaskSession(name="機器翻譯", view_key="lm")
    session.start()
    shell.refresh_tasks()
    assert shell.topbar.task_pill.visible
    assert shell.sidebar.items["lm"]._badge_slot.content is not None
    assert "機器翻譯" in shell.statusbar.status.value

    session.finish()
    shell.refresh_tasks()
    assert not shell.topbar.task_pill.visible
    assert shell.sidebar.items["lm"]._badge_slot.content is None
    assert shell.statusbar.status.value == "就緒"
    assert shell.topbar.bell_dot.visible  # 有新完成的任務 → 未讀

    shell.mark_notifications_read()
    assert not shell.topbar.bell_dot.visible


def test_failed_task_marks_statusbar(shell):
    session = TaskSession(name="翻譯")
    session.start()
    session.set_error()
    session.finish()
    shell.refresh_tasks()
    assert shell.statusbar.status.value == "上次任務失敗"


def test_task_pill_click_returns_to_the_tasks_page(shell):
    session = TaskSession(name="機器翻譯", view_key="lm")
    session.start()
    shell.refresh_tasks()
    shell.topbar.task_pill.on_click(None)
    assert shell.current_key == "lm"
    session.finish()


def test_registry_is_handed_to_pages_that_want_it(shell, monkeypatch):
    received = []
    inner = SimpleNamespace(set_view_registry=received.append)
    shell._wire_registry(SimpleNamespace(content=inner))
    assert received == [shell.registry]
    shell._wire_registry(ft.Text("沒有 setter 的頁面"))  # 不出錯


def test_resize_collapses_sidebar_on_narrow_windows(shell):
    shell.page.width = 1000
    shell._on_resize()
    assert shell.sidebar.compact
    shell.page.width = 1500
    shell._on_resize()
    assert not shell.sidebar.compact


def test_environment_helpers_tolerate_missing_config():
    assert _enabled_model_name({}) is None
    assert (
        _enabled_model_name({"lm_translator": {"models": {"a": {"enabled": False}}}})
        is None
    )
    assert _cache_dir_of({}) is None
    assert _cache_dir_of({"translator": {"cache_directory": "x"}}) == "x"


def test_broken_config_or_keys_do_not_stop_the_shell(placeholder_views, env):
    def boom():
        raise RuntimeError("壞掉了")

    s = _make_shell(env, key_snapshot=boom, config_loader=boom)
    s.mount()
    s.tasks.detach()
    assert s.current_key == vr.DEFAULT_VIEW_KEY
    assert s.topbar.api_pill.count_text.value == "未設定 Key"


# -- 主題偏好與設定異動 -----------------------------------------------------------


def test_switching_theme_is_remembered(shell, env):
    shell.set_mode("light")
    assert env.saved_modes == ["light"]
    shell.set_mode("light")  # 沒變就不重複存
    assert env.saved_modes == ["light"]
    shell.toggle_mode()
    assert env.saved_modes == ["light", "dark"]


def test_initial_mode_is_not_saved_back(placeholder_views, env):
    s = _make_shell(env, initial_mode="light")
    s.mount()
    s.tasks.detach()
    assert env.saved_modes == []
    assert s.page.theme_mode == ft.ThemeMode.LIGHT
    assert s.sidebar.theme_toggle.value == "light"


def test_failing_theme_save_does_not_break_switching(placeholder_views, env):
    def boom(_mode):
        raise OSError("disk full")

    s = _make_shell(env, mode_saver=boom)
    s.mount()
    s.tasks.detach()
    s.set_mode("light")
    assert s.mode == "light"


def test_config_saved_elsewhere_refreshes_topbar_and_statusbar(shell, env):
    assert shell.topbar.api_pill.count_text.value == "1/2 Key"
    env.snapshot = [_key(0), _key(1), _key(2)]
    env.config = {"lm_translator": {"models": {"other-model": {"enabled": True}}}}
    env.fire_config_saved()
    assert shell.topbar.api_pill.count_text.value == "3/3 Key"
    assert shell.statusbar.model.value == "other-model"
    assert shell.statusbar.workdir.value == ""  # 設定裡沒有快取資料夾 → 不顯示
