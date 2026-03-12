"""app.services_impl.logging_service

本模組承接 `app/services.py` 原本的 logging / 節流 / handler 綁定相關元件。

PR14 範圍（只抽離 shared 元件，不改 service/pipeline 的流程）：
- `LogLimiter`
- `GLOBAL_LOG_LIMITER`
- `UI_LOG_HANDLER`
- `update_logger_config()`

維護注意：
- `UI_LOG_HANDLER` 與 `GLOBAL_LOG_LIMITER` 都是 module-level 單例；
  `app.services` 必須 re-export 這些物件，確保外部拿到的是同一個 instance。
- 本模組不應 import views 或 pipelines，避免 circular import。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict

from translation_tool.utils.ui_logging_handler import UISessionLogHandler

logger = logging.getLogger(__name__)


class LogLimiter:
    """Log 節流器（UI 友善）。

    目的：核心流程可能在短時間噴出大量 log；若逐筆推進 UI，會造成明顯卡頓。

    做法：
    - 用 queue 保留近期 log（避免無限制成長）。
    - 用 pending buffer 把多筆 log 合併成一筆，降低 UI 重繪頻率。

    注意：
    - `filter()` 可能回傳 None，代表本輪不更新 UI。
    - `flush()` 只負責把 pending 合併輸出，不保證與任務結束同步；呼叫端需視流程決定何時 flush。
    """

    def __init__(self, max_logs: int = 3000, flush_interval: float = 0.1):
        """處理此函式的工作（細節以程式碼為準）。
        
        - 主要包裝：`deque`
        
        回傳：None
        """
        self.max_logs = max_logs
        self.flush_interval = flush_interval
        self.log_queue = deque(maxlen=max_logs)
        self.pending_logs: list[str] = []
        self.last_flush = 0.0

    def filter(self, update_dict: Dict[str, Any]):
        """批次合併 log + 限制輸出頻率。

        回傳：
        - dict：代表可以更新 UI（可能是合併後的 log）
        - None：代表本輪不更新 UI（節流）
        """
        if "log" not in update_dict:
            return update_dict

        log_text = update_dict["log"]
        self.log_queue.append(log_text)
        self.pending_logs.append(log_text)

        now = time.time()
        if now - self.last_flush < self.flush_interval:
            return None

        self.last_flush = now
        merged = "\n".join(self.pending_logs)
        self.pending_logs.clear()
        return {"log": merged, "progress": update_dict.get("progress")}

    def flush(self):
        """強制輸出尚未送出的 pending logs。"""
        if not self.pending_logs:
            return None

        merged = "\n".join(self.pending_logs)
        self.pending_logs.clear()
        self.last_flush = time.time()
        return {"log": merged}


# 單例：全域節流器（維持與 services.py 過去行為一致）
GLOBAL_LOG_LIMITER = LogLimiter(max_logs=5000, flush_interval=0.1)


# 單例：UI log handler（維持與 services.py 過去行為一致）
UI_LOG_HANDLER = UISessionLogHandler()
UI_LOG_HANDLER.setLevel(logging.INFO)
UI_LOG_HANDLER.setFormatter(logging.Formatter("%(message)s"))


def update_logger_config(config_loader, *, logger_name: str = "translation_tool"):
    """重新讀取 config 並套用最新的 Log 等級。

    PR14 設計重點：此函式行為需保持與舊版 services.py 一致。

    參數：
    - config_loader：可呼叫物件，回傳 config dict（由 app/services.py 傳入 `_load_app_config`）
    - logger_name：目標 logger 名稱（預設 translation_tool）

    注意：
    - handler 掛載於 root logger，並以 `if UI_LOG_HANDLER not in root_logger.handlers` 保持 idempotent。
    - 此函式只負責 logging 設定；session 綁定/解除仍由各 service 入口處理（避免 PR14 scope 膨脹）。
    """

    _config = config_loader()
    _log_cfg = _config.get("logging", {})

    _level_name = _log_cfg.get("log_level", "INFO").upper()
    _numeric_level = getattr(logging, _level_name, logging.INFO)
    _format_str = _log_cfg.get("log_format", "%(message)s")

    root_logger = logging.getLogger()
    target_logger = logging.getLogger(logger_name)

    if UI_LOG_HANDLER not in root_logger.handlers:
        root_logger.addHandler(UI_LOG_HANDLER)

    target_logger.setLevel(_numeric_level)
    target_logger.propagate = True

    root_logger.setLevel(_numeric_level)
    UI_LOG_HANDLER.setLevel(_numeric_level)
    UI_LOG_HANDLER.setFormatter(logging.Formatter(_format_str))

    logger.debug(f"Log 系統已同步：Level={_level_name}, Format={_format_str}")
