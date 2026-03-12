from app.services_impl.pipelines import _task_runner


class _Session:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append('start')

    def finish(self):
        self.calls.append('finish')

    def set_error(self):
        self.calls.append('set_error')

    def add_log(self, text):
        self.calls.append(('add_log', text))


def test_run_callable_task_runs_start_and_finish(monkeypatch):
    session = _Session()
    seen = []

    monkeypatch.setattr(_task_runner, 'ensure_pipeline_logging', lambda: seen.append('logging'))
    monkeypatch.setattr(_task_runner.UI_LOG_HANDLER, 'set_session', lambda s: seen.append(('ui', s)))

    result = _task_runner.run_callable_task(
        session=session,
        task_name='task',
        func=lambda **kwargs: 'ok',
        kwargs={},
    )

    assert result == 'ok'
    assert session.calls == ['start', 'finish']
    assert seen[0] == 'logging'
    assert seen[1] == ('ui', session)
    assert seen[-1] == ('ui', None)
