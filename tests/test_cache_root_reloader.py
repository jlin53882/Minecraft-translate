"""translator.cache_directory 變更：沒任務立即重載；有任務等任務結束；不在任務中途切換。"""

from __future__ import annotations

from app.shell.config_effects import CACHE_DIRECTORY_PATH, CacheRootReloader


class _Env:
    def __init__(self, busy=False, reload_error=None):
        self.busy = busy
        self.reload_error = reload_error
        self.reloads = 0
        self.results: list[bool] = []
        self.queue: list = []

    def make(self):
        def reload():
            self.reloads += 1
            if self.reload_error:
                raise self.reload_error

        return CacheRootReloader(
            is_busy=lambda: self.busy,
            reload=reload,
            on_reloaded=self.results.append,
            start=self.queue.append,  # 不開真的執行緒：由測試決定何時跑
        )

    def run_queued(self):
        while self.queue:
            self.queue.pop(0)()


def test_idle_change_reloads_immediately():
    env = _Env()
    r = env.make()
    r.on_config_paths({CACHE_DIRECTORY_PATH})
    env.run_queued()
    assert env.reloads == 1 and env.results == [True] and not r.pending


def test_unrelated_paths_do_nothing():
    env = _Env()
    r = env.make()
    r.on_config_paths({"lm_translator.temperature"})
    env.run_queued()
    assert env.reloads == 0 and not r.pending


def test_busy_defers_until_tasks_finish_and_never_reloads_mid_task():
    env = _Env(busy=True)
    r = env.make()
    r.on_config_paths({CACHE_DIRECTORY_PATH})
    r.poke()
    r.poke()
    env.run_queued()
    assert env.reloads == 0 and r.pending  # 任務進行中：不得換根目錄

    env.busy = False  # 任務結束
    r.poke()
    env.run_queued()
    assert env.reloads == 1 and not r.pending and env.results == [True]


def test_multiple_changes_while_busy_reload_once():
    env = _Env(busy=True)
    r = env.make()
    for _ in range(3):
        r.on_config_paths({CACHE_DIRECTORY_PATH})
    env.busy = False
    r.poke()
    env.run_queued()
    assert env.reloads == 1


def test_change_during_reload_triggers_one_more_reload():
    env = _Env()
    r = env.make()
    r.on_config_paths({CACHE_DIRECTORY_PATH})
    assert len(env.queue) == 1
    r.on_config_paths({CACHE_DIRECTORY_PATH})  # 重載執行前又變更
    assert len(env.queue) == 1  # 已在執行中：不重複啟動
    env.run_queued()  # 第一次重載結束後會接著處理等待中的變更
    assert env.reloads == 2 and env.results == [True, True]


def test_reload_failure_is_reported_not_raised():
    env = _Env(reload_error=RuntimeError("disk"))
    r = env.make()
    r.on_config_paths({CACHE_DIRECTORY_PATH})
    env.run_queued()
    assert env.results == [False] and not r.pending


def test_failing_busy_probe_is_tolerated():
    def boom():
        raise RuntimeError("tasks down")

    r = CacheRootReloader(is_busy=boom, reload=lambda: None, start=lambda f: f())
    r.on_config_paths({CACHE_DIRECTORY_PATH})  # 不得丟例外
    assert r.pending
