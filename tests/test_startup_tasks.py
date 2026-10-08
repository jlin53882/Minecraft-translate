from app import startup_tasks
from app.tasks.operation_registry import OperationRegistry


def test_startup_index_rebuild_is_reserved_before_worker_launch(monkeypatch):
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
