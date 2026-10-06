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

import concurrent.futures
import contextlib
import contextvars
import inspect
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable

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

#: 後台記錄只在這段時間內能抵銷 UI 的同文字訊息。核心流程是「先 log、緊接著 yield 給 UI」，
#: 兩者相隔毫秒；設定時間窗是避免上一個任務留下的同文字後台記錄，誤抵銷這個任務
#: 真正只有 UI 的訊息。
_SEEN_WINDOW_SEC = 5.0


# 目前執行緒（context）正在執行的任務識別。服務入口設定一次（見 ``UISessionLogHandler.set_session``），
# 之後同一條執行緒寫出的後台記錄都屬於這個任務；追蹤器以它區分「同時執行、文字相同」的不同任務。
# 注意：``ThreadPoolExecutor`` 的工作執行緒不會繼承 context，在那裡寫的記錄任務為 ``None``，
# 這種記錄「無法判斷歸屬」，與任何任務都視為相容（退回只比文字＋時間窗的行為）。
_CURRENT_TASK: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "ui_mirror_current_task", default=None
)
# 任務的顯示名稱（只用在 app.log 的任務標籤，不參與歸屬比對）
_CURRENT_TASK_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ui_mirror_current_task_name", default=None
)


def set_current_task(task: object | None, name: str | None = None) -> None:
    """設定目前 context 的任務識別與顯示名稱（``None`` 代表沒有任務）。"""
    _CURRENT_TASK.set(task)
    _CURRENT_TASK_NAME.set(name if task is not None else None)


def current_task() -> object | None:
    return _CURRENT_TASK.get()


def current_task_name() -> str | None:
    return _CURRENT_TASK_NAME.get()


@contextlib.contextmanager
def task_scope(task: object | None, name: str | None = None):
    """暫時把目前 context 歸屬到某個任務（測試或不經 ``set_session`` 的呼叫端用）。"""
    token = _CURRENT_TASK.set(task)
    name_token = _CURRENT_TASK_NAME.set(name if task is not None else None)
    try:
        yield task
    finally:
        _CURRENT_TASK_NAME.reset(name_token)
        _CURRENT_TASK.reset(token)


def format_task_tag(task: object | None, name: str | None) -> str:
    """``app.log`` 每一行的任務標籤：``[task=名稱/識別] ``（沒有任務時是空字串）。"""
    if task is None:
        return ""
    label = " ".join(str(name).split()) if name else ""
    return f"[task={label}/{task}] " if label else f"[task={task}] "


def _install_record_factory() -> None:
    """安裝 LogRecord 工廠：每筆記錄建立當下（寫 log 的那條執行緒）帶上任務標籤。

    記錄在呼叫 ``logger.info(...)`` 的執行緒建立，所以讀得到那條執行緒的 ``contextvars``
    （服務入口設定的任務、執行緒池繼承的任務）；之後不論哪個 handler（檔案、終端機、UI）
    格式化它，標籤都已經在記錄上。冪等：已安裝就不重複包。
    """
    current = logging.getLogRecordFactory()
    if getattr(current, "_ui_mirror_task_tag", False):
        return

    def factory(*args, **kwargs):
        record = current(*args, **kwargs)
        task = _CURRENT_TASK.get()
        name = _CURRENT_TASK_NAME.get()
        # 沒有任務時三個欄位都是空字串（自訂格式用 %(task_name)s 等不會印出 "None"）
        record.task_id = "" if task is None else str(task)
        record.task_name = name or ""
        record.task_tag = format_task_tag(task, name)
        return record

    factory._ui_mirror_task_tag = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def install_task_record_factory() -> None:
    """公開入口（``setup_logging`` 與 ``ensure_tracker`` 會呼叫）。"""
    _install_record_factory()


def new_task_id(label: str = "ui") -> str:
    """產生一個唯一的任務識別（``<label>-<8 碼>``）。"""
    return f"{label}-{uuid.uuid4().hex[:8]}"


