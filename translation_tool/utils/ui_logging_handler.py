import logging
from typing import Optional


class UISessionLogHandler(logging.Handler):
    """
    將 Python logging 訊息轉送到 TaskSession（UI）
    """

    def __init__(self):
        super().__init__()
        self._session = None

    def set_session(self, session):
        """
        動態綁定 TaskSession
        """
        self._session = session

    def emit(self, record: logging.LogRecord):
        if not self._session:
            return

        try:
            msg = self.format(record)

            # 統一 UI log 前綴
            if record.levelno >= logging.ERROR:
                ui_msg = f"[ERROR] {msg}"
            elif record.levelno >= logging.WARNING:
                ui_msg = f"[WARN] {msg}"
            else:
                ui_msg = f"[INFO] {msg}"

            self._session.add_log(ui_msg)

        except Exception:
            # logging handler 內部絕對不能炸
            pass
