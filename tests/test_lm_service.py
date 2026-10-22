from app.services_impl.pipelines import lm_service


class _Session:
    def __init__(self):
        self.logs = []
        self.progress = []
        self.calls = []

    def start(self):
        self.calls.append("start")

    def add_log(self, text, level="info", source="ui"):
        self.logs.append(text)

    def set_progress(self, value):
        self.progress.append(value)

    def set_error(self):
        self.calls.append("set_error")

    def finish(self):
        self.calls.append("finish")


def test_run_lm_translation_service_uses_log_limiter(monkeypatch):
    session = _Session()
    seen = []

    monkeypatch.setattr(lm_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(lm_service.UI_LOG_HANDLER, "set_session", lambda s: seen.append(s))

    def fake_gen(*args, **kwargs):
        yield {"log": "raw-1", "progress": 0.25}
        yield {"log": "raw-2", "progress": 0.5}

    filtered_inputs = []

    def fake_filter(update_dict):
        filtered_inputs.append(update_dict)
        return {
            "log": f"filtered:{update_dict['log']}",
            "progress": update_dict.get("progress"),
        }

    monkeypatch.setattr(lm_service, "lm_translate_gen", fake_gen)
    monkeypatch.setattr(lm_service.GLOBAL_LOG_LIMITER, "filter", fake_filter)
    monkeypatch.setattr(lm_service.GLOBAL_LOG_LIMITER, "flush", lambda: None)

    lm_service.run_lm_translation_service(
        "in",
        "out",
        session,
        dry_run=False,
        export_lang=False,
        write_new_cache=True,
    )

    assert filtered_inputs == [
        {"log": "raw-1", "progress": 0.25},
        {"log": "raw-2", "progress": 0.5},
    ]
    assert session.logs == ["filtered:raw-1", "filtered:raw-2"]
    assert session.progress == [0.25, 0.5]
    assert session.calls[0] == "start"
    assert session.calls[-1] == "finish"
    assert seen == [session, None]


def test_run_lm_translation_service_skips_when_limiter_returns_none(monkeypatch):
    session = _Session()

    monkeypatch.setattr(lm_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(lm_service.UI_LOG_HANDLER, "set_session", lambda s: None)

    def fake_gen(*args, **kwargs):
        yield {"log": "raw-1", "progress": 0.25}
        yield {"log": "raw-2", "progress": 0.5}

    calls = {"count": 0}

    def fake_filter(update_dict):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return {"log": "filtered:raw-2", "progress": 0.5}

    monkeypatch.setattr(lm_service, "lm_translate_gen", fake_gen)
    monkeypatch.setattr(lm_service.GLOBAL_LOG_LIMITER, "filter", fake_filter)
    monkeypatch.setattr(lm_service.GLOBAL_LOG_LIMITER, "flush", lambda: None)

    lm_service.run_lm_translation_service(
        "in",
        "out",
        session,
        dry_run=False,
        export_lang=False,
        write_new_cache=True,
    )

    assert session.logs == ["filtered:raw-2"]
    assert session.progress == [0.5]
    assert session.calls[-1] == "finish"