@contextlib.contextmanager
def new_task_scope(label: str = "ui", task: object | None = None):
    """為「沒有 ``TaskSession``、背景執行緒直接消費 generator」的工作建立一個唯一的任務歸屬。

    提取對話框、打包、QC 這類路徑沒有 session，但它們的核心流程與執行緒池寫出的後台記錄、
    以及同一條執行緒轉送給畫面的訊息，需要屬於同一個任務才分得出「同時執行的另一個任務」。
    ``task`` 可預先指定（呼叫端要在別的執行緒用同一個識別轉送訊息時，先 ``new_task_id()`` 存起來）。
    """
    with task_scope(task if task is not None else new_task_id(label), label) as scope:
        yield scope


def in_new_task(label: str, func: Callable, *, task: object | None = None) -> Callable:
    """回傳在「新任務歸屬」裡執行 ``func`` 的函式（給背景工作執行緒的 ``target`` 用）。"""

    def runner(*args, **kwargs):
        with new_task_scope(label, task):
            return func(*args, **kwargs)

    return runner


def run_in_context(func: Callable) -> Callable:
    """回傳在「呼叫當下 context 的副本」裡執行 ``func`` 的函式。

    給 ``threading.Thread(target=...)`` 用：新執行緒預設不繼承 ``contextvars``，
    包過之後它寫出的後台記錄才會帶著建立它的任務歸屬。
    """
    ctx = contextvars.copy_context()

    def runner(*args, **kwargs):
        return ctx.run(func, *args, **kwargs)

    return runner


class ContextThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """提交工作時帶著呼叫端的 ``contextvars``（任務歸屬）的 ``ThreadPoolExecutor``。

    標準的 ``ThreadPoolExecutor`` 工作執行緒不繼承 context，在池內寫出的後台記錄會失去
    任務歸屬（退回只比文字＋時間窗）。每次 ``submit`` 各自複製一份 context
    （同一個 ``Context`` 物件不能同時被兩條執行緒進入），``map`` 內部呼叫 ``submit``，所以同樣適用。
    專案內一律用它取代 ``ThreadPoolExecutor``（``tests/test_thread_context_contract.py`` 把關）。
    """

    def submit(self, fn, /, *args, **kwargs):
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)


def task_key(session: object) -> object:
    """session 的任務識別：優先用 ``task_id``，替身 session 退回物件 id。"""
    return getattr(session, "task_id", None) or id(session)


def _same_task(a: object | None, b: object | None) -> bool:
    """兩邊都知道歸屬且不同才算不同任務；任一邊未知（``None``）視為相容。"""
    return a is None or b is None or a == b


class _Seen:
    """一筆後台訊息行（occurrence 層級）；被抵銷或過期後標記，之後淘汰時不會再被誤算。"""

    __slots__ = ("consumed", "task", "text", "ts")

    def __init__(self, text: str, ts: float, task: object | None = None) -> None:
        self.text = text
        self.ts = ts
        self.task = task
        self.consumed = False


