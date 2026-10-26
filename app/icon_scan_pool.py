"""圖示預覽的 JAR 掃描執行器：行程池（多核）為主，無法啟動時退回執行緒。

解析圖示是純 Python（zipfile、json、字典查找），受 GIL 限制，執行緒幾乎不會更快；
JAR 很多時改用 ``spawn`` 行程池（Windows／macOS 唯一選擇，也避免在多執行緒的 Flet 行程中
``fork``）。與 ``translation_tool.translation_db.scan_pool`` 相同的注意事項：

- 每個 worker 行程第一次使用時自行建立一份 ``ModpackAssetCatalog``（不能跨行程共用）；
  建立是確定性的，所有行程的解析結果與單一 catalog 一致。
- 取消：worker 看不到呼叫端的回呼，改由共用的 ``Event``；監看執行緒輪詢回呼並設定它。
- 日誌：worker 行程的日誌在 worker 內收集，隨結果送回，由呼叫端重新送出。
- 打包後的程式需在入口呼叫 ``multiprocessing.freeze_support()``（見 ``main.py``）；
  啟動失敗時退回執行緒，掃描仍可完成。
- 少量 JAR 不值得行程啟動成本，仍用執行緒。
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from pathlib import Path

from app.icon_catalog import ModpackAssetCatalog
from app.icon_jar_scan import JarScan, scan_jar
from translation_tool.utils.bounded_executor import bounded_as_completed
from translation_tool.utils.cancellation import (
    TaskCancelled,
    cancel_scope,
    raise_if_cancelled,
)
from translation_tool.utils.log_unit import log_info, log_warning
from translation_tool.utils.ui_mirror import ContextThreadPoolExecutor, run_in_context

# 低於此 JAR 數不啟動行程池
PROCESS_MIN_JARS = 24
_PROBE_TIMEOUT_SEC = 120
_CANCEL_POLL_SEC = 0.05

_state: dict = {}


class _Collector(logging.Handler):
    """收集 worker 行程內的日誌記錄（等級, 訊息）。"""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


def _init_worker(source_root: str, cancel_event) -> None:
    collector = _Collector()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(collector)
    _state.update(
        source_root=Path(source_root), cancel=cancel_event, collector=collector
    )


def _ping() -> bool:
    return True


def _scan_in_worker(jar_path: Path) -> JarScan:
    cancel = _state["cancel"]
    collector: _Collector = _state["collector"]
    collector.records.clear()
    if "catalog" not in _state:
        # 第一個工作時才建（啟動探測不必等 catalog）；之後每個 JAR 重用
        with cancel_scope(cancel.is_set):
            _state["catalog"] = ModpackAssetCatalog.from_mods_directory(
                _state["source_root"]
            )
    with cancel_scope(cancel.is_set):
        result = scan_jar(jar_path, _state["catalog"])
    result.logs = list(collector.records)
    return result


def _replay_logs(logs) -> None:
    for level, message in logs:
        (log_warning if level >= logging.WARNING else log_info)(message)


def _watch_cancel(should_cancel, event, stop: threading.Event) -> None:
    while not stop.wait(_CANCEL_POLL_SEC):
        if should_cancel():
            event.set()
            return


def _start_process_pool(source_root: Path, workers: int, event) -> ProcessPoolExecutor:
    ctx = multiprocessing.get_context("spawn")
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_init_worker,
        initargs=(str(source_root), event),
    )
    try:
        pool.submit(_ping).result(timeout=_PROBE_TIMEOUT_SEC)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    return pool


Submit = Callable[[Executor, Path], Future]


@contextmanager
def icon_scan_executor(
    source_root: Path,
    *,
    workers: int,
    jar_count: int,
    should_cancel: Callable[[], bool] | None,
    with_icons: bool = True,
    use_processes: bool = True,
) -> Iterator[tuple[Executor, Submit]]:
    """產生讀取 JAR 的執行器與提交函式（``submit(executor, path) -> Future[JarScan]``）。

    ``with_icons=False`` 時只讀 lang（已有預建圖示索引時），不建立 catalog。
    """
    workers = max(1, min(workers, os.cpu_count() or 1))
    if use_processes and with_icons and workers > 1 and jar_count >= PROCESS_MIN_JARS:
        event = multiprocessing.get_context("spawn").Event()
        try:
            pool = _start_process_pool(source_root, workers, event)
        except Exception as exc:  # noqa: BLE001 - 行程池無法啟動（打包、權限等）時退回執行緒
            log_warning(f"[IconPreview] 無法啟動圖示掃描行程池，改用執行緒：{exc!r}")
        else:
            stop = threading.Event()
            if should_cancel is not None:
                threading.Thread(
                    target=run_in_context(_watch_cancel),
                    args=(should_cancel, event, stop),
                    daemon=True,
                ).start()
            try:
                with pool:
                    yield pool, lambda ex, path: ex.submit(_scan_in_worker, path)
            finally:
                stop.set()
            return
    catalog = (
        ModpackAssetCatalog.from_mods_directory(source_root) if with_icons else None
    )
    try:
        with ContextThreadPoolExecutor(max_workers=workers) as pool:
            yield pool, lambda ex, path: ex.submit(scan_jar, path, catalog)
    finally:
        if catalog is not None:
            catalog.close()


def stream_jar_scans(
    source_root: Path,
    jars: list[Path],
    *,
    workers: int,
    should_cancel: Callable[[], bool] | None = None,
    with_icons: bool = True,
) -> Iterator[JarScan]:
    """依完成順序產出每個 JAR 的掃描結果；取消時拋出 ``TaskCancelled``。

    行程池中途整個壞掉（worker 被系統終止等）時，其餘 JAR 改用執行緒完成。
    """
    remaining = {jar: None for jar in jars}
    use_processes = True
    while remaining:
        try:
            yield from _stream_once(
                source_root,
                list(remaining),
                workers,
                should_cancel,
                with_icons,
                use_processes,
                remaining,
            )
        except BrokenProcessPool as exc:
            log_warning(f"[IconPreview] 圖示掃描行程池中斷，改用執行緒完成：{exc!r}")
            use_processes = False
        else:
            return


def _stream_once(
    source_root, jars, workers, should_cancel, with_icons, use_processes, remaining
) -> Iterator[JarScan]:
    with (
        icon_scan_executor(
            source_root,
            workers=workers,
            jar_count=len(jars),
            should_cancel=should_cancel,
            with_icons=with_icons,
            use_processes=use_processes,
        ) as (executor, submit),
        bounded_as_completed(
            executor, jars, submit, max_in_flight=max(2, workers * 2)
        ) as completed,
    ):
        for future, jar in completed:
            raise_if_cancelled()
            try:
                scan = future.result()
            except (TaskCancelled, BrokenProcessPool):
                raise
            except Exception as exc:  # noqa: BLE001 - worker 回報異常：該 JAR 視為失敗，其他照常
                log_warning(f"[IconPreview] {jar.name} 掃描失敗：{exc!r}")
                scan = JarScan(jar.name, failed=True)
            remaining.pop(jar, None)
            _replay_logs(scan.logs)
            yield scan
