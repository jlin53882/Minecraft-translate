"""translation_tool/utils/ui_logging_handler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
from typing import Any

from translation_tool.utils.redaction import redact_secrets
from translation_tool.utils.ui_mirror import (
    MIRROR_FLAG,
    accepted_params,
    set_current_task,
    task_key,
)


class UISessionLogHandler(logging.Handler):
    """將 Python logging 訊息轉送到 TaskSession（UI）。"""

    def __init__(self) -> None:
        """初始化 UISessionLogHandler。"""
        super().__init__()
        self._session: Any = None

    def set_session(self, session: Any) -> None:
        """動態綁定 TaskSession，並把目前 context（執行緒）歸屬到該任務。

        服務在背景執行緒的入口呼叫它，之後這條執行緒寫出的後台記錄都帶這個任務識別，
        UI→後台鏡像去重才分得出「同時執行的不同任務、相同文字」。
        """
        self._session = session
        set_current_task(task_key(session) if session is not None else None)

    def emit(self, record: logging.LogRecord) -> None:
        """發送日誌記錄到 UI。

        UI 只顯示訊息本身（不套用檔案 log 的時間戳 / 模組格式），
        並把 logging 等級對應到 TaskSession 的 level，讓「只看警告以上」篩選有效。
        """
        if not self._session:
            return
        # 已由呼叫端直接寫入 session 的訊息（只補寫後台 log 用）不重複送進 UI
        if getattr(record, MIRROR_FLAG, False):
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
            accepted = accepted_params(self._session.add_log)
            kwargs = {}
            if accepted is None or "level" in accepted:
                kwargs["level"] = level
            if accepted is None or "source" in accepted:
                kwargs["source"] = "logger"
            self._session.add_log(ui_msg, **kwargs)

        except Exception:  # noqa: BLE001, S110 - 在 handler 內記錄錯誤會遞迴
            # logging handler 內部絕對不能炸
            pass
