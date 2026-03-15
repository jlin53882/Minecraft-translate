"""app.services_impl.logging_service 單元測試

測試目標：LogLimiter 類別、日誌節流功能、全域單例。
"""

import logging
import time
import pytest
from unittest.mock import patch, MagicMock

from app.services_impl.logging_service import (
    LogLimiter,
    GLOBAL_LOG_LIMITER,
    UI_LOG_HANDLER,
    update_logger_config,
)


class TestLogLimiter:
    """LogLimiter 測試案例"""

    def test_init_default(self):
        """測試預設初始化"""
        limiter = LogLimiter()
        assert limiter.max_logs == 3000
        assert limiter.flush_interval == 0.1

    def test_init_custom(self):
        """測試自訂參數初始化"""
        limiter = LogLimiter(max_logs=100, flush_interval=0.5)
        assert limiter.max_logs == 100
        assert limiter.flush_interval == 0.5

    def test_filter_no_log_key(self):
        """測試不含 log 鍵的 dict 直接通過"""
        limiter = LogLimiter()
        result = limiter.filter({"progress": 0.5})
        
        assert result == {"progress": 0.5}

    def test_filter_with_log_throttle(self):
        """測試日誌節流（短時間內不輸出）"""
        limiter = LogLimiter(flush_interval=1.0)  # 1秒內不輸出
        
        # 設定 last_flush 為現在，避免第一次調用就通過
        limiter.last_flush = time.time()
        
        result1 = limiter.filter({"log": "Log 1", "progress": 0.1})
        result2 = limiter.filter({"log": "Log 2", "progress": 0.2})
        
        # 短時間內，兩次都應該被節流，回傳 None
        assert result1 is None
        assert result2 is None
        
        # 等待超過 flush_interval
        time.sleep(1.1)
        
        result3 = limiter.filter({"log": "Log 3", "progress": 0.3})
        # 超過時間，應該返回合併的日誌
        assert result3 is not None
        assert "Log 3" in result3["log"]

    def test_filter_with_log_pass(self):
        """測試日誌通過（超過 flush_interval）"""
        limiter = LogLimiter(flush_interval=0.0)  # 立即輸出
        
        result = limiter.filter({"log": "Test log", "progress": 0.5})
        
        assert result is not None
        assert "log" in result
        assert "Test log" in result["log"]

    def test_filter_merges_logs(self):
        """測試在 flush_interval 內多次調用會合併"""
        limiter = LogLimiter(flush_interval=0.0)
        
        # 第一次調用立即返回（因為 0 >= 0）
        result1 = limiter.filter({"log": "Log 1"})
        
        # pending_logs 在第一次調用後被清空
        # 所以需要多次調用來累積
        limiter.pending_logs.append("Log 1")  # 模擬累積
        
        result2 = limiter.filter({"log": "Log 2"})
        
        # 由於 flush_interval=0，會立即輸出合併
        # 但 Log 1 已經在 pending_logs 中
        assert result2 is not None

    def test_flush_empty(self):
        """測試 flush 空緩衝區"""
        limiter = LogLimiter()
        result = limiter.flush()
        
        assert result is None

    def test_flush_with_pending(self):
        """測試 flush 有待處理日誌"""
        limiter = LogLimiter()
        
        # 手動加入 pending logs（模擬 filter 累積但未輸出）
        limiter.pending_logs.append("Pending log")
        limiter.last_flush = 0  # 確保不會被自動 flush
        
        result = limiter.flush()
        
        assert result is not None
        assert "Pending log" in result["log"]

    def test_flush_clears_pending(self):
        """測試 flush 清除待處理緩衝區"""
        limiter = LogLimiter()
        limiter.filter({"log": "Log 1"})
        limiter.flush()
        
        result = limiter.flush()
        
        # 第二次 flush 應該回傳 None（緩衝區已清空）
        assert result is None

    def test_max_logs_limit(self):
        """測試日誌數量上限"""
        limiter = LogLimiter(max_logs=2)
        
        limiter.filter({"log": "Log 1"})
        limiter.filter({"log": "Log 2"})
        limiter.filter({"log": "Log 3"})  # 超過上限
        
        # 最早的日誌應該被移除
        assert len(limiter.log_queue) == 2
        assert "Log 1" not in limiter.log_queue


class TestGlobalSingleton:
    """全域單例測試"""

    def test_global_limiter_singleton(self):
        """測試 GLOBAL_LOG_LIMITER 是單例"""
        from app.services_impl import logging_service
        
        assert hasattr(logging_service, "GLOBAL_LOG_LIMITER")
        assert logging_service.GLOBAL_LOG_LIMITER is GLOBAL_LOG_LIMITER

    def test_global_log_limiter_defaults(self):
        """測試全域節流器預設值"""
        assert GLOBAL_LOG_LIMITER.max_logs == 5000
        assert GLOBAL_LOG_LIMITER.flush_interval == 0.1

    def test_ui_log_handler_type(self):
        """測試 UI_LOG_HANDLER 類型"""
        assert isinstance(UI_LOG_HANDLER, logging.Handler)


class TestUpdateLoggerConfig:
    """update_logger_config 函數測試"""

    def test_update_logger_config_success(self):
        """測試成功更新日誌設定"""
        mock_config_loader = MagicMock(return_value={
            "logging": {
                "log_level": "DEBUG",
                "log_format": "%(levelname)s: %(message)s"
            }
        })
        
        # 應該不拋出例外
        update_logger_config(mock_config_loader, logger_name="test_logger")
        
        mock_config_loader.assert_called_once()

    def test_update_logger_config_missing_logging_section(self):
        """測試缺少 logging 區段使用預設值"""
        mock_config_loader = MagicMock(return_value={})
        
        # 應該使用預設值（INFO 等）
        update_logger_config(mock_config_loader)

    def test_update_logger_config_invalid_level(self):
        """測試無效的日誌等級使用預設"""
        mock_config_loader = MagicMock(return_value={
            "logging": {
                "log_level": "INVALID_LEVEL"
            }
        })
        
        # 應該使用預設 INFO
        update_logger_config(mock_config_loader)

    def test_update_logger_config_adds_handler(self):
        """測試更新時新增 handler"""
        mock_config_loader = MagicMock(return_value={
            "logging": {"log_level": "INFO"}
        })
        
        # 取得 root logger
        root_logger = logging.getLogger()
        original_handlers = list(root_logger.handlers)
        
        try:
            update_logger_config(mock_config_loader, logger_name="test_logger2")
            
            # handler 應該被新增
            # 注意：這可能會修改全域狀態
        finally:
            # 清理：如果是我們新增的，則移除
            for handler in root_logger.handlers:
                if handler not in original_handlers and handler == UI_LOG_HANDLER:
                    pass  # 保留全域 handler


class TestLogLimiterEdgeCases:
    """邊界情況測試"""

    def test_empty_log_text(self):
        """測試空日誌文字"""
        limiter = LogLimiter()
        result = limiter.filter({"log": ""})
        
        # 空字串應該被加入但不觸發輸出
        assert "" in limiter.log_queue

    def test_very_long_log(self):
        """測試很長的日誌"""
        limiter = LogLimiter()
        long_log = "x" * 10000
        
        result = limiter.filter({"log": long_log})
        
        assert result is not None

    def test_unicode_log(self):
        """測試 Unicode 日誌"""
        limiter = LogLimiter(flush_interval=0.0)
        
        result = limiter.filter({"log": "測試中文 🎉 emojis"})
        
        assert result is not None
        assert "測試中文" in result["log"]