class _BackendSeenTracker(logging.Handler):
    """記住最近寫進後台的訊息行，讓鏡像時可略過「後台早就有」的那一份。

    資料結構以「每一筆 occurrence」為單位：

    - ``_entries``：依寫入順序的全部記錄，超過 ``limit`` 從最舊的淘汰。
    - ``_pending``：每個文字尚未被抵銷的記錄（FIFO）。抵銷時取該文字最舊的一筆。

    淘汰時直接丟掉那一筆具體的記錄；已抵銷／過期的記錄早已不在 ``_pending``，
    所以淘汰舊的已抵銷記錄不會影響之後新寫入的同文字記錄（過去用計數器猜
    occurrence 歸屬，超過 limit 後會把新的有效記錄一起忘掉）。
    """

    def __init__(
        self,
        limit: int = _SEEN_LIMIT,
        window_sec: float = _SEEN_WINDOW_SEC,
        clock=time.monotonic,
    ) -> None:
        super().__init__(level=logging.NOTSET)
        self._limit = limit
        self._window = window_sec
        self._clock = clock
        self._entries: deque[_Seen] = deque()
        self._pending: dict[str, deque[_Seen]] = {}
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, MIRROR_FLAG, False):
            return
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - handler 內不可再丟例外
            return
        self.remember(message, current_task())

    def remember(self, message: str, task: object | None = None) -> None:
        lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
        if not lines:
            return
        now = self._clock()
        with self._guard:
            for line in lines:
                entry = _Seen(line, now, task)
                self._entries.append(entry)
                self._pending.setdefault(line, deque()).append(entry)
            while len(self._entries) > self._limit:
                self._discard(self._entries.popleft())

    def _discard(self, entry: _Seen) -> None:
        """從待抵銷清單移除一筆（呼叫端須持有鎖）。已抵銷／過期的不在清單內。"""
        if entry.consumed:
            return
        entry.consumed = True
        queue = self._pending.get(entry.text)
        if queue:
            try:
                queue.remove(entry)
            except ValueError:
                pass
            if not queue:
                del self._pending[entry.text]

    def consume(self, line: str, task: object | None = None) -> bool:
        """若後台最近（時間窗內、同一任務或歸屬未知）寫過這一行，抵銷最舊的一筆並回傳 True。"""
        key = line.strip()
        if not key:
            return True
        now = self._clock()
        with self._guard:
            queue = self._pending.get(key)
            while queue and now - queue[0].ts > self._window:
                queue.popleft().consumed = True  # 過期：不能再抵銷
            if not queue:
                self._pending.pop(key, None)
                return False
            match = next((e for e in queue if _same_task(e.task, task)), None)
            if match is None:
                return False  # 有同文字，但屬於另一個同時執行的任務
            queue.remove(match)
            match.consumed = True
            if not queue:
                del self._pending[key]
            return True

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()
            self._pending.clear()


BACKEND_SEEN_TRACKER = _BackendSeenTracker()


def ensure_tracker() -> None:
    """確保追蹤器掛在 root logger（``setup_logging`` 會清掉 root handlers，所以每次都檢查）。"""
    _install_record_factory()  # 讓每筆後台記錄都帶任務標籤（見 RedactingFormatter）
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
    task: object | None = None,
    task_name: str | None = None,
) -> bool:
    """把一則 UI 訊息寫入後台 log；回傳是否真的寫入。

    Args:
        text: UI 上顯示的訊息（會先遮蔽機密）。
        level: UI 等級（debug/info/system/warning/error）。
        prefix: 只加在後台的前綴（例如任務名稱），UI 維持原文。
        logger: 指定後台 logger（預設 ``app.ui``）；模組自己的 logger 能讓 log 檔看出來源。
        dedupe: 後台最近已寫過的行不重複寫入。
        task: 這則訊息所屬的任務識別；沒給時用目前 context 的任務（見 ``set_current_task``）。
            去重只會被「同一任務或歸屬未知」的後台記錄抵銷，同時執行的另一個任務
            剛好寫出相同文字時不會互相吃掉。
        task_name: 該任務的顯示名稱（只用在 ``app.log`` 的任務標籤）。

    寫出這筆記錄時會暫時把 context 歸屬到 ``task``：不論從哪條執行緒呼叫（例如 UI 執行緒
    替某個背景任務補寫），``app.log`` 這一行的任務標籤都是**這則訊息所屬的任務**。
    """
    if not text:
        return False
    try:
        ensure_tracker()
        if task is None:
            task = current_task()
        body = redact_secrets(text)
        if dedupe:
            kept = [
                ln
                for ln in body.splitlines()
                if ln.strip() and not BACKEND_SEEN_TRACKER.consume(ln, task)
            ]
            if not kept:
                return False
            body = "\n".join(kept)
        target = logger or logging.getLogger(MIRROR_LOGGER_NAME)
        name = task_name if task_name is not None else current_task_name()
        with task_scope(task, name):
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


def accepted_params(func) -> set[str] | None:
    """函式可接受的關鍵字參數名稱；有 ``**kwargs`` 或無法檢查時回傳 ``None``（視為都接受）。"""
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return None
    return {
        p.name
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
