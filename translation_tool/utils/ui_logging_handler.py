"""translation_tool/utils/ui_logging_handler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
import threading
from typing import Any

from translation_tool.utils.redaction import redact_secrets
from translation_tool.utils.ui_mirror import (
    MIRROR_FLAG,
    accepted_params,
    current_task,
    set_current_task,
    task_key,
)


class UISessionLogHandler(logging.Handler):
    """將 Python logging 訊息轉送到 TaskSession（UI）。

    同時執行多個任務時，每筆後台記錄依「寫出它的執行緒屬於哪個任務」
    （``ui_mirror.current_task()``）送進**該任務自己**的 session：

    - 記錄的任務已登記 → 送到該任務的 session。
    - 記錄沒有任務歸屬（``None``：UI 執行緒本身、不經專案 helper 的第三方執行緒）
      → 退回「最近綁定的 session」（與過去單一全域 session 的行為一致）。
    - 記錄的任務已知但沒有登記（例如已結束的任務留在池裡的延遲記錄、不掛 session 的 UI 工作
      執行緒）→ 不送，避免顯示在不相干任務的畫面上。
    """

    def __init__(self) -> None:
        """初始化 UISessionLogHandler。"""
        super().__init__()
        self._sessions: dict[Any, Any] = {}  # 任務識別 -> session（依綁定順序）
        self._latest: Any = None  # 最近綁定、尚未解除的 session
        self._registry_lock = threading.RLock()

    @property
    def _session(self) -> Any:
        """目前「最近綁定的 session」（相容舊的單一 session 介面）。"""
        return self._latest

    def set_session(self, session: Any) -> None:
        """綁定（或解除）TaskSession，並把目前 context（執行緒）歸屬到該任務。

        服務在背景執行緒的入口呼叫它：之後這條執行緒（以及繼承 context 的執行緒池）寫出的
        後台記錄都帶這個任務識別——UI→後台鏡像去重分得出「同時執行的不同任務」，
        後台→UI 轉送也依它送進正確任務的畫面。

        傳入 ``None`` 解除目前任務的綁定；若呼叫的執行緒沒有任務歸屬（例如 generator 在別條
        執行緒被關閉），無法知道要解除誰，只清掉已 ``finish()`` 的綁定。
        """
        with self._registry_lock:
            if session is not None:
                key = task_key(session)
                self._sessions.pop(key, None)  # 重新綁定移到最後
                self._sessions[key] = session
                self._latest = session
                set_current_task(key, getattr(session, "name", None))
                return
            current = current_task()
            if current is not None:
                self._sessions.pop(current, None)
            for key, bound in list(self._sessions.items()):
                # 以真正的 lifecycle（finish 過）判斷，不能看 status：``set_error()`` 只是標記失敗，
                # 服務通常在 finally 才 finish()，ERROR 但尚未 finish 的任務後面還會有記錄。
                # ``is True``：替身（MagicMock）的屬性是真值物件，不能當成已結束。
                if getattr(bound, "is_finished", False) is True:
                    del self._sessions[key]
            self._latest = next(reversed(self._sessions.values()), None)
        set_current_task(None)

    def clear(self) -> None:
        """清掉所有綁定（測試用）。"""
        with self._registry_lock:
            self._sessions.clear()
            self._latest = None
        set_current_task(None)

    def _target_session(self) -> Any:
        task = current_task()
        with self._registry_lock:
            if task is None:
                return self._latest
            return self._sessions.get(task)

    def emit(self, record: logging.LogRecord) -> None:
        """發送日誌記錄到 UI。

        UI 只顯示訊息本身（不套用檔案 log 的時間戳 / 模組格式），
        並把 logging 等級對應到 TaskSession 的 level，讓「只看警告以上」篩選有效。
        """
        # 已由呼叫端直接寫入 session 的訊息（只補寫後台 log 用）不重複送進 UI
        if getattr(record, MIRROR_FLAG, False):
            return
        session = self._target_session()
        if not session:
            return

        try:
            msg: str = redact_secrets(record.getMessage())
            if record.levelno >= logging.ERROR:
                level, ui_msg = "error", f"[ERROR] {msg}"
            elif record.levelno >= logging.WARNING:
                level, ui_msg = "warning", f"[WARN] {msg}"
            elif record.levelno >= logging.INFO:
                level, ui_msg = "info", msg
            else:
                level, ui_msg = "debug", msg

            # 舊版 / 替身 session 只接受 text：以函式簽章判斷，不靠捕捉 TypeError
            # （那會把 add_log 內部真正的 TypeError 也當成「不支援」而重複呼叫）。
            accepted = accepted_params(session.add_log)
            kwargs = {}
            if accepted is None or "level" in accepted:
                kwargs["level"] = level
            if accepted is None or "source" in accepted:
                kwargs["source"] = "logger"
            session.add_log(ui_msg, **kwargs)

        except Exception:  # noqa: BLE001, S110 - 在 handler 內記錄錯誤會遞迴
            # logging handler 內部絕對不能炸
            pass
