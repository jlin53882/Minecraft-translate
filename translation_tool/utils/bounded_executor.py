"""Bounded submission helpers for cancellable thread-pool work."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Executor, Future, wait
from contextlib import contextmanager

from translation_tool.utils.cancellation import raise_if_cancelled


@contextmanager
def bounded_as_completed[T, R](
    executor: Executor,
    items: Iterable[T],
    submit: Callable[[Executor, T], Future[R]],
    *,
    max_in_flight: int,
    poll_interval: float = 0.1,
) -> Generator[Iterator[tuple[Future[R], T]], None, None]:
    """Yield completed futures while keeping submitted-but-uncollected work bounded.

    Items are consumed lazily. Cancellation is checked before every submission and
    while waiting; leaving early cancels futures that have not started. Running
    futures still need cooperative checkpoints or bounded blocking calls, and the
    caller's executor context remains responsible for joining them.
    """
    if max_in_flight < 1:
        raise ValueError("max_in_flight must be at least 1")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    item_iterator = iter(items)
    pending: dict[Future[R], T] = {}

    def iterate() -> Iterator[tuple[Future[R], T]]:
        exhausted = False
        drained = False

        def fill_window() -> None:
            nonlocal exhausted
            while not exhausted and len(pending) < max_in_flight:
                raise_if_cancelled()
                try:
                    item = next(item_iterator)
                except StopIteration:
                    exhausted = True
                    return
                pending[submit(executor, item)] = item

        try:
            fill_window()
            while pending:
                raise_if_cancelled()
                done, _ = wait(
                    pending,
                    timeout=poll_interval,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue
                for future in done:
                    item = pending.pop(future)
                    yield future, item
                fill_window()
            drained = True
        finally:
            if not drained:
                for future in pending:
                    future.cancel()

    iterator = iterate()
    try:
        yield iterator
    finally:
        iterator.close()
