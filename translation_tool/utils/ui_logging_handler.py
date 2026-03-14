"""translation_tool/utils/ui_logging_handler.py 模組。

用途：提供本檔案定義的功能與流程，供專案其他模組呼叫。
維護注意：本檔案的函式 docstring 用於維護說明，不代表行為變更。
"""

import logging

class UISessionLogHandler(logging.Handler):
    """
    將 Python logging 訊息轉送到 TaskSession（UI）
    """

    def __init__(self):
        """初始化 UISessionLogHandler。"""
        super().__init__()
        self._session = None

    def set_session(self, session):
        """動態綁定 TaskSession。"""
        self._session = session

    def emit(self, record: logging.LogRecord):
        """發送日誌記錄到 UI。"""
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
