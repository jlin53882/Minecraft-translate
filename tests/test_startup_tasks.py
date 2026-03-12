from app import startup_tasks


def test_rebuild_index_on_startup_calls_service(monkeypatch):
    seen = []
    monkeypatch.setattr(startup_tasks, 'cache_rebuild_index_service', lambda: seen.append('rebuilt'))

    startup_tasks.rebuild_index_on_startup()

    assert seen == ['rebuilt']


def test_start_background_startup_tasks_starts_thread(monkeypatch):
    seen = []

    class _Thread:
        def __init__(self, target=None, daemon=None):
            seen.append(('init', target, daemon))
            self.target = target
        def start(self):
            seen.append('start')

    monkeypatch.setattr(startup_tasks.threading, 'Thread', _Thread)
    thread = startup_tasks.start_background_startup_tasks()

    assert seen[0][0] == 'init'
    assert seen[0][2] is True
    assert seen[1] == 'start'
    assert isinstance(thread, _Thread)
