"""scan_pool.py

掃描 jar 的讀取端執行器。

JAR 解析是純 Python（zipfile、json、正規表示式），執行緒受 GIL 限制，worker 越多越慢；
大量 jar 時改用行程池（``spawn``，Windows 與 macOS 的唯一選擇，也避免在多執行緒的
Flet 行程中 ``fork``）。行程池的注意事項：

- 選項不含替換規則物件（會追蹤修改、不可序列化）：以純字典傳給 worker，在初始化時重建。
- 取消：worker 看不到呼叫端的回呼，改由共用的 ``Event``；監看執行緒輪詢回呼並設定它，
  worker 在讀取的下一個區塊就中止，與執行緒版相同。
- 日誌：worker 行程的日誌不會進到呼叫端的 UI 會話，所以在 worker 內收集，隨結果送回，
  由呼叫端重新送出。
- 打包後的程式需在入口呼叫 ``multiprocessing.freeze_support()``（見 ``main.py``）；
  啟動失敗（無法建立行程、worker 立即結束）時退回單一讀取執行緒，掃描仍可完成。
- 少量 jar 不值得行程啟動成本（每個 worker 要重新匯入模組），仍用單一讀取執行緒。
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor

# 低於此 jar 數不啟動行程池
PROCESS_MIN_JARS = 24
_PROBE_TIMEOUT_SEC = 120
_CANCEL_POLL_SEC = 0.05

Submit = Callable[[Executor, Path], Future]


class _Collector(logging.Handler):
    """收集 worker 行程內的日誌記錄（等級, 訊息）。"""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


_state: dict[str, Any] = {}


def _init_worker(options, rule_dicts, dir_names, cancel_event) -> None:
    from translation_tool.utils.text_processor import ReplaceRules

    collector = _Collector()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(collector)
    _state.update(
        options=replace(options, rules=ReplaceRules(rule_dicts)),
        dirs=tuple(dir_names),
        cancel=cancel_event,
        collector=collector,
    )


def _ping() -> bool:
    return True


def _scan_in_worker(path: Path):
    from translation_tool.translation_db.scanner import scan_jar

    collector: _Collector = _state["collector"]
    collector.records.clear()
    cancel = _state["cancel"]
    result = scan_jar(path, _state["options"], _state["dirs"], cancel.is_set)
    result.logs = list(collector.records)
    return result


def replay_logs(logs: Sequence[tuple[int, str]]) -> None:
    """把 worker 行程收集的日誌送進呼叫端的日誌（保留等級）。"""
    for level, message in logs:
        (log_warning if level >= logging.WARNING else log_info)(message)


def _watch_cancel(
    should_cancel: Callable[[], bool], event, stop: threading.Event
) -> None:
    while not stop.wait(_CANCEL_POLL_SEC):
        if should_cancel():
            event.set()
            return


def _start_process_pool(options, dir_names, workers, event) -> ProcessPoolExecutor:
    ctx = multiprocessing.get_context("spawn")
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_init_worker,
        initargs=(
            replace(options, rules=()),
            [dict(rule) for rule in options.rules],
            tuple(dir_names),
            event,
        ),
    )
    try:
        pool.submit(_ping).result(timeout=_PROBE_TIMEOUT_SEC)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    return pool


@contextmanager
def scan_executor(
    options,
    dir_names: Sequence[str],
    *,
    workers: int,
    jar_count: int,
    should_cancel: Callable[[], bool] | None,
) -> Iterator[tuple[Executor, Submit]]:
    """產生讀取 jar 的執行器與對應的提交函式（``submit(executor, path) -> Future``）。"""
    from translation_tool.translation_db.scanner import scan_jar

    if workers > 1 and jar_count >= PROCESS_MIN_JARS:
        event = multiprocessing.get_context("spawn").Event()
        try:
            pool = _start_process_pool(options, dir_names, workers, event)
        except Exception as exc:  # noqa: BLE001 - 行程池無法啟動（打包、權限等）時退回執行緒
            log_warning(f"[ModDB] 無法啟動掃描行程池，改用單一執行緒讀取：{exc!r}")
        else:
            stop = threading.Event()
            if should_cancel is not None:
                threading.Thread(
                    target=_watch_cancel,
                    args=(should_cancel, event, stop),
                    daemon=True,
                ).start()
            try:
                with pool:
                    yield pool, lambda ex, path: ex.submit(_scan_in_worker, path)
            finally:
                stop.set()
            return
    dirs = tuple(dir_names)
    with ContextThreadPoolExecutor(max_workers=1) as pool:
        yield (
            pool,
            lambda ex, path: ex.submit(scan_jar, path, options, dirs, should_cancel),
        )
