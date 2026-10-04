"""#117 後續：設定使用時才讀、有任務時存檔的提醒、log_dir 實際生效。"""

from __future__ import annotations

import asyncio

from app import config_apply
from app.shell import app_shell
from app.shell.app_shell import AppShell
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


class _Reloader:
    def __init__(self):
        self.seen = []

    def on_config_paths(self, paths):
        self.seen.append(frozenset(paths))

    def poke(self):
        pass


def _shell(active):
    import threading

    shell = AppShell.__new__(AppShell)
    shell._sched_lock = threading.Lock()
    shell._disposed = False
    shell.tasks = _Tasks(active)
    shell._cache_reloader = _Reloader()
    shell.submitted = []
    shell._submit_ui = lambda handler: shell.submitted.append(handler) or (True, None)
    shell.page = object()
    return shell


def _run_notice(shell, monkeypatch):
    """執行排程出來的 notify 協程，回傳顯示的訊息。"""
    shown = []
    monkeypatch.setattr(
        app_shell, "show_snack", lambda page, msg, *a, **k: shown.append(msg)
    )
    for handler in shell.submitted:
        asyncio.run(handler())
    return shown


def test_next_task_setting_while_task_running_shows_notice(monkeypatch):
    shell = _shell(active=[object()])
    shell._on_config_paths_saved(frozenset({"lm_translator.initial_batch_size_lang"}))
    shown = _run_notice(shell, monkeypatch)
    assert len(shown) == 1 and config_apply.NOTICE_NEXT_TASK in shown[0]


def test_next_batch_setting_does_not_claim_running_task_is_unaffected(monkeypatch):
    shell = _shell(active=[object()])
    shell._on_config_paths_saved(frozenset({"lm_translator.temperature"}))
    shown = _run_notice(shell, monkeypatch)
    assert len(shown) == 1
    assert config_apply.NOTICE_NEXT_BATCH in shown[0]
    assert config_apply.NOTICE_NEXT_TASK not in shown[0]


def test_immediate_logging_setting_never_shows_next_task_notice(monkeypatch):
    """log_level / log_format 存檔後立即套用，不得顯示「下次任務才套用」。"""
    shell = _shell(active=[object()])
    shell._on_config_paths_saved(frozenset({"logging.log_level", "logging.log_format"}))
    assert shell.submitted == []


def test_restart_setting_notice_even_without_running_task(monkeypatch):
    shell = _shell(active=[])
    shell._on_config_paths_saved(frozenset({"species_cache.cache_filename"}))
    shown = _run_notice(shell, monkeypatch)
    assert len(shown) == 1 and config_apply.NOTICE_RESTART in shown[0]


def test_no_notice_without_running_task_for_task_scoped_settings():
    shell = _shell(active=[])
    shell._on_config_paths_saved(frozenset({"lm_translator.temperature"}))
    assert shell.submitted == []


def test_cache_directory_change_is_forwarded_and_explained_when_busy(monkeypatch):
    shell = _shell(active=[object()])
    paths = frozenset({"translator.cache_directory"})
    shell._on_config_paths_saved(paths)
    assert shell._cache_reloader.seen == [paths]
    shown = _run_notice(shell, monkeypatch)
    assert len(shown) == 1 and config_apply.NOTICE_WHEN_IDLE in shown[0]


def test_nothing_happens_after_dispose():
    shell = _shell(active=[object()])
    shell._disposed = True
    shell._on_config_paths_saved(frozenset({"lm_translator.initial_batch_size_lang"}))
    assert shell.submitted == [] and shell._cache_reloader.seen == []


def test_notice_handler_is_skipped_if_disposed_before_it_runs(monkeypatch):
    shell = _shell(active=[object()])
    shell._on_config_paths_saved(frozenset({"lm_translator.initial_batch_size_lang"}))
    shell._disposed = True
    assert _run_notice(shell, monkeypatch) == []


# --- 中央表 save_notice ----------------------------------------------------


def test_save_notice_follows_central_timing_table():
    n = config_apply.save_notice
    assert n({"logging.log_level"}, tasks_running=True) is None
    assert n({"ui.theme_mode"}, tasks_running=True) is None
    assert n({"lm_translator.initial_batch_size_md"}, tasks_running=False) is None
    both = n(
        {"lm_translator.initial_batch_size_md", "lm_translator.temperature"},
        tasks_running=True,
    )
    assert config_apply.NOTICE_NEXT_TASK in both
    assert config_apply.NOTICE_NEXT_BATCH in both
    restart = n({"species_cache.cache_directory"}, tasks_running=False)
    assert config_apply.NOTICE_RESTART in restart


def test_every_registered_timing_is_known():
    for path, rule in config_apply.CONFIG_APPLY_RULES.items():
        assert rule["timing"] in config_apply.ALL_TIMINGS, path
