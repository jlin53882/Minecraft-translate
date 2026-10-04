"""translation_tool/utils/ui_logging_handler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging
from typing import Any

from translation_tool.utils.redaction import redact_secrets


class UISessionLogHandler(logging.Handler):
    """將 Python logging 訊息轉送到 TaskSession（UI）。"""

    def __init__(self) -> None:
        """初始化 UISessionLogHandler。"""
        super().__init__()
        self._session: Any = None

    def set_session(self, session: Any) -> None:
        """動態綁定 TaskSession。"""
        self._session = session

    def emit(self, record: logging.LogRecord) -> None:
        """發送日誌記錄到 UI。

        UI 只顯示訊息本身（不套用檔案 log 的時間戳 / 模組格式），
        並把 logging 等級對應到 TaskSession 的 level，讓「只看警告以上」篩選有效。
        """
        if not self._session:
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

            try:
                self._session.add_log(ui_msg, level=level, source="logger")
            except TypeError:
                # 舊版 session 只接受 text
                self._session.add_log(ui_msg)

        except Exception:  # noqa: BLE001, S110 - 在 handler 內記錄錯誤會遞迴
            # logging handler 內部絕對不能炸
            pass
