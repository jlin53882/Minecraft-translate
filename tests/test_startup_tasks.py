import logging

from app import startup_tasks


def test_rebuild_index_on_startup_calls_service(monkeypatch):
    seen = []
    monkeypatch.setattr(
        startup_tasks,
        "cache_rebuild_index_service",
        lambda: seen.append("rebuilt") or {"success": True},
    )
    monkeypatch.setattr(
        startup_tasks.cache_manager, "is_search_index_current", lambda: False
    )

    startup_tasks.rebuild_index_on_startup()

    assert seen == ["rebuilt"]


def test_rebuild_index_on_startup_skips_when_index_current(monkeypatch):
    seen = []
    monkeypatch.setattr(
        startup_tasks, "cache_rebuild_index_service", lambda: seen.append("rebuilt")
    )
    monkeypatch.setattr(
        startup_tasks.cache_manager, "is_search_index_current", lambda: True
    )

    startup_tasks.rebuild_index_on_startup()

    assert seen == []


def test_startup_rebuild_does_not_report_success_when_service_fails(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        startup_tasks,
        "cache_rebuild_index_service",
        lambda: {"success": False, "error": "cache init failed"},
    )
    monkeypatch.setattr(
        startup_tasks.cache_manager, "is_search_index_current", lambda: False
    )

    with caplog.at_level(logging.INFO, logger="main_app"):
        startup_tasks.rebuild_index_on_startup()

    assert not any("重建完成" in r.getMessage() for r in caplog.records)
    assert any(
        r.levelno == logging.ERROR and "重建失敗" in r.getMessage()
        for r in caplog.records
    )


def test_start_background_startup_tasks_starts_thread(monkeypatch):
    seen = []

    class _Thread:
        def __init__(self, target=None, daemon=None):
            seen.append(("init", target, daemon))
            self.target = target

        def start(self):
            seen.append("start")

    monkeypatch.setattr(startup_tasks.threading, "Thread", _Thread)
    thread = startup_tasks.start_background_startup_tasks()

    assert seen[0][0] == "init"
    assert seen[0][2] is True
    assert seen[1] == "start"
    assert isinstance(thread, _Thread)
