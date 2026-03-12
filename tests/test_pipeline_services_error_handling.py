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


def test_run_callable_task_sets_error_and_optional_session_log(monkeypatch):
    session = _Session()
    seen = []

    monkeypatch.setattr(_task_runner, 'ensure_pipeline_logging', lambda: None)
    monkeypatch.setattr(_task_runner.UI_LOG_HANDLER, 'set_session', lambda s: seen.append(s))

    def boom(**kwargs):
        raise RuntimeError('boom')

    result = _task_runner.run_callable_task(
        session=session,
        task_name='task',
        func=boom,
        kwargs={},
        add_session_log_on_error=True,
    )

    assert result is None
    assert session.calls[0] == 'start'
    assert session.calls[-1] == 'set_error'
    assert any(isinstance(c, tuple) and c[0] == 'add_log' for c in session.calls)
    assert seen == [session, None]
