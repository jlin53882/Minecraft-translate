from __future__ import annotations

from app.services_impl.pipelines import (
    _pipeline_logging,
    extract_service,
    ftb_service,
    kubejs_service,
    lm_service,
    md_service,
    merge_service,
)


class DummySession:
    def __init__(self):
        self.error = False

    def start(self):
        return None

    def finish(self):
        return None

    def add_log(self, _msg):
        return None

    def set_progress(self, _v):
        return None

    def set_error(self):
        self.error = True


class DummyHandler:
    def set_session(self, _session):
        return None


def _assert_logger_before_first_step(events: list[str]) -> None:
    assert "logger" in events
    assert "first-step" in events
    assert events.index("logger") < events.index("first-step")


def _make_logger_patch(events: list[str]):
    def _fake_apply(_config_loader, *, logger_name="translation_tool"):
        assert logger_name == "translation_tool"
        events.append("logger")
        return None

    return _fake_apply


def test_ftb_pipeline_bootstrap_order(monkeypatch):
    import translation_tool.core.ftb_translator as ftb_core

    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(ftb_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_run_ftb_pipeline(*_args, **_kwargs):
        events.append("first-step")

    monkeypatch.setattr(ftb_core, "run_ftb_pipeline", _fake_run_ftb_pipeline)

    ftb_service.run_ftb_translation_service("in", DummySession(), None)
    _assert_logger_before_first_step(events)


def test_kubejs_pipeline_bootstrap_order(monkeypatch):
    import translation_tool.core.kubejs_translator as kubejs_core

    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(kubejs_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_run_kubejs_pipeline(*_args, **_kwargs):
        events.append("first-step")

    monkeypatch.setattr(kubejs_core, "run_kubejs_pipeline", _fake_run_kubejs_pipeline)

    kubejs_service.run_kubejs_tooltip_service("in", DummySession(), None)
    _assert_logger_before_first_step(events)


def test_md_pipeline_bootstrap_order(monkeypatch):
    import translation_tool.core.md_translation_assembly as md_core

    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(md_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_run_md_pipeline(*_args, **_kwargs):
        events.append("first-step")

    monkeypatch.setattr(md_core, "run_md_pipeline", _fake_run_md_pipeline)

    md_service.run_md_translation_service("in", DummySession(), output_dir=None)
    _assert_logger_before_first_step(events)


def test_lm_pipeline_bootstrap_order(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(lm_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_lm_translate_gen(*_args, **_kwargs):
        events.append("first-step")
        yield {"log": "ok"}

    monkeypatch.setattr(lm_service, "lm_translate_gen", _fake_lm_translate_gen)

    lm_service.run_lm_translation_service("in", "out", DummySession())
    _assert_logger_before_first_step(events)


def test_extract_lang_pipeline_bootstrap_order(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(extract_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_extract_lang_gen(*_args, **_kwargs):
        events.append("first-step")
        yield {"log": "ok", "progress": 1.0}

    monkeypatch.setattr(extract_service, "extract_lang_files_generator", _fake_extract_lang_gen)

    extract_service.run_lang_extraction_service("mods", "out", DummySession())
    _assert_logger_before_first_step(events)


def test_extract_book_pipeline_bootstrap_order(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(extract_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_extract_book_gen(*_args, **_kwargs):
        events.append("first-step")
        yield {"log": "ok", "progress": 1.0}

    monkeypatch.setattr(extract_service, "extract_book_files_generator", _fake_extract_book_gen)

    extract_service.run_book_extraction_service("mods", "out", DummySession())
    _assert_logger_before_first_step(events)


def test_merge_pipeline_bootstrap_order(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(_pipeline_logging, "apply_logger_config", _make_logger_patch(events))
    monkeypatch.setattr(merge_service, "UI_LOG_HANDLER", DummyHandler())

    def _fake_merge_gen(*_args, **_kwargs):
        events.append("first-step")
        yield {"progress": 1.0}

    monkeypatch.setattr(merge_service, "merge_zhcn_to_zhtw_from_zip", _fake_merge_gen)

    merge_service.run_merge_zip_batch_service(["demo.zip"], "out", DummySession(), True)
    _assert_logger_before_first_step(events)
