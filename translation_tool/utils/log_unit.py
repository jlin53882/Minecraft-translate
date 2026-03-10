# translation_tool/utils/log_unit.py
# ------------------------------------------------------------
"""
模組開發說明 (Integrated Header)

1. 設計目的 (Core Objectives)
介面解耦：各模組無需直接操作 logging 函式庫，統一由 log_unit 入口管理。
環境安全：防止在 UI 或多執行緒環境中，因 Logger 尚未初始化而導致的執行錯誤。
原生支援：完整保留 logging 的標準格式化功能（例如：log_info("x=%s", x)）。

2. 核心技術實作 (Key Features)
動態 Logger 定位 (Dynamic Name)：
自動識別呼叫端模組。日誌輸出的 %(name)s 會顯示真正發出請求的模組名（如 core.translator），而非統一顯示 log_unit。
呼叫棧追蹤 (Stacklevel Correction)：
修正日誌輸出中的「檔名、行號、函式名」，確保其指向真正的業務代碼位置。
異常自動追蹤 (Advanced Exception Handling)：
log_exception() 封裝：自動擷取 Traceback 堆疊資訊，專門用於追蹤多執行緒環境下的 Crash 問題。

3. 工具函式優化 (Enhanced Utilities)
進度安全化 (progress)：
類型強制：自動轉換為 float。
邊界約束 (Clamp)：確保進度數值嚴格限制在 0.0 至 100.0 之間，防止 UI 進度條溢出。
"""
from __future__ import annotations

import logging
import time
import sys
from typing import Any, Dict

_THIS_MODULE = __name__
_CALLER_LOGGER_CACHE: Dict[object, str] = {}


def _get_caller_logger_name() -> str:
    """
    找到第一個不在 log_unit 模組內的呼叫 frame，回傳該模組 __name__ 作為 logger 名稱。
    目標：讓 %(name)s 顯示真正呼叫者模組。
    """
    try:
        f = sys._getframe(1)
        while f:
            mod = f.f_globals.get("__name__", "")
            if mod and mod != _THIS_MODULE:
                code = f.f_code
                cached = _CALLER_LOGGER_CACHE.get(code)
                if cached:
                    return cached
                _CALLER_LOGGER_CACHE[code] = mod
                return mod
            f = f.f_back
    except Exception:
        pass
    return _THIS_MODULE


def _log(level: int, msg: str, *args: Any, **kwargs: Any) -> None:
    """
    內部統一入口：
    - B：動態決定 logger（讓 %(name)s 正確）
    - A：stacklevel 修正檔名/行號/函式
    - 支援 logging 原生格式化參數 (msg + args)
    """
    try:
        caller_logger_name = _get_caller_logger_name()
        logger = logging.getLogger(caller_logger_name)

        # caller -> log_xxx -> _log -> logger.log  => stacklevel=3
        if "stacklevel" not in kwargs:
            kwargs["stacklevel"] = 3

        logger.log(level, msg, *args, **kwargs)
    except Exception:
        pass


def log_info(msg: str, *args: Any, **kwargs: Any) -> None:
    """INFO：一般流程資訊。"""
    _log(logging.INFO, msg, *args, **kwargs)


def log_warning(msg: str, *args: Any, **kwargs: Any) -> None:
    """WARNING：非致命但值得注意的狀態。"""
    _log(logging.WARNING, msg, *args, **kwargs)


def log_error(msg: str, *args: Any, **kwargs: Any) -> None:
    """ERROR：錯誤狀態或例外。"""
    _log(logging.ERROR, msg, *args, **kwargs)


def log_debug(msg: str, *args: Any, **kwargs: Any) -> None:
    """DEBUG：除錯資訊（通常只在 DEBUG level 顯示）。"""
    _log(logging.DEBUG, msg, *args, **kwargs)


def log_exception(msg: str, *args: Any, **kwargs: Any) -> None:
    """
    EXCEPTION：等同 logger.exception()，會自動帶 traceback。
    使用方式（一定要在 except 區塊內呼叫）：

        try:
            ...
        except Exception:
            log_exception("做某事失敗: path=%s", path)

    註：
    - 這個函數會用 ERROR 等級輸出（跟 logger.exception 一樣）
    - 仍然套用 A+B：%(name)s + 檔名/行號都會是呼叫者
    """
    try:
        caller_logger_name = _get_caller_logger_name()
        logger = logging.getLogger(caller_logger_name)

        # caller -> log_exception -> logger.exception  => stacklevel=2
        if "stacklevel" not in kwargs:
            kwargs["stacklevel"] = 2

        logger.exception(msg, *args, **kwargs)
    except Exception:
        pass


def progress(*args, session=None, p: float | None = None) -> None:
    """
    統一進度回報入口（給 UI / TaskSession 用）
    相容兩種呼叫：
      - progress(p)
      - progress(session, p)
    也支援：
      - progress(p=..., session=...)
    """
    # 兼容 positional
    if len(args) == 1:
        # progress(p)
        p = args[0]
    elif len(args) >= 2:
        # progress(session, p)
        session = args[0]
        p = args[1]

    if session is None or not hasattr(session, "set_progress"):
        return

    try:
        fp = float(p) if p is not None else 0.0
        if fp < 0.0:
            fp = 0.0
        elif fp > 1.0:
            fp = 1.0
        session.set_progress(fp)
    except Exception:
        pass



def get_formatted_duration(start_tick: float) -> str:
    """計算從 start_tick（perf_counter）到現在的耗時，回傳可讀字串。"""
    current_tick = time.perf_counter()
    duration = int(current_tick - start_tick)

    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours} 小時 {minutes} 分 {seconds} 秒"
    return f"{minutes} 分 {seconds} 秒"

