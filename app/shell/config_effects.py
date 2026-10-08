"""設定存檔後需要「做事」的副作用（#117）。

目前只有一項：``translator.cache_directory`` 變更時重載翻譯快取與搜尋索引。

規則（與 PR #143 的「現行任務不可中途換快取根目錄」契約相容）：
- 沒有任務進行中：存檔後立即在背景執行緒重載，完成後通知畫面刷新。
- 有任務進行中：不動；等所有任務結束後才重載（避免任務寫入新舊兩個資料夾）。
- 重載期間又有新的變更：重載結束後再跑一次。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)

CACHE_DIRECTORY_PATH = "translator.cache_directory"


def _start_daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="cache-root-reload", daemon=True).start()


class CacheRootReloader:
    """依設定變更與任務閒置狀態，決定何時重載快取。"""

    def __init__(
        self,
        *,
        is_busy: Callable[[], bool],
        reload: Callable[[], object],
        on_reloaded: Callable[[bool], None] | None = None,
        start: Callable[[Callable[[], None]], None] = _start_daemon_thread,
    ) -> None:
        self._is_busy = is_busy
        self._reload = reload
        self._on_reloaded = on_reloaded
        self._start = start
        self._lock = threading.Lock()
        self._pending = False
        self._running = False

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending

    def on_config_paths(self, changed_paths: Iterable[str]) -> None:
        """設定存檔事件（可能在任何執行緒）。"""
        if CACHE_DIRECTORY_PATH not in set(changed_paths):
            return
        with self._lock:
            self._pending = True
        self.poke()

    def poke(self) -> None:
        """任務狀態改變時呼叫：若有待處理的重載且已經閒置，就開始重載。"""
        with self._lock:
            if not self._pending or self._running:
                return
        try:
            busy = self._is_busy()
        except Exception:
            log.debug("讀取任務狀態失敗，稍後再試", exc_info=True)
            return
        if busy:
            return
        with self._lock:
            if not self._pending or self._running:
                return
            self._pending = False
            self._running = True
        try:
            self._start(self._run)
        except Exception:
            with self._lock:
                self._pending = True
                self._running = False
            log.warning("快取重載工作未能取得操作 owner，將等待後續重試", exc_info=True)

    def _run(self) -> None:
        ok = False
        try:
            self._reload()
            ok = True
        except Exception:
            log.warning("快取資料夾變更後重載失敗", exc_info=True)
        finally:
            with self._lock:
                self._running = False
        if self._on_reloaded is not None:
            try:
                self._on_reloaded(ok)
            except Exception:
                log.debug("快取重載後的畫面刷新失敗", exc_info=True)
        self.poke()  # 重載期間若又有變更，這裡會接著處理
