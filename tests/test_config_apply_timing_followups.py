"""#117 後續：設定使用時才讀、有任務時存檔的提醒、log_dir 實際生效。"""

from __future__ import annotations

import asyncio

from app.shell import app_shell
from app.shell.app_shell import CONFIG_WHILE_RUNNING_NOTICE, AppShell
from app.views import lm_view
from translation_tool.utils import exceptions


def test_lm_folder_name_is_read_at_use_time(monkeypatch):
    values = {"lm_translate_folder_name": "A資料夾"}
    monkeypatch.setattr(lm_view, "load_config", lambda: {"lm_translator": values})
    assert lm_view.get_lm_translate_folder_name() == "A資料夾"
    values["lm_translate_folder_name"] = "B資料夾"
    assert lm_view.get_lm_translate_folder_name() == "B資料夾"


def test_error_log_dir_follows_config(monkeypatch, tmp_path):
    from translation_tool.utils import config_manager

    monkeypatch.setattr(
        config_manager,
        "load_config",
        lambda *a, **k: {"logging": {"log_dir": str(tmp_path / "my_logs")}},
    )
    assert exceptions._resolve_error_log_dir() == tmp_path / "my_logs"


def test_error_log_dir_falls_back_when_config_fails(monkeypatch):
    from translation_tool.utils import config_manager

    def boom(*a, **k):
        raise RuntimeError("config broken")

    monkeypatch.setattr(config_manager, "load_config", boom)
    assert exceptions._resolve_error_log_dir().name == "logs"


class _Tasks:
    def __init__(self, active):
        self._active = active

    def active(self):
        return self._active


def _shell(active):
    shell = AppShell.__new__(AppShell)
    import threading

    shell._sched_lock = threading.Lock()
    shell._disposed = False
    shell.tasks = _Tasks(active)
    shell.submitted = []
    shell._submit_ui = lambda handler: shell.submitted.append(handler) or (True, None)
    return shell


def test_notice_scheduled_when_task_running_and_config_changed():
    shell = _shell(active=[object()])
    shell._on_config_paths_saved(frozenset({"lm_translator.temperature"}))
    assert len(shell.submitted) == 1


def test_no_notice_without_running_task():
    shell = _shell(active=[])
    shell._on_config_paths_saved(frozenset({"lm_translator.temperature"}))
    assert shell.submitted == []


def test_no_notice_for_theme_only_change():
    shell = _shell(active=[object()])
    shell._on_config_paths_saved(frozenset({"ui.theme_mode"}))
    assert shell.submitted == []


def test_no_notice_after_dispose():
    shell = _shell(active=[object()])
    shell._disposed = True
    shell._on_config_paths_saved(frozenset({"lm_translator.temperature"}))
    assert shell.submitted == []


def test_notice_handler_shows_snack(monkeypatch):
    shell = _shell(active=[object()])
    shown = []
    monkeypatch.setattr(
        app_shell, "show_snack", lambda page, msg, *a, **k: shown.append(msg)
    )
    shell.page = object()
    asyncio.run(shell._show_config_while_running_notice())
    assert shown == [CONFIG_WHILE_RUNNING_NOTICE]
    shell._disposed = True
    asyncio.run(shell._show_config_while_running_notice())
    assert len(shown) == 1
