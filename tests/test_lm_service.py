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
    monkeypatch.setattr(
        lm_service.UI_LOG_HANDLER, "set_session", lambda s: seen.append(s)
    )

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


def test_run_lm_translation_service_streams_progress_and_batch_events(monkeypatch):
    """流水線 LM 必須在 API 工作期間即時寫入進度與共用批次事件。"""
    session = _Session()
    session.task_id = "task-pipeline"
    mirrored = []

    monkeypatch.setattr(lm_service, "ensure_pipeline_logging", lambda: None)
    monkeypatch.setattr(lm_service.UI_LOG_HANDLER, "set_session", lambda _s: None)
    monkeypatch.setattr(
        lm_service,
        "mirror_session_log",
        lambda _session, _logger, text, level="info", **_kwargs: mirrored.append(
            (level, text)
        ),
    )
    monkeypatch.setattr(lm_service.GLOBAL_LOG_LIMITER, "filter", lambda update: update)
    monkeypatch.setattr(lm_service.GLOBAL_LOG_LIMITER, "flush", lambda: None)

    def fake_gen(*_args, **kwargs):
        # This callback executes before the generator yields again, simulating
        # a long provider request.  The session must already contain the log.
        kwargs["update_sink"]({"log": "live-progress", "progress": 0.35})
        kwargs["event_sink"](
            {
                "kind": "batch_start",
                "operation": "translate",
                "provider": "gemini",
                "model": "model-pool",
                "batch_index": 1,
                "batch_total": 2,
                "batch_size": 4,
                "expected_count": 4,
            }
        )
        assert session.logs == ["live-progress"]
        yield {"log": "after-progress", "progress": 0.5}

    monkeypatch.setattr(lm_service, "lm_translate_gen", fake_gen)

    lm_service.run_lm_translation_service("in", "out", session)

    assert session.logs == ["live-progress", "after-progress"]
    assert session.progress == [0.35, 0.5]
    assert mirrored and mirrored[0][0] == "info"
    assert "[LM 翻譯][批次 1/2]" in mirrored[0][1]
