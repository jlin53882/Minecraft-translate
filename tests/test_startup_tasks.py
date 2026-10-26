from app import startup_tasks
from app.tasks.operation_registry import OperationRegistry


def test_startup_index_rebuild_is_reserved_before_worker_launch(monkeypatch):
    monkeypatch.setattr(startup_tasks, "warm_view_imports", lambda: None)
    monkeypatch.setattr(startup_tasks, "warm_token_encoder", lambda: None)
    registry = OperationRegistry()
    monkeypatch.setattr(
        startup_tasks.cache_manager, "is_search_index_current", lambda: True
    )
    reserved_counts = []

    def launcher(target):
        reserved_counts.append(registry.active_count())
        target()

    handle = startup_tasks.start_background_startup_tasks(
        registry, worker_launcher=launcher
    )

    assert handle is not None
    assert reserved_counts == [1]
    assert handle.descriptor.owner == "startup-index"
    assert handle.descriptor.cancellation.value == "non_cancellable"
    assert handle.descriptor.shutdown.value == "drain_only"
    assert handle.descriptor.presentation.value == "maintenance"
    assert handle.done_event.is_set()
    assert registry.active() == []


def test_startup_index_rebuild_is_not_launched_after_shutdown(monkeypatch):
    registry = OperationRegistry()
    registry.begin_shutdown()
    service_calls = []
    monkeypatch.setattr(
        startup_tasks.cache_manager,
        "is_search_index_current",
        lambda: service_calls.append("check") or True,
    )
    launch_calls = []

    handle = startup_tasks.start_background_startup_tasks(
        registry, worker_launcher=lambda target: launch_calls.append(target)
    )

    assert handle is None
    assert launch_calls == []
    assert service_calls == []


def test_warm_view_imports_imports_every_view_module_without_building_views(
    monkeypatch,
):
    from app.view_registry import VIEW_SPECS

    imported = []
    monkeypatch.setattr(
        startup_tasks.importlib, "import_module", lambda name: imported.append(name)
    )

    startup_tasks.warm_view_imports()

    assert imported == [spec.module for spec in VIEW_SPECS]


def test_warm_view_imports_survives_a_broken_module(monkeypatch):
    from app.view_registry import VIEW_SPECS

    imported = []

    def fake_import(name):
        imported.append(name)
        if len(imported) == 1:
            raise ImportError("boom")

    monkeypatch.setattr(startup_tasks.importlib, "import_module", fake_import)

    startup_tasks.warm_view_imports()

    assert len(imported) == len(VIEW_SPECS)


def test_thread_fallback_warms_imports_before_rebuilding_index(monkeypatch):
    order = []
    monkeypatch.setattr(
        startup_tasks, "warm_view_imports", lambda: order.append("warm")
    )
    monkeypatch.setattr(
        startup_tasks, "warm_token_encoder", lambda: order.append("tokenizer")
    )
    monkeypatch.setattr(
        startup_tasks, "rebuild_index_on_startup", lambda: order.append("index") or True
    )

    thread = startup_tasks.start_background_startup_tasks()
    thread.join(timeout=5)

    # tokenizer 在啟動時先載入：第一次載入會持有 GIL，不能留到使用者第一次預覽批次機翻時
    assert order == ["warm", "tokenizer", "index"]


def test_warm_token_encoder_loads_the_default_tokenizer_once(monkeypatch):
    from translation_tool.core import lm_batch_budget

    loaded = []
    monkeypatch.setattr(
        lm_batch_budget,
        "_get_tiktoken_encoding",
        lambda name: loaded.append(name) or object(),
    )

    startup_tasks.warm_token_encoder()

    assert loaded == [lm_batch_budget.DEFAULT_TOKEN_ESTIMATOR.tiktoken_encoding]


def test_warm_token_encoder_survives_a_missing_tokenizer(monkeypatch):
    from translation_tool.core import lm_batch_budget

    def broken(_name):
        raise OSError("no vocabulary file")

    monkeypatch.setattr(lm_batch_budget, "_get_tiktoken_encoding", broken)

    startup_tasks.warm_token_encoder()  # 預熱失敗不能讓啟動失敗
