"""UI 日誌 → 後台日誌的鏡像（讓畫面上看得到的訊息，後台 log 檔也一定有）。

背景：UI（``TaskSession`` / ``LogView``）與後台（``logging``）原本是兩條獨立的路徑，
只寫進畫面的訊息不會出現在 log 檔，排查問題時就缺資料。這裡集中處理：

- ``mirror_to_backend()``：把一則 UI 訊息寫進後台 logger，並帶 ``ui_mirrored`` 標記，
  ``UISessionLogHandler`` 看到標記就略過，不會再回灌 UI 造成畫面重複。
- 後台已記錄過的訊息不重複寫：核心流程常常「自己先 ``log_info(msg)`` 再 ``yield {"log": msg}``」，
  這時 UI 轉送的那一份不該讓後台出現兩次。``_BackendSeenTracker`` 掛在 root logger，
  記住最近寫過的後台訊息行；鏡像時每個已見過的行只抵銷一次，所以相同文字連續出現兩次
  （一次後台、一次只有 UI）仍會正確補寫一筆。
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, deque
from collections.abc import Iterable

from translation_tool.utils.redaction import redact_secrets

#: 鏡像訊息預設使用的後台 logger 名稱（呼叫端沒有自己的 logger 時使用）
MIRROR_LOGGER_NAME = "app.ui"

#: 標記鏡像記錄的 ``LogRecord`` 屬性名稱；``UISessionLogHandler`` 看到就略過
MIRROR_FLAG = "ui_mirrored"

#: 來源是後台 logger 本身（已經在後台）的 ``source`` 值，不需要再鏡像
BACKEND_SOURCES = frozenset({"logger", "backend"})

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "system": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_SEEN_LIMIT = 1000


class _BackendSeenTracker(logging.Handler):
    """記住最近寫進後台的訊息行，讓鏡像時可略過「後台早就有」的那一份。"""

    def __init__(self, limit: int = _SEEN_LIMIT) -> None:
        super().__init__(level=logging.NOTSET)
        self._limit = limit
        self._order: deque[str] = deque()
        self._counts: Counter[str] = Counter()
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, MIRROR_FLAG, False):
            return
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - handler 內不可再丟例外
            return
        self.remember(message)

    def remember(self, message: str) -> None:
        lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
        if not lines:
            return
        with self._guard:
            for line in lines:
                self._order.append(line)
                self._counts[line] += 1
            while len(self._order) > self._limit:
                oldest = self._order.popleft()
                if self._counts.get(oldest, 0) > 0:
                    self._counts[oldest] -= 1
                    if self._counts[oldest] <= 0:
                        del self._counts[oldest]

    def consume(self, line: str) -> bool:
        """若後台最近寫過這一行，抵銷一次並回傳 True。"""
        key = line.strip()
        if not key:
            return True
        with self._guard:
            if self._counts.get(key, 0) > 0:
                self._counts[key] -= 1
                if self._counts[key] <= 0:
                    del self._counts[key]
                return True
        return False

    def clear(self) -> None:
        with self._guard:
            self._order.clear()
            self._counts.clear()


BACKEND_SEEN_TRACKER = _BackendSeenTracker()


def ensure_tracker() -> None:
    """確保追蹤器掛在 root logger（``setup_logging`` 會清掉 root handlers，所以每次都檢查）。"""
    root = logging.getLogger()
    if BACKEND_SEEN_TRACKER not in root.handlers:
        root.addHandler(BACKEND_SEEN_TRACKER)


def mirror_to_backend(
    text: str,
    level: str = "info",
    *,
    prefix: str = "",
    logger: logging.Logger | None = None,
    dedupe: bool = True,
) -> bool:
    """把一則 UI 訊息寫入後台 log；回傳是否真的寫入。

    Args:
        text: UI 上顯示的訊息（會先遮蔽機密）。
        level: UI 等級（debug/info/system/warning/error）。
        prefix: 只加在後台的前綴（例如任務名稱），UI 維持原文。
        logger: 指定後台 logger（預設 ``app.ui``）；模組自己的 logger 能讓 log 檔看出來源。
        dedupe: 後台最近已寫過的行不重複寫入。
    """
    if not text:
        return False
    try:
        ensure_tracker()
        body = redact_secrets(text)
        if dedupe:
            kept = [
                ln
                for ln in body.splitlines()
                if ln.strip() and not BACKEND_SEEN_TRACKER.consume(ln)
            ]
            if not kept:
                return False
            body = "\n".join(kept)
        target = logger or logging.getLogger(MIRROR_LOGGER_NAME)
        target.log(
            _LEVELS.get(str(level).lower(), logging.INFO),
            "%s%s",
            prefix,
            body,
            extra={MIRROR_FLAG: True},
        )
        return True
    except Exception:  # noqa: BLE001 - 鏡像失敗不可影響 UI 或任務本身
        return False


def mirror_lines(
    lines: Iterable[tuple[str, str]],
    *,
    prefix: str = "",
    logger: logging.Logger | None = None,
) -> None:
    """批次鏡像 ``(文字, 等級)``；給「背景執行緒累積 UI 行、再批次推畫面」的路徑使用。"""
    for text, level in lines:
        mirror_to_backend(text, level, prefix=prefix, logger=logger)
