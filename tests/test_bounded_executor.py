from __future__ import annotations

import threading

import pytest

from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cancellation import TaskCancelled, cancel_scope
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor


def test_bounded_as_completed_only_submits_the_initial_window():
    release_workers = threading.Event()
    three_submitted = threading.Event()
    submitted: list[int] = []
    result: list[tuple[object, int]] = []
    errors: list[BaseException] = []

    def work(item: int) -> int:
        assert release_workers.wait(timeout=2)
        return item

    def submit(executor, item: int):
        submitted.append(item)
        if len(submitted) == 3:
            three_submitted.set()
        return executor.submit(work, item)

    def items():
        yield from range(3)
        raise AssertionError("the executor submitted beyond its in-flight window")

    def take_one(completed) -> None:
        try:
            result.append(next(completed))
        except BaseException as exc:  # noqa: BLE001 - forward consumer-thread errors
            errors.append(exc)

    with (
        ContextThreadPoolExecutor(max_workers=2) as executor,
        bounded_as_completed(
            executor,
            items(),
            submit,
            max_in_flight=3,
        ) as completed,
    ):
        consumer = threading.Thread(target=take_one, args=(completed,))
        consumer.start()
        assert three_submitted.wait(timeout=2)
        assert submitted == [0, 1, 2]
        release_workers.set()
        consumer.join(timeout=2)

    assert not consumer.is_alive()
    assert errors == []
    assert len(result) == 1


def test_bounded_as_completed_stops_submitting_when_cancelled():
    cancel = threading.Event()
    release_workers = threading.Event()
    three_submitted = threading.Event()
    submitted: list[int] = []

    def work(item: int) -> int:
        assert release_workers.wait(timeout=2)
        return item

    def submit(executor, item: int):
        submitted.append(item)
        if len(submitted) == 3:
            three_submitted.set()
        return executor.submit(work, item)

    def cancel_after_initial_window() -> None:
        assert three_submitted.wait(timeout=2)
        cancel.set()
        release_workers.set()

    cancel_driver = threading.Thread(target=cancel_after_initial_window)
    cancel_driver.start()
    with (
        cancel_scope(cancel.is_set),
        ContextThreadPoolExecutor(max_workers=2) as executor,
        bounded_as_completed(
            executor,
            range(20),
            submit,
            max_in_flight=3,
        ) as completed,
        pytest.raises(TaskCancelled),
    ):
        list(completed)
    cancel_driver.join(timeout=2)

    assert not cancel_driver.is_alive()
    assert submitted == [0, 1, 2]
